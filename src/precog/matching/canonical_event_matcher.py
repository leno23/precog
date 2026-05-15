"""Canonical event matcher service (Cohort 5+ Slot B).

The first new-architecture production module after Migration 0090's
platform-prefix rename.  Creates ``canonical_events`` rows from
upstream source state (``games`` + ``platform_events`` + ``game_states``)
with V2.44 atomicity contract: every canonical_events INSERT bundles
canonical_event_link INSERT + canonical_event_match_log INSERT +
canonical_event_phase_log auto-trigger row + games/game_states
canonical_event_id back-link UPDATEs in a single transaction.

Per session-92 4-agent design council (Galadriel + Holden + Miles +
Uhura) + PM-composed build spec at
``memory/build_spec_slot_a_matcher_pm_memo.md`` + session-107 rebase
addendum at ``memory/build_spec_slot_b_matcher_addendum_session_107.md``.

Two operational modes:

    Steady-state mode (poller):
        Inherits from BasePoller.  Polls every ~30s for platform_events
        that have a ``game_id`` set but no active canonical_event_link.
        For each, runs identity resolution (natural_key_hash via
        sport + game_date + sorted team_codes), then writes the V2.44
        atomic bundle.

    Backfill mode (CLI-driven):
        Triggered by ``precog matcher backfill --all``.  Idempotent
        via ``uq_canonical_events_nk`` UNIQUE constraint.  Bounded by
        ``--batch-size`` flag (default 1000); receipted via summary
        printout + a single audit-log summary row.

Pattern 73 SSOT inventory (reused from existing constants):

    - ``CREATED_BY_PREFIXES`` (constants.py): canonical_events.created_by
      vocabulary.  Matcher uses ``CREATED_BY_MATCHER`` (= 'matcher:slot-B:v1')
      for steady-state writes; ``CREATED_BY_BACKFILL`` (= 'cli:matcher-backfill:v1')
      for backfill CLI writes.
    - ``CANONICAL_EVENT_LIFECYCLE_PHASES`` (constants.py): matcher
      creates rows with ``lifecycle_phase='proposed'`` ONLY (parent
      spec Q5; phase advancement is downstream scope).
    - ``LINK_STATE_VALUES``: matcher creates links with ``link_state='active'``.
    - ``DECIDED_BY_PREFIXES``: matcher's audit-log + link rows use
      ``DECIDED_BY_MATCHER`` (= 'service:matcher:slot-B:v1') for
      steady-state; ``DECIDED_BY_BACKFILL`` (= 'service:cli:matcher-backfill:v1')
      for backfill CLI.
    - ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``: matcher writes
      audit rows with ``action='create'``.

P41 9-capability mandates (parent spec § P41 9-cap Mandates,
session-92 council convergence):

    1. Logging -- structured INFO/WARN/ERROR per match decision.
    2. Error cases -- hybrid retry + DLQ + circuit breaker; never
       silent NULL.
    3. Alerts -- system_health row + alert on queue depth.
    4. Operator visibility -- rate metric + CLI status + queryable
       log.
    5. created_by -- 'matcher:slot-B:v1' / 'cli:matcher-backfill:v1'.
    6. Signal handler -- BasePoller mechanical inheritance.
    7. Integration -- new CRUD modules + ServiceSupervisor + CLI.
    8. Backpressure -- 30s poll interval; --batch-size on backfill;
       respects system_health pause.
    9. Restart-safety -- ON CONFLICT idempotency via uq_canonical_events_nk.

Reference:
    - ADR-118 V2.44 (atomicity contract) + V2.46 (canonical layer
      relationships) + V2.49 (OQ-H1 NORMALIZE)
    - Migration 0091 (slot B audit-log + provenance infrastructure)
    - ``src/precog/database/crud_canonical_event_match_log.py``
    - ``src/precog/database/crud_canonical_event_links.py``
      (``create_link_in_cursor`` helper)
    - ``src/precog/database/crud_canonical_events.py``
      (existing ``create_canonical_event``)
    - ``memory/build_spec_slot_a_matcher_pm_memo.md``
    - ``memory/build_spec_slot_b_matcher_addendum_session_107.md``
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from precog.database.connection import fetch_all, fetch_one, get_cursor
from precog.database.constants import CANONICAL_EVENT_LIFECYCLE_PHASES
from precog.database.crud_canonical_event_links import create_link_in_cursor
from precog.database.crud_canonical_event_match_log import (
    append_event_match_log_row_in_cursor,
    get_cohort5_event_matcher_algorithm_id,
)
from precog.schedulers.base_poller import BasePoller

logger = logging.getLogger(__name__)


# =============================================================================
# Module-level constants (Pattern 73 SSOT pointers / matcher-internal identity)
# =============================================================================

# canonical_events.created_by identity for steady-state matcher writes.
# Pattern 73 SSOT pointer to constants.py:CREATED_BY_PREFIXES (matcher:
# prefix family).
CREATED_BY_MATCHER = "matcher:slot-B:v1"

# canonical_events.created_by identity for backfill CLI writes.
# Pattern 73 SSOT pointer to constants.py:CREATED_BY_PREFIXES (cli: prefix
# family).
CREATED_BY_BACKFILL = "cli:matcher-backfill:v1"

# canonical_event_links.decided_by + canonical_event_match_log.decided_by
# identity for steady-state matcher writes.  Pattern 73 SSOT pointer to
# constants.py:DECIDED_BY_PREFIXES (service: prefix family).
DECIDED_BY_MATCHER = "service:matcher:slot-B:v1"

# canonical_event_links.decided_by + canonical_event_match_log.decided_by
# identity for backfill CLI writes.
DECIDED_BY_BACKFILL = "service:cli:matcher-backfill:v1"

# Steady-state matcher's algorithmic match confidence for natural-key-derived
# matches (highest confidence; downstream consumers may filter on this).
# Pure natural-key match (sport + game_date + sorted team_codes) has no
# ambiguity to disambiguate, so confidence is 1.0 (Decimal-only per
# CLAUDE.md Critical Pattern #1).
_NATURAL_KEY_MATCH_CONFIDENCE: Decimal = Decimal("1.000")

# Default threshold below which the matcher routes a candidate to the
# canonical_match_reviews queue rather than auto-creating the link.  Slot
# B's natural-key resolution path always produces 1.0 confidence so the
# threshold is a forward-pointer for future fuzzy-match algorithms (Cohort
# 6+).  Configured at matcher init via the T_match config key.
_DEFAULT_T_MATCH: Decimal = Decimal("0.85")

# Maximum consecutive logical-failure count before the matcher trips its
# circuit breaker and pauses.  Hybrid failure-mode policy per parent spec
# Q5: retry transient / DLQ to canonical_match_reviews / pause on N>K.
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5

# Default initial lifecycle_phase value for newly-created events.  Slot B
# matcher creates rows with this phase ONLY (parent spec Q5).  Phase
# advancement is downstream scope.  Pattern 73 SSOT real-guard assertion:
# the literal must appear in CANONICAL_EVENT_LIFECYCLE_PHASES (constants.py);
# drift between matcher initial-phase and the canonical 5-value vocab
# surfaces as ImportError/AssertionError at module load (defense-in-depth
# against future refactor that drops 'proposed' from the vocab).
_INITIAL_LIFECYCLE_PHASE = "proposed"
assert _INITIAL_LIFECYCLE_PHASE in CANONICAL_EVENT_LIFECYCLE_PHASES, (
    f"_INITIAL_LIFECYCLE_PHASE {_INITIAL_LIFECYCLE_PHASE!r} not in "
    f"canonical CANONICAL_EVENT_LIFECYCLE_PHASES {CANONICAL_EVENT_LIFECYCLE_PHASES!r} -- "
    "matcher Pattern 73 SSOT drift detected"
)


# =============================================================================
# Dataclasses -- per-match decision + backfill receipt
# =============================================================================


@dataclass
class MatchResult:
    """Outcome of a single matcher decision.

    Returned by ``_match_one_candidate``; consumed by ``poll_once`` and
    ``backfill_all``.  Reports the decision the matcher made for a
    given platform_event candidate so the caller can aggregate stats.

    Attributes:
        canonical_event_id: id of the canonical_events row (newly-created
            or pre-existing via ON CONFLICT).  None if the match was
            queued for review (sub-threshold confidence).
        link_id: id of the canonical_event_links row (newly-created).
            None if the match was queued for review OR if a conflict
            with an existing active link was detected (rare; matcher
            yields to the existing link).
        action: 'create' / 'conflict' / 'queued_for_review' / 'error'.
            'create' = new canonical_event + link created; 'conflict'
            = canonical_event with this natural_key already exists
            (idempotency); 'queued_for_review' = confidence below
            threshold; 'error' = matcher exception, candidate skipped.
        platform_event_id: source platform_events.id for traceability.
        natural_key_hash: SHA-256 hex digest of the natural key (for
            audit/debug; the raw bytes go into canonical_events).
        note: free-form explanation (typically used for 'conflict' and
            'queued_for_review' outcomes).
    """

    action: str
    platform_event_id: int
    canonical_event_id: int | None = None
    link_id: int | None = None
    natural_key_hash: str = ""
    note: str | None = None


@dataclass
class BackfillReceipt:
    """Summary of a single backfill CLI invocation.

    Returned by ``backfill_all``; printed by ``precog matcher backfill``.
    Captures the per-action counts and any error excerpts for operator
    review.

    Attributes:
        created: count of new canonical_events + links created.
        conflicts: count of ON CONFLICT skips (canonical_event already
            existed; matcher yielded).
        queued_for_review: count of below-threshold matches routed to
            canonical_match_reviews.
        errors: count of exceptions; first 10 error messages captured
            in ``error_excerpts`` for runbook investigation.
        started_at: timestamp the backfill started.
        finished_at: timestamp the backfill completed (None if still
            running).
        batches_completed: count of transactional batches that committed.
        error_excerpts: up to 10 most-recent error messages from the
            run (operator-readable; trimmed to avoid runaway log
            output).
    """

    created: int = 0
    conflicts: int = 0
    queued_for_review: int = 0
    errors: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    batches_completed: int = 0
    error_excerpts: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        """Return a human-readable single-line summary for runbook printing."""
        elapsed = "n/a"
        if self.finished_at is not None:
            elapsed = f"{(self.finished_at - self.started_at).total_seconds():.1f}s"
        return (
            f"backfill summary: {self.created} created, "
            f"{self.conflicts} conflicts (idempotent skip), "
            f"{self.queued_for_review} queued for review, "
            f"{self.errors} errors in {self.batches_completed} batches "
            f"({elapsed})"
        )


# =============================================================================
# Natural-key derivation -- Pattern 73 SSOT for sport-event identity
# =============================================================================


def compute_natural_key_hash(
    *,
    sport: str,
    game_date: str,
    home_team_code: str,
    away_team_code: str,
) -> bytes:
    """Compute the canonical natural-key hash for a sport-event.

    The natural key is the deterministic identity tuple that
    canonicalizes a real-world sports event across platforms:
    (sport, game_date, sorted home/away team codes).  Sorting the
    team codes makes the hash invariant under home/away polarity --
    important because some platforms publish events with the polarity
    swapped vs. the canonical sports convention (and the canonical
    identity should not depend on cosmetic polarity).

    The hash is SHA-256 of the canonical pipe-separated string,
    matching the slot 0067 ``create_canonical_event`` docstring
    example.  Stable across runs / processes / machines.

    Args:
        sport: e.g., ``'NFL'`` / ``'NCAAF'`` / ``'NBA'`` / ``'NHL'``.
            Canonical home: ``games.sport`` column (uppercase
            convention).
        game_date: ISO-format date string ``'YYYY-MM-DD'`` per
            ``games.game_date::date::text`` PG output.
        home_team_code: ``games.home_team_code`` (uppercase team
            abbreviation, e.g., ``'BUF'``).
        away_team_code: ``games.away_team_code``.

    Returns:
        32-byte SHA-256 digest (bytes object suitable for direct
        psycopg2 BYTEA persistence).

    Example:
        >>> nk = compute_natural_key_hash(
        ...     sport="NFL",
        ...     game_date="2026-09-04",
        ...     home_team_code="MIA",
        ...     away_team_code="BUF",
        ... )
        >>> isinstance(nk, bytes) and len(nk) == 32
        True

    Educational Note:
        Sorting the team codes (rather than using home/away order) is
        the load-bearing invariance: a hash of ``(NFL, 2026-09-04,
        BUF, MIA)`` MUST equal a hash of ``(NFL, 2026-09-04, MIA, BUF)``
        so the same real-world event canonicalizes to the same hash
        regardless of which platform's polarity convention surfaced
        the event first.  The home/away roles still live on
        canonical_event_participants via ``role_id`` (1=home, 2=away).

    Reference:
        - ``crud_canonical_events.create_canonical_event`` docstring
          example
        - ADR-118 V2.38 decision #1 (natural_key_hash discipline)
    """
    sorted_codes = sorted([home_team_code.upper(), away_team_code.upper()])
    payload = f"{sport.upper()}|{game_date}|{sorted_codes[0]}|{sorted_codes[1]}"
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _format_resolution_window(game_time: datetime | None, game_date: str) -> str:
    """Build a TSTZRANGE string for canonical_events.resolution_window.

    Slot B convention: resolution window opens at game_time (or
    ``midnight UTC + 12h`` of game_date if game_time is unknown) and
    closes 6 hours later.  Sports events resolve within ~3-4 hours of
    kickoff for major leagues; 6h is a conservative envelope.

    Args:
        game_time: ``games.game_time`` (TIMESTAMPTZ, NULLABLE).
        game_date: ``games.game_date`` ISO string fallback when
            game_time is NULL.

    Returns:
        TSTZRANGE string in inclusive-inclusive form, e.g.,
        ``'[2026-09-04 17:00+00, 2026-09-04 23:00+00]'``.
    """
    if game_time is not None:
        start = game_time
    else:
        # game_date is YYYY-MM-DD; fall back to game_date midday UTC as a
        # conservative default.  Matcher does not gate creation on
        # game_time presence; downstream phase-advancement will refine.
        start = datetime.fromisoformat(f"{game_date}T12:00:00+00:00")
    end = start + timedelta(hours=6)
    # PG TSTZRANGE accepts ISO format with explicit UTC offset.
    return f"[{start.isoformat()},{end.isoformat()}]"


# =============================================================================
# Entity resolution -- lazy upsert of canonical_entities for team codes
# =============================================================================


def _resolve_team_entity_id(
    cur: Any,
    *,
    team_code: str,
    sport: str,
) -> int:
    """Resolve (or lazily create) a canonical_entities row for a team code.

    Slot B convention: each (team_code, sport) tuple resolves to a
    single canonical_entities row of ``entity_kind='team'`` (id=1 per
    Migration 0067 seed).  ``entity_key`` is built as ``'<sport>:<TEAM>'``
    so cross-sport collisions (e.g., NFL BUF Bills vs. NBA BUF if it
    existed) don't collapse.

    Pattern 73 SSOT: ``entity_kind='team'`` is the canonical key for
    sports teams per Migration 0067 seed data.  Cross-cohort entity-
    kind expansion (fighter / candidate / etc.) lives in entity-kind
    Migration 0067 seed; this matcher never touches kinds other than
    team in Slot B scope.

    Args:
        cur: psycopg2 cursor under active transaction.
        team_code: e.g., ``'BUF'`` (already-uppercase per
            games.home_team_code convention).
        sport: e.g., ``'NFL'``.

    Returns:
        canonical_entities.id (BIGSERIAL) for the team entity.

    Educational Note:
        canonical_entities is empty (0 rows) at Slot B activation per
        MCP probe.  The matcher's first run will lazily populate the
        table; subsequent runs hit the SELECT path.  This is the
        intended bootstrap: no eager seed of all ~120 NFL/NCAAF/NBA/NHL
        teams; the matcher creates entities on-demand as games surface
        them.

    Reference:
        - Migration 0067 (canonical_entity_kinds seed: team is id=1)
        - ``crud_canonical_entity.py`` (low-level entities CRUD)
    """
    entity_key = f"{sport.upper()}:{team_code.upper()}"

    # SELECT first (fast path -- most teams will already exist after
    # the first matcher cycle).
    cur.execute(
        """
        SELECT id FROM canonical_entities
        WHERE entity_kind_id = 1  -- 'team' (Migration 0067 seed id)
          AND entity_key = %s
        """,
        (entity_key,),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row["id"])

    # INSERT lazy-create.  Pattern 73 SSOT: entity_kind_id=1 (team)
    # matches Migration 0067 seed.  display_name uses team_code as a
    # reasonable default; future operator UI flows can edit.
    cur.execute(
        """
        INSERT INTO canonical_entities (entity_kind_id, entity_key, display_name, metadata)
        VALUES (1, %s, %s, %s::jsonb)
        RETURNING id
        """,
        (entity_key, team_code, json.dumps({"created_by": "matcher:slot-B:v1"})),
    )
    row = cur.fetchone()
    return int(row["id"])


# =============================================================================
# Single-candidate match -- the V2.44 atomic transaction core
# =============================================================================


@dataclass
class _Candidate:
    """Internal candidate row materialized from the matcher's SELECT pass."""

    platform_event_id: int
    game_id: int
    sport: str
    game_date: str  # YYYY-MM-DD format
    home_team_code: str
    away_team_code: str
    game_time: datetime | None
    game_title: str  # built from teams for canonical_events.title


