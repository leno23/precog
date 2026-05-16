"""CRUD operations for canonical_event_links.

Cohort 3 of the canonical-layer foundation (ADR-118 v2.41 amendment, session
78 capture, Cohort 3 amendment + session 80 S82 design-stage P41 council).
Sister module to ``crud_canonical_events.py`` and
``crud_canonical_market_links.py``; uses the same raw-psycopg2 +
``get_cursor`` / ``fetch_one`` + RealDictCursor + heavy-docstring conventions.

Tables covered:
    - ``canonical_event_links`` (Migration 0072) — bridges the canonical
      event identity tier (``canonical_events``) to the platform-tier
      ``events`` table under a state machine governed by ``link_state IN
      ('active','retired','quarantined')``.  See Migration 0072 docstring
      for the full DDL rationale and the Cohort 3 amendment decisions.

This module is the **structural parallel** of ``crud_canonical_market_links.py``
per L12-L13 (parallelism IS the contract).  Same public surface, same
docstring template, same Phase 1 deliberate gaps; only column names + table
name + FK target differ.

Pattern 14 5-step bundle status:
    Step 3b of 5 for the Cohort 3 slot-0072 bundle (sibling to step 3a =
    ``crud_canonical_market_links.py``).  See that module's docstring for
    the full bundle status; this module's status mirrors it.

Phase 1 surface (slot 0072 -- deliberately minimal):
    Read + retire helpers only.

Cohort 5+ Slot B surface (Migration 0091 + canonical_event_matcher):
    Adds ``create_link()`` + ``create_link_in_cursor()`` for the matcher's
    atomic transaction-spanning two-table-write (canonical_events INSERT
    + canonical_event_links INSERT + canonical_event_match_log INSERT
    in a single transaction per ADR-118 V2.44 atomicity contract).

    Slot 0072's deliberate absence of ``create_link()`` (per build spec
    § 8 step 3b at the time: "adding a thin create_link() here would
    tempt callers into single-table writes that bypass the audit-log
    invariant") is RESOLVED by slot B's pairing of ``create_link()``
    with the matcher's atomic transaction discipline -- the matcher
    NEVER calls ``create_link()`` without also calling
    ``crud_canonical_event_match_log.append_event_match_log_row_in_cursor()``
    in the same transaction.  Direct callers of ``create_link()``
    outside the matcher are policy-discouraged (see § 6 V2.44 atomicity
    contract below).

V2.44 atomicity contract (parent spec § Per-Q Adjudications Q4 +
session-92 council convergence):

    The matcher's steady-state write path is a single transaction:

        1. INSERT canonical_events (existing CRUD)
        2. INSERT canonical_event_links via ``create_link_in_cursor()`` (NEW)
        3. INSERT canonical_event_phase_log (auto-trigger from step 1)
        4. INSERT canonical_event_match_log via
           ``append_event_match_log_row_in_cursor()`` (slot B CRUD)
        5. UPDATE games.canonical_event_id (if applicable)
        6. UPDATE game_states.canonical_event_id (if applicable)
        7. COMMIT (atomically)

    Either ALL writes commit or ALL roll back; no partial-state
    failures.  This is the integrity-defining contract that the
    matcher's audit ledger is built on.

UPDATE coverage (Cohort 3 deliberate gap):
    Mirrors ``crud_canonical_market_links.py`` discipline — only
    ``retire_link``; no general ``update_link()`` helper.  Re-tuning a
    link = retire-then-create-new (Critical Pattern #6 inheritance).

Pattern 73 SSOT discipline:
    The ``link_state`` value vocabulary lives at
    ``src/precog/database/constants.py`` ``LINK_STATE_VALUES``.  This
    module imports from there; it does NOT hardcode the strings.  The
    same constant covers BOTH ``canonical_market_links.link_state`` AND
    ``canonical_event_links.link_state`` — single vocabulary, two
    enforcement sites.

Reference:
    - ``docs/foundation/ARCHITECTURE_DECISIONS_V2.42.md`` lines ~17707-17717
      (Cohort 3 amendment + DDL + decision rationale)
    - ``src/precog/database/alembic/versions/0072_canonical_link_tables.py``
    - ``src/precog/database/crud_canonical_market_links.py`` (parallel
      module, structural template)
    - ``src/precog/database/constants.py`` ``LINK_STATE_VALUES``
"""

