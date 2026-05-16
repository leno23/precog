"""CRUD operations for canonical_event_match_log -- THE event-matching audit ledger.

Cohort 5+ Slot B audit ledger (Migration 0091) per session-92 4-agent
Cohort 5+ Slot B design council (Galadriel + Holden + Miles + Uhura) +
PM-composed build spec at ``memory/build_spec_slot_a_matcher_pm_memo.md``
+ session-107 rebase addendum at
``memory/build_spec_slot_b_matcher_addendum_session_107.md``.

Sister module to ``crud_canonical_match_log.py`` (slot 0073 -- market-
tier) and ``crud_canonical_event_phase_log.py`` (slot 0079 -- event-
phase tier).  Uses the same raw-psycopg2 + ``get_cursor`` /
RealDictCursor + heavy-docstring conventions.

Tables covered:
    - ``canonical_event_match_log`` (Migration 0091) -- append-only
      audit ledger for the event-matching layer.  Every match decision
      the matcher makes (``create`` / ``retire`` / ``quarantine`` on
      canonical_event_links, plus ``review_approve`` / ``review_reject``
      from operator-driven review flows) writes one row here.

THE RESTRICTED API SURFACE -- APPEND-ONLY VIA APPLICATION DISCIPLINE:

    This module exposes EXACTLY ONE write function:
    ``append_event_match_log_row()`` (+ a cursor-aware sibling for
    transaction-spanning atomicity per V2.44).  There are NO
    ``update_*`` / ``delete_*`` / ``upsert_*`` functions.  This is by
    design: the audit ledger is append-only, and the discipline lives
    in this module's API surface (Migration 0091 docstring §
    "Append-only via application discipline").

    The trigger-enforced version (BEFORE UPDATE/DELETE -> RAISE
    EXCEPTION 'canonical_event_match_log is append-only') is queued
    for a future slot after a 30-day production soak validates the
    application-discipline approach (slot 0073 + 0079 precedent).
    Until then:

        - DO NOT add ``update_*`` / ``delete_*`` / ``upsert_*``
          helpers to this module without an ADR amendment.
        - DO NOT write ad-hoc ``UPDATE canonical_event_match_log`` /
          ``DELETE FROM canonical_event_match_log`` SQL anywhere
          outside the slot-B migration's downgrade (Pattern 73
          violation -- consumers would drift; future S81 grep audits
          will sweep for this).
        - The ``append_event_match_log_row()`` function is the ONLY
          sanctioned write path; future log-readers can rely on the
          function's contract (validation + invariant enforcement).

Pattern 73 SSOT discipline (CLAUDE.md Critical Pattern #8):

    Two vocabularies are SSOT-anchored at
    ``src/precog/database/constants.py``:

        ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` -- 6-value event-
                                                      tier ``action``
                                                      vocabulary
                                                      (create / retire
                                                      / update_phase /
                                                      review_approve /
                                                      review_reject /
                                                      quarantine).
                                                      Inline DDL CHECK
                                                      on
                                                      canonical_event_match_log.action
                                                      cites this
                                                      constant by name;
                                                      this module uses
                                                      it in real-guard
                                                      ``ValueError``-
                                                      raising
                                                      validation.

        ``DECIDED_BY_PREFIXES``                     -- 3-prefix actor
                                                      taxonomy reused
                                                      from slot 0073
                                                      (``human:`` /
                                                      ``service:`` /
                                                      ``system:``).
                                                      Real-guard
                                                      validation in
                                                      ``append_event_match_log_row()``
                                                      enforces prefix
                                                      discipline.

    Slot 0073 + 0079 inheritance: imports are USED in real-guard
    validation (not side-effect-only) per #1085 finding #2
    strengthening.  Drift between the constant and the DDL CHECK
    surfaces as a Pattern 73 SSOT failure mode at test time.

decided_by value-set convention (Pattern 73 SSOT pointer; slot 0073
inheritance):

    Canonical home: ``constants.py:DECIDED_BY_PREFIXES``.  Conventions:
        ``'human:<username>'``    -- human-driven action (operator
                                     review / manual matcher invocation).
        ``'service:matcher:v1'``  -- autonomous matcher service
                                     (steady-state pull-poller).  Renamed
                                     from ``'service:matcher:slot-B:v1'``
                                     by Migration 0092 (session 110)
                                     dropping session-planning shorthand.
        ``'service:cli:matcher-backfill:v1'`` -- backfill CLI invocation
                                                  (operator-triggered).
        ``'system:<context>'``    -- seed / migration / fixture writes.

    Length-bound enforcement (``len(decided_by) <= 64``) lives in
    ``append_event_match_log_row()`` validation per slot 0073 #1085
    finding #3 inheritance.

V2.44 atomicity contract (parent spec § Per-Q Adjudications Q4 +
session-92 council convergence):

    The matcher's steady-state write path is a single transaction that
    INSERTs canonical_events + canonical_event_links +
    canonical_event_phase_log + canonical_event_match_log + UPDATEs
    games.canonical_event_id + game_states.canonical_event_id in
    bundled commit.  This module's ``append_event_match_log_row_in_cursor()``
    is the cursor-aware sibling that the matcher uses inside its
    transaction boundary so the audit row commits atomically with the
    primary writes.

L33 dedicated CRUD module restriction:

    All write paths to canonical_event_match_log MUST go through this
    module's ``append_event_match_log_row()`` /
    ``append_event_match_log_row_in_cursor()`` functions.  There is NO
    direct-SQL escape hatch in the public API; future call sites
    adding ad-hoc INSERT SQL bypass the validation layer (Pattern 73
    SSOT vocabulary checks + decided_by length bound + decided_by
    prefix discipline + confidence bound) and represent a Pattern 73
    violation.  S81 grep audits sweep for direct INSERT into
    canonical_event_match_log outside this module + the slot-B
    migration's seed.

Reference:
    - ``docs/foundation/ARCHITECTURE_DECISIONS.md`` ADR-118 V2.44 +
      V2.46 + V2.49
    - ``src/precog/database/alembic/versions/0091_canonical_event_match_log_and_matcher_provenance.py``
    - ``src/precog/database/crud_canonical_match_log.py`` (slot 0073
      style + pattern reference; market-tier sister module)
    - ``src/precog/database/crud_canonical_event_phase_log.py`` (slot
      0079 style + pattern reference; event-phase tier sister module)
    - ``src/precog/database/constants.py``
      ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` + ``DECIDED_BY_PREFIXES``
    - ``memory/build_spec_slot_a_matcher_pm_memo.md`` (binding build spec)
    - ``memory/build_spec_slot_b_matcher_addendum_session_107.md`` (rebase addendum)
"""