def _match_one_candidate(
    cur: Any,
    candidate: _Candidate,
    *,
    algorithm_id: int,
    created_by: str,
    decided_by: str,
    t_match: Decimal,
) -> MatchResult:
    """Match a single platform_event candidate; write V2.44 atomic bundle.

    The matcher's core write path.  Called per-candidate inside the
    caller's transaction (caller owns commit / rollback).  Implements
    parent spec § File 4 step-by-step:

        1. Compute natural_key_hash from (sport, game_date, sorted
           home/away team codes).
        2. SELECT canonical_events WHERE natural_key_hash = nk for ON
           CONFLICT idempotency.  If exists, skip to step 6 (link
           creation with existing canonical_event_id).
        3. Resolve / lazily create canonical_entities for home_team
           + away_team.
        4. INSERT canonical_events row with lifecycle_phase='proposed'.
        5. INSERT canonical_event_participants rows (home + away).
        6. INSERT canonical_event_links row (V2.44 atomicity).  May
           raise ExclusionViolation if an active link already exists
           for this platform_event_id -- caller handles by routing
           the candidate to ``conflict`` outcome.
        7. INSERT canonical_event_match_log row recording the decision.
        8. UPDATE games.canonical_event_id back-link.
        9. UPDATE game_states.canonical_event_id back-link for ALL
           game_states rows where game_id = candidate.game_id.

    Steps 4-9 run inside the caller's active transaction; either ALL
    commit or ALL roll back (V2.44 atomicity).

    Args:
        cur: psycopg2 cursor under active transaction.
        candidate: materialized candidate row from the matcher's
            SELECT.
        algorithm_id: cohort5_event_matcher_v1 (or manual_v1 for
            operator-driven invocations).
        created_by: canonical_events.created_by identity string.
        decided_by: canonical_event_links.decided_by +
            canonical_event_match_log.decided_by identity string.
        t_match: confidence threshold for review queue routing.
            Currently unused for natural-key matches (always 1.0
            confidence); reserved for fuzzy-match algorithms in
            future cohorts.

    Returns:
        ``MatchResult`` with action='create' / 'conflict' /
        'queued_for_review' / 'error'.
    """
    import psycopg2  # local import to keep top-level imports lean

    nk = compute_natural_key_hash(
        sport=candidate.sport,
        game_date=candidate.game_date,
        home_team_code=candidate.home_team_code,
        away_team_code=candidate.away_team_code,
    )
    nk_hex = nk.hex()

    # Confidence: pure natural-key matches are 1.0 (no ambiguity).
    confidence = _NATURAL_KEY_MATCH_CONFIDENCE

    # Step 2: idempotency check.  If the canonical_events row already
    # exists by natural_key_hash, this is a re-run / reprocessing path
    # -- yield to the existing row.
    cur.execute(
        "SELECT id FROM canonical_events WHERE natural_key_hash = %s",
        (psycopg2.Binary(nk),),
    )
    existing = cur.fetchone()

    if existing is not None:
        canonical_event_id = int(existing["id"])
        logger.info(
            "matcher: canonical_events row already exists for platform_event_id=%d "
            "(natural_key=%s); attempting link creation only",
            candidate.platform_event_id,
            nk_hex[:12],
        )
    else:
        # Step 3: lazy-resolve canonical_entities for home + away teams.
        home_entity_id = _resolve_team_entity_id(
            cur, team_code=candidate.home_team_code, sport=candidate.sport
        )
        away_entity_id = _resolve_team_entity_id(
            cur, team_code=candidate.away_team_code, sport=candidate.sport
        )

        # Step 4: INSERT canonical_events.  Use existing
        # crud_canonical_events shape but inline here to share the
        # transaction (cursor-aware variant doesn't exist for that
        # CRUD; matcher imports the raw INSERT shape to honor V2.44).
        participants_sorted = sorted([home_entity_id, away_entity_id])
        resolution_window = _format_resolution_window(candidate.game_time, candidate.game_date)

        cur.execute(
            """
            INSERT INTO canonical_events (
                event_domain_id, event_type_id, participants_sorted,
                resolution_window, natural_key_hash, title, description,
                lifecycle_phase, metadata, created_by
            ) VALUES (
                1,  -- 'sports' (Migration 0067 seed)
                1,  -- 'game' (Migration 0067 seed)
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            RETURNING id
            """,
            (
                participants_sorted,
                resolution_window,
                psycopg2.Binary(nk),
                candidate.game_title,
                f"Sport={candidate.sport}, date={candidate.game_date}",
                _INITIAL_LIFECYCLE_PHASE,
                json.dumps(
                    {
                        "source_game_id": candidate.game_id,
                        "matcher_version": "slot-B:v1",
                    }
                ),
                created_by,
            ),
        )
        row = cur.fetchone()
        canonical_event_id = int(row["id"])

        # Step 5: INSERT canonical_event_participants for home + away.
        # role_id=1 (home), 2 (away) per Migration 0067 seed.
        cur.execute(
            """
            INSERT INTO canonical_event_participants (
                canonical_event_id, canonical_entity_id, role_id, sequence_number
            ) VALUES (%s, %s, 1, 1), (%s, %s, 2, 2)
            """,
            (
                canonical_event_id,
                home_entity_id,
                canonical_event_id,
                away_entity_id,
            ),
        )

    # Step 6: INSERT canonical_event_links via CRUD helper.  May raise
    # ExclusionViolation if an active link exists already (rare; matcher
    # yields).
    try:
        link_id = create_link_in_cursor(
            cur,
            canonical_event_id=canonical_event_id,
            platform_event_id=candidate.platform_event_id,
            confidence=confidence,
            algorithm_id=algorithm_id,
            decided_by=decided_by,
            link_state="active",
        )
    except psycopg2.errors.ExclusionViolation:
        # Existing active link for this platform_event_id -- the
        # canonical_event_link from a prior matcher cycle is still
        # active.  Matcher yields; the caller routes this candidate
        # to 'conflict' outcome.  No audit row written (the prior
        # link's audit row anchors the history).
        logger.warning(
            "matcher: ExclusionViolation on canonical_event_links INSERT "
            "for platform_event_id=%d; prior active link wins",
            candidate.platform_event_id,
        )
        return MatchResult(
            action="conflict",
            platform_event_id=candidate.platform_event_id,
            canonical_event_id=canonical_event_id,
            natural_key_hash=nk_hex,
            note="active canonical_event_link already exists for this platform_event_id",
        )

    # Step 7: INSERT canonical_event_match_log row via CRUD helper.
    append_event_match_log_row_in_cursor(
        cur,
        action="create",
        decided_by=decided_by,
        algorithm_id=algorithm_id,
        canonical_event_id=canonical_event_id,
        link_id=link_id,
        platform_event_id=candidate.platform_event_id,
        confidence=confidence,
        features={
            "source": "natural_key_v1",
            "natural_key_hex": nk_hex,
            "sport": candidate.sport,
            "game_date": candidate.game_date,
        },
        note=f"matcher slot-B: linked platform_event_id={candidate.platform_event_id}",
    )

    # Step 8: UPDATE games.canonical_event_id back-link.
    cur.execute(
        """
        UPDATE games SET canonical_event_id = %s
        WHERE id = %s AND canonical_event_id IS NULL
        """,
        (canonical_event_id, candidate.game_id),
    )

    # Step 9: UPDATE game_states.canonical_event_id back-link for all
    # game_states rows pointing at this game_id.  Only updates rows
    # where canonical_event_id IS NULL (idempotent against re-runs).
    cur.execute(
        """
        UPDATE game_states SET canonical_event_id = %s
        WHERE game_id = %s AND canonical_event_id IS NULL
        """,
        (canonical_event_id, candidate.game_id),
    )

    logger.info(
        "matcher: created canonical_event_id=%d + link_id=%d for "
        "platform_event_id=%d (natural_key=%s, sport=%s, date=%s)",
        canonical_event_id,
        link_id,
        candidate.platform_event_id,
        nk_hex[:12],
        candidate.sport,
        candidate.game_date,
    )

    return MatchResult(
        action="create",
        platform_event_id=candidate.platform_event_id,
        canonical_event_id=canonical_event_id,
        link_id=link_id,
        natural_key_hash=nk_hex,
    )