import logging
from decimal import Decimal
from typing import Any, cast

from .connection import fetch_all, fetch_one, get_cursor
from .constants import DECIDED_BY_PREFIXES, LINK_STATE_VALUES

logger = logging.getLogger(__name__)


# Maximum allowed length for ``decided_by`` matches the DDL column boundary
# (``VARCHAR(64)``).  Mirrors slot 0073's ``_DECIDED_BY_MAX_LENGTH`` shape.
_DECIDED_BY_MAX_LENGTH = 64


# =============================================================================
# CANONICAL EVENT LINKS — READ OPERATIONS
# =============================================================================


def get_active_link_for_platform_event(
    platform_event_id: int,
) -> dict[str, Any] | None:
    """
    Get the (at-most-one) active canonical_event_links row for a platform event.

    Canonical lookup for cross-platform identity resolution at the event
    tier: when a downstream consumer asks "what's the canonical event for
    this platform-tier event row?", they call this function.  The
    ``canonical_event_links.uq_canonical_event_links_active`` partial EXCLUDE
    constraint guarantees at most one ``active`` link per ``platform_event_id``,
    so this function returns either a single row or ``None`` — never multiple.

    Args:
        platform_event_id: Integer FK target into the platform ``events``
            table (``events.id``).

    Returns:
        Full ``canonical_event_links`` row dict if an active link exists,
        ``None`` if no active link is found (either no link at all, or all
        existing links are in ``retired`` / ``quarantined`` state).
        Keys: id, canonical_event_id, platform_event_id, link_state,
            confidence, algorithm_id, decided_by, decided_at, retired_at,
            retire_reason, created_at, updated_at

    Example:
        >>> link = get_active_link_for_platform_event(platform_event_id=42)
        >>> if link:
        ...     print(f"Canonical event id={link['canonical_event_id']}")
        ... else:
        ...     print("No active canonical link for this platform event")

    Educational Note:
        Same load-bearing partial-index reasoning as
        ``crud_canonical_market_links.get_active_link_for_platform_market`` —
        the ``link_state = 'active'`` filter + EXCLUDE constraint's index
        is the cheapest path AND ensures at-most-one semantics.

    Reference:
        - Migration 0072 (table DDL + EXCLUDE constraint)
        - ``crud_canonical_market_links.get_active_link_for_platform_market()``
          (parallel helper for the market tier)
        - ADR-118 v2.41 amendment Cohort 3 design council L6 + L7 + L13
    """
    query = """
        SELECT id, canonical_event_id, platform_event_id, link_state,
               confidence, algorithm_id, decided_by, decided_at, retired_at,
               retire_reason, created_at, updated_at
        FROM canonical_event_links
        WHERE platform_event_id = %s
          AND link_state = 'active'
    """
    return fetch_one(query, (platform_event_id,))


def get_link_by_id(link_id: int) -> dict[str, Any] | None:
    """
    Get a canonical_event_links row by its surrogate integer PK.

    Args:
        link_id: BIGSERIAL surrogate PK from ``canonical_event_links.id``.

    Returns:
        Full row dict if found, ``None`` otherwise.  Same keys as
        ``get_active_link_for_platform_event``.

    Example:
        >>> row = get_link_by_id(7)
        >>> if row:
        ...     print(row["link_state"])  # 'active' / 'retired' / 'quarantined'

    Educational Note:
        Lookup by surrogate PK is the cheapest path (single B-tree probe on
        the primary key).  Used primarily by the slot-0073
        ``canonical_match_log.link_id`` JOIN path — the SET NULL ON DELETE
        rule per ADR-118 v2.42 sub-amendment B means historical match-log
        rows may carry ``link_id IS NULL`` after the underlying link CASCADE-
        deleted; callers must handle the ``None`` return.

    Reference:
        - Migration 0072 (table DDL)
        - ``crud_canonical_market_links.get_link_by_id()`` (parallel helper)
    """
    query = """
        SELECT id, canonical_event_id, platform_event_id, link_state,
               confidence, algorithm_id, decided_by, decided_at, retired_at,
               retire_reason, created_at, updated_at
        FROM canonical_event_links
        WHERE id = %s
    """
    return fetch_one(query, (link_id,))