import json
import logging
from decimal import Decimal
from typing import Any, cast

from .connection import fetch_all, fetch_one, get_cursor
from .constants import CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES, DECIDED_BY_PREFIXES

logger = logging.getLogger(__name__)


# Maximum allowed length for ``decided_by`` matches the DDL column boundary
# (``VARCHAR(64)``).  Centralizing the constant here lets callers reference
# it without re-deriving the magic number; surfaced to module scope rather
# than duplicated inside the validation function.  Mirrors slot 0073's
# ``_DECIDED_BY_MAX_LENGTH`` shape.
_DECIDED_BY_MAX_LENGTH = 64


# Lazy per-process cache for the event_matcher_v1 algorithm_id.  The seed
# row is immutable post-Migration-0091 (renamed by Migration 0092), so
# caching the lookup at module level is safe and keeps the per-call DB
# cost zero after the first resolution.  Mirrors slot 0073's
# ``_MANUAL_V1_ID_CACHE`` shape.
_EVENT_MATCHER_ALGO_ID_CACHE: int | None = None


# =============================================================================
# CROSS-MODULE HELPER -- event_matcher_v1 algorithm_id resolution
# =============================================================================


def get_event_matcher_algorithm_id() -> int:
    """Resolve the canonical event_matcher_v1 algorithm_id (lazy-cached).

    Per the event matcher convention (Migration 0091 seed, renamed by
    Migration 0092): the matcher's primary writer path uses algorithm_id
    pointing at this seeded row.  Operator-driven review_approve /
    review_reject paths use ``manual_v1.id`` (Migration 0071 seed) per
    slot 0073 precedent.

    Centralized here (matcher-log CRUD module) per slot 0073's
    ``get_manual_v1_algorithm_id()`` precedent -- closes the helper-
    triplication that would arise if every matcher caller re-derived
    the resolution.

    Lazy resolution: cached at module level on first call.  Module
    imports cleanly in environments where the DB is not yet at head;
    first call hits the DB once per process.

    Returns:
        The BIGSERIAL ``id`` of the event_matcher_v1 row in
        match_algorithm.

    Raises:
        RuntimeError: if the seed row is missing (Migration 0091 not
            applied, or seed was DELETEd; Migration 0092 not applied,
            so row is still under the old name).
    """
    global _EVENT_MATCHER_ALGO_ID_CACHE
    if _EVENT_MATCHER_ALGO_ID_CACHE is not None:
        return _EVENT_MATCHER_ALGO_ID_CACHE
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT id FROM match_algorithm WHERE name = %s AND version = %s",
            ("event_matcher_v1", "1.0.0"),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "event_matcher_v1 algorithm row not found in match_algorithm -- "
                "ensure Migrations 0091 (INSERT) and 0092 (rename) have run "
                "and the seed row is present"
            )
    _EVENT_MATCHER_ALGO_ID_CACHE = cast("int", row["id"])
    return _EVENT_MATCHER_ALGO_ID_CACHE