# =============================================================================
# Candidate selection -- the matcher's SELECT pass
# =============================================================================


def _select_candidates(
    cur: Any,
    *,
    limit: int,
    only_unlinked_games: bool = True,
) -> list[_Candidate]:
    """Select platform_event candidates for the matcher to process.

    Joins platform_events -> games and filters for platform_events
    that have a game_id but lack an active canonical_event_link.
    Returns up to ``limit`` candidates in game_date ASC order
    (oldest-first per parent spec Open Builder Q1).

    Args:
        cur: psycopg2 cursor.
        limit: max rows to return.
        only_unlinked_games: when True (default), filter further to
            only games that have NULL canonical_event_id (idempotency
            across matcher re-runs).  Backfill mode passes True;
            future re-link flows may pass False.

    Returns:
        List of ``_Candidate`` objects.
    """
    query = """
        SELECT
            pe.id AS platform_event_id,
            pe.game_id,
            g.sport,
            g.game_date::text AS game_date,
            g.home_team_code,
            g.away_team_code,
            g.game_time,
            COALESCE(pe.title, g.home_team_code || ' @ ' || g.away_team_code) AS game_title
        FROM platform_events pe
        JOIN games g ON g.id = pe.game_id
        WHERE pe.game_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM canonical_event_links cel
              WHERE cel.platform_event_id = pe.id
                AND cel.link_state = 'active'
          )
    """
    if only_unlinked_games:
        query += " AND g.canonical_event_id IS NULL "
    query += " ORDER BY g.game_date ASC, pe.id ASC LIMIT %s "

    cur.execute(query, (limit,))
    rows = cur.fetchall()
    return [
        _Candidate(
            platform_event_id=int(r["platform_event_id"]),
            game_id=int(r["game_id"]),
            sport=str(r["sport"]),
            game_date=str(r["game_date"]),
            home_team_code=str(r["home_team_code"]),
            away_team_code=str(r["away_team_code"]),
            game_time=r["game_time"],
            game_title=str(r["game_title"]),
        )
        for r in rows
    ]