def list_links_for_canonical_event(
    canonical_event_id: int,
) -> list[dict[str, Any]]:
    """
    List all canonical_event_links rows for a given canonical event.

    Returns links in any state (active / retired / quarantined).  Order is
    by ``decided_at DESC`` so the most-recently-decided link surfaces first
    — useful for operator review flows that want "the current state plus
    historical context for this canonical event".

    Args:
        canonical_event_id: BIGSERIAL surrogate PK from ``canonical_events.id``.

    Returns:
        List of full row dicts (possibly empty).  Same keys as
        ``get_active_link_for_platform_event``.

    Example:
        >>> links = list_links_for_canonical_event(canonical_event_id=42)
        >>> platforms_seen = {l["platform_event_id"] for l in links}
        >>> print(f"Canonical event 42 has linked from {len(platforms_seen)} platform rows")

    Educational Note:
        Returns ALL link states because operator review flows need history.
        Hot path for the Miles operator-alert-query "stale active links" —
        filter results client-side by ``link_state == 'active'`` and
        ``decided_at < cutoff``.

        The ``idx_canonical_event_links_canonical_event_id`` index makes
        this lookup O(log n) regardless of total link-table size.

    Reference:
        - Migration 0072 (idx_canonical_event_links_canonical_event_id)
        - ADR-118 v2.41 amendment Cohort 3 alert-query catalog (Miles
          consideration #4)
    """
    query = """
        SELECT id, canonical_event_id, platform_event_id, link_state,
               confidence, algorithm_id, decided_by, decided_at, retired_at,
               retire_reason, created_at, updated_at
        FROM canonical_event_links
        WHERE canonical_event_id = %s
        ORDER BY decided_at DESC
    """
    return fetch_all(query, (canonical_event_id,))


# =============================================================================
# CANONICAL EVENT LINKS — CREATE OPERATION (Slot B, Cohort 5+)
# =============================================================================