# =============================================================================
# CANONICAL EVENT MATCH LOG -- APPEND-ONLY WRITE PATH
# =============================================================================


def append_event_match_log_row(
    *,
    action: str,
    decided_by: str,
    algorithm_id: int,
    canonical_event_id: int | None = None,
    link_id: int | None = None,
    platform_event_id: int | None = None,
    confidence: Decimal | None = None,
    features: dict[str, Any] | None = None,
    prior_link_id: int | None = None,
    note: str | None = None,
) -> int:
    """Append one row to canonical_event_match_log.  THE ONLY SANCTIONED WRITE PATH.

    The audit ledger is append-only.  This function performs CRUD-layer
    validation that complements the DDL CHECK constraints (Pattern 73
    SSOT real-guard discipline inherited from slot 0073 + 0079):

        - ``action`` MUST be in ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``
          (Pattern 73 SSOT real-guard validation; raises ``ValueError``
          before SQL).
        - ``decided_by`` MUST start with one of ``DECIDED_BY_PREFIXES``
          (Pattern 73 SSOT real-guard validation).
        - ``len(decided_by)`` MUST be ``<= 64`` (boundary validation
          per slot 0073 #1085 finding #3 -- surfaces a clear
          ``ValueError`` before psycopg2 raises a generic
          StringDataRightTruncation).
        - ``confidence`` MUST be NULL OR a ``Decimal`` in [0, 1] AND
          NOT ``Decimal('NaN')`` (CRUD-layer parity with DDL CHECK;
          ``float`` rejected via TypeError per CLAUDE.md Critical
          Pattern #1).
        - All FK columns are passed through unchanged; psycopg2
          surfaces FK violations as ``ForeignKeyViolation``.

    Args:
        action: VARCHAR(16) discriminator.  MUST be in
            ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``.
        decided_by: VARCHAR(64) NOT NULL actor attribution.  MUST
            start with one of ``DECIDED_BY_PREFIXES``; MUST be <= 64
            chars.
        algorithm_id: BIGINT FK into ``match_algorithm.id``.  NOT
            NULL.  Matcher writes use ``event_matcher_v1.id``
            (resolved via ``get_event_matcher_algorithm_id()``);
            operator-decided rows use ``manual_v1.id`` (slot 0073
            ``get_manual_v1_algorithm_id()`` precedent).
        canonical_event_id: BIGINT FK into ``canonical_events.id``.
            NULL allowed for ``action='create'`` rows where the
            canonical_event_id has just been INSERTed in the same
            transaction (caller's ordering preference) -- typical
            matcher writes pass the freshly-allocated id.
        link_id: BIGINT FK into ``canonical_event_links.id``.  NULL
            allowed for create rows where the link_id has just been
            INSERTed.
        platform_event_id: INTEGER FK into ``platform_events.id``.
            NULL allowed for human-only audit rows that don't carry
            a platform-tier anchor.
        confidence: NUMERIC(4,3) algorithm score.  NULL allowed (human
            overrides have no algorithmic confidence).  Decimal-only
            per CLAUDE.md Critical Pattern #1; never float.
        features: JSONB free-form input snapshot at decision time.
            NULL acceptable.
        prior_link_id: BIGINT FK into ``canonical_event_links.id``.
            For ``action='retire'`` / ``action='quarantine'`` rows:
            pointer to the predecessor link row being superseded.
            NULL for fresh ``create`` rows where there is no
            predecessor.
        note: Free-text TEXT operator-readable explanation.  NULL
            acceptable; no boundary enforcement (TEXT is unbounded).

    Returns:
        The BIGSERIAL ``id`` of the newly-inserted log row.

    Raises:
        ValueError: validation failure (action / decided_by /
            confidence domain errors) -- surfaced before SQL.
        TypeError: confidence is non-Decimal non-None (CLAUDE.md
            Critical Pattern #1 enforcement).
        psycopg2.errors.ForeignKeyViolation: link_id /
            canonical_event_id / platform_event_id / algorithm_id /
            prior_link_id references a non-existent row.
        psycopg2.errors.CheckViolation: should not occur in practice
            (CRUD validation precedes DDL CHECK), but surfaces if
            the DDL CHECK and the Python constant drift apart
            (Pattern 73 SSOT failure mode).

    Example:
        >>> log_id = append_event_match_log_row(
        ...     action="create",
        ...     decided_by="service:matcher:v1",
        ...     algorithm_id=get_event_matcher_algorithm_id(),
        ...     canonical_event_id=42,
        ...     link_id=17,
        ...     platform_event_id=89,
        ...     confidence=Decimal("0.987"),
        ...     features={"source": "natural_key_v1", "match_score": 0.987},
        ...     note="initial match",
        ... )

    Reference:
        - Migration 0091 (table DDL + CHECK constraints)
        - ``constants.py`` ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``
          + ``DECIDED_BY_PREFIXES``
        - Build spec § File 2 (CRUD API surface specification)
        - Slot 0073 ``crud_canonical_match_log.append_match_log_row()``
          (sister-module style + validation reference)
    """
    # Validate BEFORE opening the cursor so callers see a clear
    # validation message rather than a CheckViolation from psycopg2.
    # The cursor-aware variant re-runs validation as defense-in-depth.
    _validate_append_event_match_log_args(
        action=action, confidence=confidence, decided_by=decided_by
    )

    with get_cursor(commit=True) as cur:
        return append_event_match_log_row_in_cursor(
            cur,
            action=action,
            decided_by=decided_by,
            algorithm_id=algorithm_id,
            canonical_event_id=canonical_event_id,
            link_id=link_id,
            platform_event_id=platform_event_id,
            confidence=confidence,
            features=features,
            prior_link_id=prior_link_id,
            note=note,
        )