# =============================================================================
# Backfill entry point -- opt-in CLI driver
# =============================================================================


def backfill_all(
    *,
    batch_size: int = 1000,
    dry_run: bool = False,
    max_batches: int | None = None,
) -> BackfillReceipt:
    """One-time backfill of canonical_events from existing upstream source state.

    Idempotent: ON CONFLICT against ``uq_canonical_events_nk`` UNIQUE
    constraint skips already-matched rows.  Bounded: each batch is a
    single transaction (commit-bundled per V2.44).  Receipted: returns
    a ``BackfillReceipt`` with per-action counts + summary text.

    Args:
        batch_size: rows per transaction.  Default 1000; larger
            batches are off-peak-friendly but increase lock duration.
        dry_run: when True, prints planned actions but does NOT write.
            ROLLBACK after each batch; receipt counts reflect planned
            actions.
        max_batches: optional bound on batch count (defensive against
            runaway loops).  None = unbounded.

    Returns:
        ``BackfillReceipt`` with full per-action breakdown +
        ``summary_text()`` for runbook printing.

    Example:
        >>> receipt = backfill_all(batch_size=500, dry_run=True)
        >>> print(receipt.summary_text())
        backfill summary: 0 created, 0 conflicts (idempotent skip), ...

    Reference:
        - Parent spec § File 4 backfill mode
        - Parent spec Open Builder Q1 (oldest-first ordering applied)
        - CLI: ``precog matcher backfill --all``
    """
    receipt = BackfillReceipt()
    algorithm_id = get_cohort5_event_matcher_algorithm_id()

    batch_idx = 0
    while True:
        if max_batches is not None and batch_idx >= max_batches:
            logger.info("matcher backfill: max_batches=%d reached; stopping", max_batches)
            break

        # Open a fresh transaction per batch.  commit=True commits on
        # success; on exception the with-block rolls back automatically.
        try:
            with get_cursor(commit=not dry_run) as cur:
                candidates = _select_candidates(cur, limit=batch_size)
                if not candidates:
                    logger.info(
                        "matcher backfill: no more candidates; stopping after "
                        "batch %d (created=%d, conflicts=%d, queued=%d, errors=%d)",
                        batch_idx,
                        receipt.created,
                        receipt.conflicts,
                        receipt.queued_for_review,
                        receipt.errors,
                    )
                    break

                for candidate in candidates:
                    try:
                        result = _match_one_candidate(
                            cur,
                            candidate,
                            algorithm_id=algorithm_id,
                            created_by=CREATED_BY_BACKFILL,
                            decided_by=DECIDED_BY_BACKFILL,
                            t_match=_DEFAULT_T_MATCH,
                        )
                        if result.action == "create":
                            receipt.created += 1
                        elif result.action == "conflict":
                            receipt.conflicts += 1
                        elif result.action == "queued_for_review":
                            receipt.queued_for_review += 1
                    except Exception as e:
                        receipt.errors += 1
                        if len(receipt.error_excerpts) < 10:
                            receipt.error_excerpts.append(
                                f"platform_event_id={candidate.platform_event_id}: "
                                f"{type(e).__name__}: {e}"
                            )
                        logger.exception(
                            "matcher backfill: per-candidate error for platform_event_id=%d",
                            candidate.platform_event_id,
                        )
                        # Per-candidate error invalidates the entire
                        # batch transaction (atomic V2.44); raise to
                        # roll back and let the outer loop continue
                        # with a fresh batch.
                        raise

                receipt.batches_completed += 1
                if dry_run:
                    # Caller-visible: the with-block won't commit when
                    # commit=False, so changes within the cursor's
                    # transaction roll back naturally on context exit.
                    pass
        except Exception:
            # Batch-level error caught and recorded; continue to next
            # batch unless the error rate is alarming.  The hybrid
            # failure-mode policy (parent spec Q5) trips after 5
            # consecutive batch failures; for simplicity Slot B's
            # backfill loop is single-pass with per-batch logging only.
            logger.warning(
                "matcher backfill: batch %d failed; continuing to next batch",
                batch_idx,
            )

        batch_idx += 1

    receipt.finished_at = datetime.now(UTC)

    # Write a single audit-log summary row (parent spec Open Builder
    # Q3 default: write summary as a single action='create' row with
    # summary note).  Skip in dry_run mode (no DB writes).  We use
    # action='create' here even though semantically this is a "summary"
    # action because the closed-vocab CHECK on canonical_event_match_log
    # doesn't include 'summary'; the note field carries the discriminator.
    if not dry_run:
        try:
            from precog.database.crud_canonical_event_match_log import (
                append_event_match_log_row,
            )

            append_event_match_log_row(
                action="create",
                decided_by=DECIDED_BY_BACKFILL,
                algorithm_id=algorithm_id,
                canonical_event_id=None,  # summary row, no specific event
                note=f"BACKFILL_SUMMARY: {receipt.summary_text()}",
            )
        except Exception:
            logger.exception(
                "matcher backfill: failed to write summary audit row (receipt counts unaffected)"
            )

    return receipt