def create_link(
    *,
    canonical_event_id: int,
    platform_event_id: int,
    confidence: Decimal,
    algorithm_id: int,
    decided_by: str,
    link_state: str = "active",
) -> int:
    """Create a new canonical_event_links row.  Slot B (Cohort 5+) write path.

    Cohort 5+ Slot B (Migration 0091 + canonical_event_matcher) writer
    helper.  The matcher's atomic transaction uses
    ``create_link_in_cursor()`` (cursor-aware sibling); standalone
    callers (operator-driven fixture flows; tests) use this function.

    Enforces ``uq_canonical_event_links_active`` EXCLUDE constraint at
    INSERT time (one ``active`` link per ``platform_event_id``).
    Callers MUST handle ``psycopg2.errors.UniqueViolation`` /
    ``ExclusionViolation`` and decide whether to retire the prior link
    first (matcher's typical flow: retire-then-insert in the same
    transaction).

    Args:
        canonical_event_id: BIGSERIAL FK into ``canonical_events.id``.
            NOT NULL.  Caller must have already INSERTed the
            canonical_events row.
        platform_event_id: INTEGER FK into ``platform_events.id``.
            NOT NULL.
        confidence: NUMERIC(4,3) in [0, 1].  Decimal-only per
            CLAUDE.md Critical Pattern #1.  Algorithm-derived match
            confidence; operator overrides typically use Decimal('1.0').
        algorithm_id: BIGINT FK into ``match_algorithm.id``.  NOT
            NULL.  Matcher writes use ``event_matcher_v1.id``
            (resolved via
            ``crud_canonical_event_match_log.get_event_matcher_algorithm_id()``);
            operator overrides use ``manual_v1.id`` (slot 0073
            ``get_manual_v1_algorithm_id()``).
        decided_by: VARCHAR(64) NOT NULL actor attribution.  MUST
            start with one of ``DECIDED_BY_PREFIXES``; MUST be <= 64
            chars.
        link_state: VARCHAR(16) state; defaults to ``'active'``.
            MUST be in ``LINK_STATE_VALUES``.  Direct caller-supplied
            non-active states (``'retired'`` / ``'quarantined'``) are
            unusual but supported for the matcher's restore-and-
            quarantine flow.

    Returns:
        The BIGSERIAL ``id`` of the newly-inserted link row.

    Raises:
        ValueError: validation failure (decided_by / link_state /
            confidence domain errors).
        TypeError: confidence is non-Decimal (CLAUDE.md Critical
            Pattern #1 enforcement).
        psycopg2.errors.ExclusionViolation: ``uq_canonical_event_links_active``
            EXCLUDE fires -- another active link already exists for
            this ``platform_event_id``.  Caller must retire the prior
            link first.
        psycopg2.errors.ForeignKeyViolation: canonical_event_id /
            platform_event_id / algorithm_id references a non-existent
            row.
        psycopg2.errors.CheckViolation: confidence outside [0, 1] OR
            link_state outside the canonical enum (Pattern 73 SSOT
            failure mode if CRUD validation drifted from DDL CHECK).

    Example:
        >>> link_id = create_link(
        ...     canonical_event_id=42,
        ...     platform_event_id=89,
        ...     confidence=Decimal("0.987"),
        ...     algorithm_id=2,  # event_matcher_v1
        ...     decided_by="service:matcher:v1",
        ... )

    Educational Note:
        For the matcher's atomic transaction-spanning path (V2.44
        atomicity contract), use ``create_link_in_cursor()`` -- it
        does NOT commit, allowing the caller to bundle the link INSERT
        with canonical_events INSERT + canonical_event_match_log
        INSERT in a single transaction.

    Reference:
        - Migration 0072 (table DDL + EXCLUDE constraint)
        - Migration 0091 (Slot B matcher infrastructure)
        - ``create_link_in_cursor()`` (cursor-aware sibling for V2.44
          atomic flows)
        - Slot 0073 + 0074 link-table CRUD discipline precedent
    """
    _validate_create_link_args(decided_by=decided_by, link_state=link_state, confidence=confidence)
    with get_cursor(commit=True) as cur:
        return create_link_in_cursor(
            cur,
            canonical_event_id=canonical_event_id,
            platform_event_id=platform_event_id,
            confidence=confidence,
            algorithm_id=algorithm_id,
            decided_by=decided_by,
            link_state=link_state,
        )


def _validate_create_link_args(
    *,
    decided_by: str,
    link_state: str,
    confidence: Decimal,
) -> None:
    """Pattern 73 SSOT + boundary + Decimal-Pattern-#1 validation for create_link args.

    Mirrors slot 0073's _validate_append_match_log_args shape adapted
    for the link-INSERT path.  Extracted so both ``create_link``
    (validates BEFORE opening its own cursor) AND
    ``create_link_in_cursor`` (validates as defense-in-depth) share
    the same canonical validation logic.
    """
    # link_state must be in canonical 3-value vocabulary.  Pattern 73 SSOT.
    if link_state not in LINK_STATE_VALUES:
        raise ValueError(
            f"link_state {link_state!r} not in canonical LINK_STATE_VALUES "
            f"{LINK_STATE_VALUES!r}; pattern 73 SSOT vocabulary violation"
        )

    # decided_by prefix discipline (slot 0073 inheritance).
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

    # confidence bound check + Decimal-Pattern-#1 enforcement.
    # Unlike canonical_event_match_log.confidence (NULLABLE), the link
    # table's confidence is NOT NULL -- pass-through required.
    if not isinstance(confidence, Decimal):
        raise TypeError(
            f"confidence must be Decimal per CLAUDE.md Critical Pattern #1 "
            f"(no float in probability paths); got "
            f"{type(confidence).__name__}={confidence!r}"
        )
    if confidence.is_nan():
        raise ValueError(f"confidence must not be Decimal('NaN'); got {confidence!r}")
    if confidence < Decimal("0") or confidence > Decimal("1"):
        raise ValueError(f"confidence must be in [0, 1]; got {confidence!r}")