def _validate_append_event_match_log_args(
    *,
    action: str,
    confidence: Decimal | None,
    decided_by: str,
) -> None:
    """Pattern 73 SSOT + boundary + Decimal-Pattern-#1 validation for log-append args.

    Extracted so both ``append_event_match_log_row`` (validates BEFORE
    opening its own cursor) AND ``append_event_match_log_row_in_cursor``
    (validates as defense-in-depth for matcher transaction-spanning
    callers) can share the same canonical validation logic.

    Centralizing the validation here keeps the public function's
    "no SQL on validation failure" contract intact while letting the
    cursor-aware variant validate without re-implementing the rules.
    Mirrors slot 0073's ``_validate_append_match_log_args`` shape.
    """
    # action must be in the canonical 6-value vocabulary.  Pattern 73 SSOT.
    if action not in CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES:
        raise ValueError(
            f"action {action!r} not in canonical "
            f"CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES "
            f"{CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    # decided_by prefix discipline: must start with one of the canonical
    # actor-taxonomy prefixes from constants.py:DECIDED_BY_PREFIXES.  CHECK
    # cannot enforce string format, so this is the discipline.
    if not any(decided_by.startswith(p) for p in DECIDED_BY_PREFIXES):
        raise ValueError(
            f"decided_by {decided_by!r} must start with one of "
            f"DECIDED_BY_PREFIXES {DECIDED_BY_PREFIXES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    # decided_by length boundary per slot 0073 #1085 finding #3 inheritance.
    if len(decided_by) > _DECIDED_BY_MAX_LENGTH:
        raise ValueError(
            f"decided_by length {len(decided_by)} exceeds "
            f"VARCHAR({_DECIDED_BY_MAX_LENGTH}) column boundary; got {decided_by!r}"
        )

    # confidence bound check -- CRUD-layer parity with DDL CHECK constraint.
    # CLAUDE.md Critical Pattern #1 (Decimal Precision) enforced at the CRUD
    # boundary: float silently satisfies >= 0 and <= 1, so explicit
    # isinstance guard surfaces the type violation as TypeError before any
    # value-level check runs (slot 0073 Ripley P1 sentinel finding).
    # Decimal('NaN') silently passes >= and <= comparisons, so explicit guard.
    if confidence is not None:
        if not isinstance(confidence, Decimal):
            raise TypeError(
                f"confidence must be Decimal or None per CLAUDE.md Critical "
                f"Pattern #1 (no float in probability paths); got "
                f"{type(confidence).__name__}={confidence!r}"
            )
        if confidence.is_nan():
            raise ValueError(f"confidence must not be Decimal('NaN'); got {confidence!r}")
        if confidence < Decimal("0") or confidence > Decimal("1"):
            raise ValueError(f"confidence must be in [0, 1] or None; got {confidence!r}")


def append_event_match_log_row_in_cursor(
    cur: Any,
    *,
    action: str,
    decided_by: str,
    algorithm_id: int,
    canonical_event_id: int | None = None,
    link_id: int | None = None,
    platform_event_id: int | None = None,
    confidence: Decimal | None = None,
    features: dict[str, Any] | None = None,
    prior_link_id: int | None = None,
    note: str | None = None,
) -> int:
    """Cursor-aware variant of ``append_event_match_log_row`` -- caller owns transaction.

    Used by the matcher (Cohort 5+ Slot B) which writes canonical_events
    + canonical_event_links + canonical_event_phase_log +
    canonical_event_match_log + UPDATEs games.canonical_event_id +
    game_states.canonical_event_id atomically in a single transaction
    per ADR-118 V2.44 atomicity contract.

    Performs identical validation to ``append_event_match_log_row()``
    via the shared ``_validate_append_event_match_log_args`` helper;
    does NOT open a cursor and does NOT commit.

    Args:
        cur: psycopg2 cursor under an active transaction (the caller's
            ``with get_cursor(commit=True)`` block).
        (remaining args identical to ``append_event_match_log_row``)

    Returns:
        The BIGSERIAL ``id`` of the newly-inserted log row.

    Raises:
        ValueError / TypeError: validation failures identical to
            ``append_event_match_log_row``.
    """
    # Defense-in-depth: validate every time the cursor-aware path is
    # invoked, even though the public ``append_event_match_log_row``
    # already validated before opening its cursor.  Matcher callers
    # reach this path directly; the validation cost is trivial and
    # surfaces drift early.
    _validate_append_event_match_log_args(
        action=action, confidence=confidence, decided_by=decided_by
    )

    # features dict is serialized to JSON text and cast to JSONB at the SQL
    # layer (`%s::jsonb` in the query below).  Going through json.dumps
    # gives us deterministic encoding regardless of psycopg2 adapter
    # registration state, which keeps the test mocks simple.
    features_param: str | None = None if features is None else json.dumps(features)

    query = """
        INSERT INTO canonical_event_match_log (
            canonical_event_id, link_id, platform_event_id, action,
            confidence, algorithm_id, features, prior_link_id, decided_by,
            note
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s::jsonb, %s, %s,
            %s
        )
        RETURNING id
    """
    cur.execute(
        query,
        (
            canonical_event_id,
            link_id,
            platform_event_id,
            action,
            confidence,
            algorithm_id,
            features_param,
            prior_link_id,
            decided_by,
            note,
        ),
    )
    row = cur.fetchone()
    return cast("int", row["id"])


# =============================================================================
# CANONICAL EVENT MATCH LOG -- READ OPERATIONS
# =============================================================================


def get_event_match_log_for_canonical_event(
    canonical_event_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get the canonical_event_match_log rows for a canonical event, newest-first.

    This is the operator audit hot path -- when an operator asks
    "what's the decision history for this canonical event?", they call
    this function.  Returns rows in descending ``decided_at`` order
    (most-recent first); the underlying
    ``idx_canonical_event_match_log_decided_at`` index +
    ``idx_canonical_event_match_log_canonical_event_id`` partial index
    keep this O(log n) + limit-bounded regardless of total log-table
    size.

    Filters by ``canonical_event_id``.  Rows whose canonical_event_id
    was SET NULL by a canonical_events DELETE (rare; mostly test
    cleanup paths) are NOT returned by this query -- use
    ``get_event_match_log_by_action()`` to find historical orphans by
    action discriminator + time window.

    Args:
        canonical_event_id: BIGINT target into ``canonical_events.id``.
        limit: Maximum rows returned.  Defaults to 50.

    Returns:
        List of full row dicts (possibly empty), ordered by
        ``decided_at DESC``.  Keys: id, canonical_event_id, link_id,
        platform_event_id, action, confidence, algorithm_id, features,
        prior_link_id, decided_by, decided_at, note, created_at.

    Example:
        >>> rows = get_event_match_log_for_canonical_event(42)
        >>> latest = rows[0] if rows else None
        >>> if latest:
        ...     print(f"Last action: {latest['action']} by {latest['decided_by']}")

    Reference:
        - Migration 0091 (idx_canonical_event_match_log_decided_at +
          idx_canonical_event_match_log_canonical_event_id)
    """
    query = """
        SELECT id, canonical_event_id, link_id, platform_event_id,
               action, confidence, algorithm_id, features, prior_link_id,
               decided_by, decided_at, note, created_at
        FROM canonical_event_match_log
        WHERE canonical_event_id = %s
        ORDER BY decided_at DESC
        LIMIT %s
    """
    return fetch_all(query, (canonical_event_id, limit))


def get_event_match_log_by_action(
    action: str,
    since: Any,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get canonical_event_match_log rows by action discriminator + time window.

    Alert-query support: surfaces rows of a given ``action`` since a
    timestamp.  Useful for matcher operator runbook flows ("how many
    create actions in the last hour?", "any quarantine actions since
    last operator handoff?").

    Pattern 73 SSOT real-guard validation: ``action`` is checked
    against ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` before SQL,
    raising ``ValueError`` for unknown values.  This catches typos at
    the read path the same way the write path does.

    Args:
        action: VARCHAR(16) discriminator.  MUST be in
            ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``.
        since: TIMESTAMPTZ lower bound (inclusive).  Pass a
            ``datetime.datetime`` or any psycopg2-compatible timestamp.
        limit: Maximum rows returned.  Defaults to 100.

    Returns:
        List of full row dicts (possibly empty), ordered by
        ``decided_at DESC``.

    Raises:
        ValueError: ``action`` not in
            ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` (Pattern 73
            SSOT real-guard validation).

    Example:
        >>> from datetime import datetime, timedelta, UTC
        >>> hour_ago = datetime.now(UTC) - timedelta(hours=1)
        >>> recent_creates = get_event_match_log_by_action("create", hour_ago)

    Reference:
        - constants.py ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``
        - Migration 0091 (idx_canonical_event_match_log_action)
    """
    if action not in CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES:
        raise ValueError(
            f"action {action!r} not in canonical "
            f"CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES "
            f"{CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    query = """
        SELECT id, canonical_event_id, link_id, platform_event_id,
               action, confidence, algorithm_id, features, prior_link_id,
               decided_by, decided_at, note, created_at
        FROM canonical_event_match_log
        WHERE action = %s
          AND decided_at >= %s
        ORDER BY decided_at DESC
        LIMIT %s
    """
    return fetch_all(query, (action, since, limit))


def get_event_match_log_row_by_id(log_id: int) -> dict[str, Any] | None:
    """Get a single canonical_event_match_log row by surrogate PK.

    Used by operator runbook flows that have a log_id from another
    query (e.g., a quarantine alert's note text references a specific
    log row) and want the full row for context.

    Args:
        log_id: BIGSERIAL surrogate PK from
            ``canonical_event_match_log.id``.

    Returns:
        Full row dict if found, ``None`` otherwise.  Keys match
        ``get_event_match_log_for_canonical_event``.

    Example:
        >>> row = get_event_match_log_row_by_id(123)
        >>> if row:
        ...     print(row["action"], row["decided_by"], row["note"])

    Reference:
        - Migration 0091 (canonical_event_match_log_pkey)
    """
    query = """
        SELECT id, canonical_event_id, link_id, platform_event_id,
               action, confidence, algorithm_id, features, prior_link_id,
               decided_by, decided_at, note, created_at
        FROM canonical_event_match_log
        WHERE id = %s
    """
    return fetch_one(query, (log_id,))


# =============================================================================
# Sentinel: CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES + DECIDED_BY_PREFIXES are
# imported and USED above in real-guard ``ValueError``-raising validation
# (append_event_match_log_row + get_event_match_log_by_action).  If a future
# refactor drops the validation, the imports become unused and ruff (F401)
# will fire -- closing the side-effect-only-import drift surface that #1085
# finding #2 strengthening prevents.  This is the canonical strengthening of
# slot-0072's LINK_STATE_VALUES side-effect-only-import convention.
# =============================================================================