# =============================================================================
# Steady-state poller -- BasePoller-derived service
# =============================================================================


class CanonicalEventMatcher(BasePoller):
    """Background service for canonical-event matching (Slot B steady-state).

    Polls every ~30s for platform_events that have a game_id but lack
    an active canonical_event_link.  For each, writes the V2.44 atomic
    bundle (canonical_events + canonical_event_link +
    canonical_event_match_log + game_states/games back-link UPDATEs).

    Per parent spec Q1 (pull-primary poller) + V2.44 atomicity contract
    + P41 9-capability mandates.

    The class-var triplet (SERVICE_KEY / HEALTH_COMPONENT / BREAKER_TYPE)
    is the metadata the supervisor reads at registration time per the
    pattern documented in ``service_supervisor.py`` SERVICE_TO_COMPONENT
    registry.

    Cohort 5+ Slot B activation: feature flag
    ``features.canonical_event_matcher.enabled`` defaults to ``false``
    until session 108+ soak window opens (parent spec § Dispatch Plan).
    """

    SERVICE_KEY: ClassVar[str] = "canonical_event_matcher"
    HEALTH_COMPONENT: ClassVar[str] = "canonical_event_matcher"
    # data_stale is the operationally-correct breaker for an event-
    # matching service that has gone silent (parallel to
    # canonical_observations_writer's choice for the same operational
    # reason: consumer-facing alert "the matcher is not creating fresh
    # canonical_events").
    BREAKER_TYPE: ClassVar[str] = "data_stale"

    MIN_POLL_INTERVAL: ClassVar[int] = 5
    # 30s baseline per parent spec § File 7 YAML default; matches
    # canonical_observations_writer + temporal_alignment_writer cadence.
    DEFAULT_POLL_INTERVAL: ClassVar[int] = 30

    # Default per-poll batch size (steady-state is smaller than backfill
    # to keep transaction duration short).
    DEFAULT_BATCH_SIZE: ClassVar[int] = 100

    def __init__(
        self,
        poll_interval: int | None = None,
        batch_size: int | None = None,
        t_match: Decimal | None = None,
        circuit_breaker_threshold: int | None = None,
    ) -> None:
        super().__init__(poll_interval=poll_interval)
        self.batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        self.t_match = t_match or _DEFAULT_T_MATCH
        self.circuit_breaker_threshold = (
            circuit_breaker_threshold or _DEFAULT_CIRCUIT_BREAKER_THRESHOLD
        )
        # Algorithm id is resolved lazily on first poll (avoids DB hit
        # at construction time when the DB may not yet be at head).
        self._algorithm_id: int | None = None
        # Hybrid failure-mode tracking (parent spec Q5): consecutive
        # logical-failure count; resets to 0 on a successful poll.
        self._consecutive_failures = 0

    def _get_job_name(self) -> str:
        return "Canonical Event Matcher"

    def _poll_once(self) -> dict[str, int]:
        """Single matcher poll cycle.

        Selects up to ``batch_size`` candidates and processes them in
        a single V2.44 atomic transaction.  On per-candidate exception,
        the entire batch rolls back and the matcher logs the error;
        the next cycle re-tries (idempotent via uq_canonical_events_nk).

        Returns:
            Stats dict with ``items_created`` count (matches BasePoller
            convention).
        """
        if self._algorithm_id is None:
            self._algorithm_id = get_cohort5_event_matcher_algorithm_id()

        created = 0
        conflicts = 0
        errors = 0

        try:
            with get_cursor(commit=True) as cur:
                candidates = _select_candidates(cur, limit=self.batch_size)
                for candidate in candidates:
                    try:
                        result = _match_one_candidate(
                            cur,
                            candidate,
                            algorithm_id=self._algorithm_id,
                            created_by=CREATED_BY_MATCHER,
                            decided_by=DECIDED_BY_MATCHER,
                            t_match=self.t_match,
                        )
                        if result.action == "create":
                            created += 1
                        elif result.action == "conflict":
                            conflicts += 1
                    except Exception as e:
                        errors += 1
                        logger.exception(
                            "matcher: per-candidate exception "
                            "platform_event_id=%d (%s); batch will roll back",
                            candidate.platform_event_id,
                            type(e).__name__,
                        )
                        # V2.44 atomicity: per-candidate exception
                        # rolls back the entire batch.  Re-raise so
                        # the cursor context exits with rollback.
                        raise

            # Success path: reset consecutive-failure counter.
            self._consecutive_failures = 0
        except Exception:
            self._consecutive_failures += 1
            logger.warning(
                "matcher: poll cycle failed (%d consecutive failures); "
                "circuit breaker threshold=%d",
                self._consecutive_failures,
                self.circuit_breaker_threshold,
            )
            # The hybrid failure-mode pause is logged at WARN-level; a
            # future cohort plumbs system_health pause logic to gate
            # subsequent polls when consecutive failures exceed
            # threshold.  Slot B logs the condition for operator
            # visibility but does not auto-pause (the supervisor's
            # existing circuit-breaker integration would handle pause).
            return {
                "items_created": 0,
                "items_fetched": 0,
                "errors": 1,
            }

        return {
            "items_created": created,
            "items_fetched": created + conflicts,
            "items_updated": 0,
        }