def create_link_in_cursor(
    cur: Any,
    *,
    canonical_event_id: int,
    platform_event_id: int,
    confidence: Decimal,
    algorithm_id: int,
    decided_by: str,
    link_state: str = "active",
) -> int:
    """Cursor-aware variant of ``create_link`` -- caller owns the transaction.

    Used by the canonical-event matcher (Cohort 5+ Slot B) inside its
    atomic two-table-write transaction (V2.44 atomicity contract):

        BEGIN;
          INSERT INTO canonical_events ... RETURNING id;
          create_link_in_cursor(cur, canonical_event_id=<just-inserted>, ...);
          append_event_match_log_row_in_cursor(cur, action='create', ...);
          UPDATE games SET canonical_event_id = ... WHERE id = <game_id>;
          UPDATE game_states SET canonical_event_id = ... WHERE game_id = <game_id>;
        COMMIT;

    Performs identical validation to ``create_link()`` via
    ``_validate_create_link_args``; does NOT open a cursor and does
    NOT commit.

    Args:
        cur: psycopg2 cursor under an active transaction (the caller's
            ``with get_cursor(commit=True)`` block).
        (remaining args identical to ``create_link``)

    Returns:
        The BIGSERIAL ``id`` of the newly-inserted link row.

    Raises:
        ValueError / TypeError: validation failures identical to
            ``create_link``.
        psycopg2.errors.ExclusionViolation /
            psycopg2.errors.ForeignKeyViolation: SQL-layer integrity
            failures.
    """
    # Defense-in-depth validation; mirrors slot 0073 cursor-aware variant.
    _validate_create_link_args(decided_by=decided_by, link_state=link_state, confidence=confidence)

    query = """
        INSERT INTO canonical_event_links (
            canonical_event_id, platform_event_id, link_state,
            confidence, algorithm_id, decided_by
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s
        )
        RETURNING id
    """
    cur.execute(
        query,
        (
            canonical_event_id,
            platform_event_id,
            link_state,
            confidence,
            algorithm_id,
            decided_by,
        ),
    )
    row = cur.fetchone()
    return cast("int", row["id"])


# =============================================================================
# CANONICAL EVENT LINKS — RETIRE OPERATION
# =============================================================================


def retire_link(link_id: int, retire_reason: str | None = None) -> bool:
    """
    Retire a canonical_event_links row.

    Sets ``link_state = 'retired'``, ``retired_at = now()``, and
    ``retire_reason = <provided>``.  Canonical retirement path — callers
    must NOT write ad-hoc UPDATE SQL touching these columns (Pattern 73
    violation; consumers would drift).

    Args:
        link_id: BIGSERIAL surrogate PK from ``canonical_event_links.id``.
        retire_reason: Optional operator-readable rationale stored in the
            ``retire_reason VARCHAR(64)`` column.  Examples per build spec § 4:
            ``'platform_delisted'``, ``'algorithm_corrected'``,
            ``'duplicate_canonical'``.  Free-text NULL acceptable for Phase
            1 callers.

    Returns:
        ``True`` if a row was retired (matched and updated), ``False`` if
        no row matched the given id.

    Example:
        >>> if retire_link(7, retire_reason="platform_delisted"):
        ...     print("Link 7 retired")
        ... else:
        ...     print("Link 7 not found")

    Educational Note:
        Same ``updated_at`` trigger reasoning as
        ``crud_canonical_market_links.retire_link`` — the
        ``trg_canonical_event_links_updated_at`` BEFORE UPDATE trigger
        refreshes ``updated_at`` automatically.

        After retiring an active link, the EXCLUDE partial-active constraint
        is satisfied for that ``platform_event_id`` and a fresh ``active``
        link can be inserted.  Matcher pattern: retire old, insert new
        (atomic via the slot-0073 two-table-write wrapper).

        Idempotent in effect: retiring an already-retired row simply
        refreshes ``retired_at`` and overwrites ``retire_reason``.

    Reference:
        - Migration 0072 (table DDL + BEFORE UPDATE trigger)
        - ``crud_canonical_market_links.retire_link()`` (parallel helper)
        - ADR-118 v2.41 amendment Cohort 3 design council L6 (link_state
          state machine)
    """
    query = """
        UPDATE canonical_event_links
        SET link_state = 'retired',
            retired_at = now(),
            retire_reason = %s
        WHERE id = %s
    """
    with get_cursor(commit=True) as cur:
        cur.execute(query, (retire_reason, link_id))
        return cast("bool", cur.rowcount > 0)