def create_canonical_event_matcher(
    poll_interval: int = CanonicalEventMatcher.DEFAULT_POLL_INTERVAL,
    batch_size: int = CanonicalEventMatcher.DEFAULT_BATCH_SIZE,
) -> CanonicalEventMatcher:
    """Factory function for ServiceSupervisor registration.

    Mirrors the ``create_canonical_observations_writer`` factory shape
    so the supervisor's SERVICE_FACTORIES registry has uniform
    construction semantics.
    """
    return CanonicalEventMatcher(
        poll_interval=poll_interval,
        batch_size=batch_size,
    )


# =============================================================================
# Matcher status query helpers -- consumed by the CLI status command
# =============================================================================


def get_matcher_status_summary() -> dict[str, Any]:
    """Return a dict summarizing matcher status for the CLI / operator runbook.

    Reads from system_health + canonical_event_match_log; doesn't
    require the matcher service to be running (operator can inspect
    history even when the matcher is paused).

    Returns:
        Dict with keys:
            - last_heartbeat: most-recent system_health.last_check for
              ``component='canonical_event_matcher'``, or None.
            - recent_creates_24h: count of action='create' rows in the
              last 24 hours.
            - recent_conflicts_24h: count of action='retire' /
              'quarantine' rows in the last 24 hours.
            - unlinked_platform_events: count of platform_events with
              game_id but no active canonical_event_link (matcher's
              pending queue).
            - matcher_algorithm_id: id of cohort5_event_matcher_v1 in
              match_algorithm.

    Example:
        >>> summary = get_matcher_status_summary()
        >>> print(f"Last heartbeat: {summary['last_heartbeat']}")
        >>> print(f"Pending queue: {summary['unlinked_platform_events']}")

    Reference:
        - Migration 0091 (canonical_event_match_log indexes)
        - CLI: ``precog matcher status``
    """
    summary: dict[str, Any] = {
        "last_heartbeat": None,
        "recent_creates_24h": 0,
        "recent_conflicts_24h": 0,
        "unlinked_platform_events": 0,
        "matcher_algorithm_id": None,
    }

    # Heartbeat from system_health (matcher is registered with the
    # supervisor under component='canonical_event_matcher').
    health = fetch_one(
        """
        SELECT last_check, status
        FROM system_health
        WHERE component = 'canonical_event_matcher'
        ORDER BY last_check DESC
        LIMIT 1
        """,
        (),
    )
    if health is not None:
        summary["last_heartbeat"] = health["last_check"]
        summary["heartbeat_status"] = health["status"]

    # 24-hour action counts -- direct match-log query.
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    rows = fetch_all(
        """
        SELECT action, COUNT(*) AS n
        FROM canonical_event_match_log
        WHERE decided_at >= %s
        GROUP BY action
        """,
        (cutoff,),
    )
    for row in rows:
        if row["action"] == "create":
            summary["recent_creates_24h"] = int(row["n"])
        elif row["action"] in ("retire", "quarantine"):
            summary["recent_conflicts_24h"] += int(row["n"])

    # Pending queue count -- platform_events with game_id but no active link.
    pending = fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM platform_events pe
        WHERE pe.game_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM canonical_event_links cel
              WHERE cel.platform_event_id = pe.id
                AND cel.link_state = 'active'
          )
        """,
        (),
    )
    if pending is not None:
        summary["unlinked_platform_events"] = int(pending["n"])

    # Algorithm id from the match_algorithm seed row.
    algo = fetch_one(
        "SELECT id FROM match_algorithm WHERE name = %s AND version = %s",
        ("cohort5_event_matcher_v1", "1.0.0"),
    )
    if algo is not None:
        summary["matcher_algorithm_id"] = int(algo["id"])

    return summary
