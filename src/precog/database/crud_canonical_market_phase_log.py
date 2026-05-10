"""CRUD operations for canonical_market_phase_log -- THE market-phase audit ledger.

Cleanup epic Slot 4 (Migration 0088 R3 + R6 council unanimous).
Mirror of ``crud_canonical_event_phase_log.py`` (slot 0079 sister module);
uses the same raw-psycopg2 + ``get_cursor`` / ``RealDictCursor`` +
heavy-docstring conventions.

Tables covered:
    - ``canonical_market_phase_log`` (Migration 0088) -- append-only audit
      ledger for ``canonical_markets.lifecycle_phase`` transitions.  Most
      rows are written by the AFTER INSERT OR UPDATE OF lifecycle_phase
      trigger ``trg_canonical_markets_log_phase_transition`` (changed_by=
      'system:trigger'); operator-driven manual phase corrections that
      bypass the canonical_markets UPDATE path use
      ``append_market_phase_transition()``.

THE RESTRICTED API SURFACE -- APPEND-ONLY VIA APPLICATION DISCIPLINE:

    This module exposes EXACTLY ONE write function:
    ``append_market_phase_transition()``.  There are NO ``update_*``
    functions, NO ``delete_*`` functions, NO ``upsert_*`` functions.  This
    is by design: the audit ledger is append-only, and the discipline lives
    in this module's API surface (Migration 0088 docstring + slot 0079
    inheritance).

    The trigger-enforced version (BEFORE UPDATE/DELETE -> RAISE EXCEPTION)
    is queued for a future cohort after a 30-day production soak validates
    that the application-discipline approach is sufficient (slot 0073 +
    slot 0079 precedent inheritance).  Until then:

        - DO NOT add ``update_*`` / ``delete_*`` / ``upsert_*`` helpers
          to this module without an ADR amendment.
        - DO NOT write ad-hoc ``UPDATE canonical_market_phase_log`` /
          ``DELETE FROM canonical_market_phase_log`` SQL anywhere outside
          the slot-0088 migration's downgrade (Pattern 73 violation --
          consumers would drift; future S81 grep audits will sweep for
          this).
        - The ``append_market_phase_transition()`` function is the ONLY
          sanctioned MANUAL write path.  The trigger-driven path
          (system:trigger rows from canonical_markets INSERT/UPDATE) is
          also sanctioned and coordinates correctness via the trigger
          function body.

System-driven vs. operator-driven write paths:

    System-driven (the common case):
        Every INSERT / UPDATE OF lifecycle_phase on ``canonical_markets``
        invokes ``trg_canonical_markets_log_phase_transition`` which
        INSERTs a row with ``changed_by='system:trigger'``.  This is the
        primary audit-stream populator; operators do not call into this
        module for the common case.

    Operator-driven (rare):
        Manual phase corrections that bypass the canonical_markets UPDATE
        path -- e.g., correcting a misclassified phase by directly
        appending an audit row WITHOUT actually changing
        canonical_markets.lifecycle_phase.  Use case: a previously-emitted
        phase-log row was wrong because the operator's earlier UPDATE
        used the wrong phase, and the operator wants to record a
        correction in the audit stream.  The function does NOT itself
        update canonical_markets; it only writes the audit row.

Pattern 73 SSOT discipline (CLAUDE.md Critical Pattern #8):

    Two vocabularies are SSOT-anchored at
    ``src/precog/database/constants.py``:

        ``CANONICAL_MARKET_LIFECYCLE_PHASES``  -- 5-value lifecycle_phase
                                                  vocabulary mirrored by
                                                  (a) Migration 0088's
                                                  canonical_markets.lifecycle_phase
                                                  CHECK and (b) Migration
                                                  0088's two CHECKs on
                                                  ``new_phase`` and
                                                  ``previous_phase``.
                                                  This module imports
                                                  the constant and uses
                                                  it in real-guard
                                                  ``ValueError``-raising
                                                  validation in
                                                  ``append_market_phase_transition()``.

        ``DECIDED_BY_PREFIXES``                 -- 3-prefix actor taxonomy
                                                  (``human:`` / ``service:`` /
                                                  ``system:``).  Reused
                                                  from slot 0073/0079 per
                                                  Pattern 73 SSOT
                                                  discipline.  The
                                                  trigger emits
                                                  'system:trigger'
                                                  (system: prefix);
                                                  manual operator paths
                                                  via this CRUD use
                                                  'human:<username>' or
                                                  other prefix-matched
                                                  values.

    Both ``CANONICAL_MARKET_LIFECYCLE_PHASES`` and ``DECIDED_BY_PREFIXES``
    are imported and USED in real-guard ``ValueError``-raising validation
    (slot 0073/0079 inheritance per #1085 finding #2 strengthening).

L33 dedicated CRUD module restriction:

    All MANUAL write paths to canonical_market_phase_log MUST go through
    this module's ``append_market_phase_transition()`` function.  There
    is NO direct-SQL escape hatch in the public API; future call sites
    adding ad-hoc INSERT SQL bypass the validation layer (Pattern 73 SSOT
    vocabulary checks + changed_by length bound + changed_by prefix
    discipline) and represent a Pattern 73 violation.  S81 grep audits
    sweep for direct INSERT into canonical_market_phase_log outside this
    module + the slot-0088 migration trigger function.

Reference:
    - ``docs/foundation/ARCHITECTURE_DECISIONS.md`` ADR-118 V2.46 head;
      V2.47 ships at Slot 5 (session 99)
    - ``src/precog/database/alembic/versions/0088_canonical_lifecycle_phase_redistribution.py``
    - ``src/precog/database/crud_canonical_event_phase_log.py`` (sister
      module; slot 0079; mirror template per build spec § 0d D-3)
    - ``src/precog/database/constants.py`` ``CANONICAL_MARKET_LIFECYCLE_PHASES``
      + ``DECIDED_BY_PREFIXES``
    - ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md``
      (binding build spec)
    - ``memory/design_review_lifecycle_phase_synthesis.md`` (council R6)
"""

import logging
from typing import Any, cast

from .connection import fetch_all, get_cursor
from .constants import CANONICAL_MARKET_LIFECYCLE_PHASES, DECIDED_BY_PREFIXES

logger = logging.getLogger(__name__)


# Maximum allowed length for ``changed_by`` matches the DDL column boundary
# (``VARCHAR(64)``).  Mirrors slot 0073/0079's ``_DECIDED_BY_MAX_LENGTH`` /
# ``_CHANGED_BY_MAX_LENGTH`` shape; surfaced to module scope rather than
# duplicated inside the validation function.
_CHANGED_BY_MAX_LENGTH = 64


# =============================================================================
# CANONICAL MARKET PHASE LOG -- APPEND-ONLY WRITE PATH
# =============================================================================


def append_market_phase_transition(
    *,
    canonical_market_id: int,
    new_phase: str,
    changed_by: str,
    previous_phase: str | None = None,
    note: str | None = None,
) -> int:
    """Append one row to canonical_market_phase_log.  THIS IS THE ONLY SANCTIONED MANUAL WRITE PATH.

    Use case: operator-initiated phase corrections that bypass the normal
    canonical_markets UPDATE path (e.g., correcting a misclassified phase
    in the audit stream without changing canonical_markets.lifecycle_phase
    itself).  System-driven transitions land via the
    ``trg_canonical_markets_log_phase_transition`` trigger and do NOT call
    this function.

    The function performs CRUD-layer validation that complements the DDL
    CHECK constraints (Pattern 73 SSOT real-guard discipline inherited
    from slot 0073/0079):

        - ``new_phase`` MUST be in ``CANONICAL_MARKET_LIFECYCLE_PHASES``
          (Pattern 73 SSOT real-guard validation; raises ``ValueError``
          before SQL).
        - ``previous_phase`` MUST be NULL OR in
          ``CANONICAL_MARKET_LIFECYCLE_PHASES`` (Pattern 73 SSOT).
        - ``changed_by`` MUST start with one of ``DECIDED_BY_PREFIXES``
          (Pattern 73 SSOT real-guard validation).
        - ``len(changed_by)`` MUST be ``<= 64`` (boundary validation per
          slot-0073 #1085 finding #3 inheritance -- surfaces a clear
          ``ValueError`` before psycopg2 raises a generic
          StringDataRightTruncation).
        - ``canonical_market_id`` is passed through unchanged; psycopg2
          surfaces FK violations as ``ForeignKeyViolation``.

    Args:
        canonical_market_id: BIGINT FK into ``canonical_markets.id``.
            NOT NULL.  Validated as int at the type level; FK integrity
            surfaced at the SQL layer.
        new_phase: VARCHAR(32) NOT NULL.  MUST be in
            ``CANONICAL_MARKET_LIFECYCLE_PHASES``
            (open/suspended/settling/resolved/voided).  Always populated;
            even a "correction" audit row has a destination phase.
        changed_by: VARCHAR(64) NOT NULL actor attribution.  MUST start
            with one of ``DECIDED_BY_PREFIXES`` (``human:`` / ``service:``
            / ``system:``); MUST be <= 64 chars.  Operator-driven calls
            typically use ``'human:<username>'``; service-driven calls
            use ``'service:<svc-name>'``.  The trigger emits
            ``'system:trigger'`` and does NOT call this function.
        previous_phase: VARCHAR(32) NULL.  When non-NULL, MUST be in
            ``CANONICAL_MARKET_LIFECYCLE_PHASES``.  NULL allowed for
            corrections that record a fresh-state transition (no
            predecessor in the operator's framing).
        note: Free-text TEXT operator-readable explanation.  NULL
            acceptable; no boundary enforcement (TEXT is unbounded).

    Returns:
        The BIGSERIAL ``id`` of the newly-inserted log row.

    Raises:
        ValueError: validation failure (new_phase / previous_phase /
            changed_by domain or boundary errors) -- surfaced before SQL.
        psycopg2.errors.ForeignKeyViolation: canonical_market_id references
            a non-existent canonical_markets row.
        psycopg2.errors.CheckViolation: should not occur in practice
            (CRUD validation precedes DDL CHECK), but surfaces if the DDL
            CHECK and the Python constant drift apart (Pattern 73 SSOT
            failure mode -- the dedicated SSOT parity test catches this
            shape pre-merge).

    Example:
        >>> log_id = append_market_phase_transition(
        ...     canonical_market_id=42,
        ...     new_phase="settling",
        ...     changed_by="human:eric",
        ...     previous_phase="open",
        ...     note="Manual correction: market locked at announced settlement time",
        ... )

    Educational Note:
        Most phase-log rows arrive via the trigger, not this function.
        Operators reading the audit stream should expect ~99% of rows
        to carry ``changed_by='system:trigger'``; rows with ``human:``
        or ``service:`` prefixes are operator-driven corrections that
        warrant runbook attention (someone manually augmented the audit
        stream).  The note column captures the operator's reasoning.

    Reference:
        - Migration 0088 (table DDL + CHECK constraints + trigger)
        - ``constants.py`` ``CANONICAL_MARKET_LIFECYCLE_PHASES`` +
          ``DECIDED_BY_PREFIXES``
        - Build spec § 0d D-3 (write API mandated)
        - Slot 0079 ``crud_canonical_event_phase_log.append_phase_transition()``
          (sister-module mirror reference)
    """
    # Validate BEFORE opening the cursor so callers see a clear validation
    # message rather than a CheckViolation from psycopg2.  Slot 0073/0079
    # inheritance: same shape as ``_validate_append_phase_transition_args``.
    _validate_append_market_phase_transition_args(
        new_phase=new_phase,
        previous_phase=previous_phase,
        changed_by=changed_by,
    )

    with get_cursor(commit=True) as cur:
        # ---- The append (single INSERT, RETURNING id) -----------------------
        # No UPDATE / DELETE / UPSERT -- this is the ONLY sanctioned manual
        # write path.  No transaction-spanning two-table-write here either,
        # because the trigger-driven write path handles canonical_markets
        # transitions atomically; this function is for AUDIT-ONLY corrections.
        cur.execute(
            """
            INSERT INTO canonical_market_phase_log (
                canonical_market_id, previous_phase, new_phase, changed_by, note
            ) VALUES (
                %s, %s, %s, %s, %s
            )
            RETURNING id
            """,
            (
                canonical_market_id,
                previous_phase,
                new_phase,
                changed_by,
                note,
            ),
        )
        row = cur.fetchone()
        return cast("int", row["id"])


def _validate_append_market_phase_transition_args(
    *,
    new_phase: str,
    previous_phase: str | None,
    changed_by: str,
) -> None:
    """Pattern 73 SSOT + boundary validation for market-phase-transition append args.

    Centralizing the validation here keeps ``append_market_phase_transition``'s
    "no SQL on validation failure" contract intact (callers asserting
    ``mock_get_cursor.assert_not_called()`` still pass).  Mirrors slot
    0073/0079's ``_validate_append_*_args`` shape.
    """
    # new_phase must be in the canonical 5-value market vocabulary.  Pattern 73 SSOT.
    if new_phase not in CANONICAL_MARKET_LIFECYCLE_PHASES:
        raise ValueError(
            f"new_phase {new_phase!r} not in canonical "
            f"CANONICAL_MARKET_LIFECYCLE_PHASES {CANONICAL_MARKET_LIFECYCLE_PHASES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    # previous_phase: nullable, but if non-NULL must be in the same vocabulary.
    if previous_phase is not None and previous_phase not in CANONICAL_MARKET_LIFECYCLE_PHASES:
        raise ValueError(
            f"previous_phase {previous_phase!r} not in canonical "
            f"CANONICAL_MARKET_LIFECYCLE_PHASES {CANONICAL_MARKET_LIFECYCLE_PHASES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    # changed_by prefix discipline: must start with one of the canonical
    # actor-taxonomy prefixes from constants.py:DECIDED_BY_PREFIXES.  CHECK
    # cannot enforce string format, so this is the discipline.  Slot 0073/0079
    # inheritance.
    if not any(changed_by.startswith(p) for p in DECIDED_BY_PREFIXES):
        raise ValueError(
            f"changed_by {changed_by!r} must start with one of "
            f"DECIDED_BY_PREFIXES {DECIDED_BY_PREFIXES!r}; "
            "pattern 73 SSOT vocabulary violation"
        )

    # changed_by length boundary per slot-0073 #1085 finding #3 inheritance.
    if len(changed_by) > _CHANGED_BY_MAX_LENGTH:
        raise ValueError(
            f"changed_by length {len(changed_by)} exceeds "
            f"VARCHAR({_CHANGED_BY_MAX_LENGTH}) column boundary; got {changed_by!r}"
        )


# =============================================================================
# CANONICAL MARKET PHASE LOG -- READ OPERATIONS
# =============================================================================


def get_phase_history_for_market(
    canonical_market_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get the phase-transition history for a canonical market, newest-first.

    The operator audit hot path: when an operator asks "what's the phase
    history for this market?", they call this function.  Returns rows in
    descending ``transition_at`` order (most-recent first); the underlying
    ``idx_canonical_market_phase_log_market_transition`` composite index
    serves both the WHERE filter and the ORDER BY in one scan, keeping
    this O(log n) + limit-bounded regardless of total log-table size.

    Args:
        canonical_market_id: BIGINT target into the ``canonical_markets.id``
            primary key.
        limit: Maximum rows returned.  Defaults to 50 -- large enough for
            most operator runbook queries, small enough to bound the
            per-query overhead.  Pagination beyond 50 rows is not
            currently supported (file an issue if a use case justifies it).

    Returns:
        List of full row dicts (possibly empty), ordered by
        ``transition_at DESC``.  Keys: id, canonical_market_id,
        previous_phase, new_phase, transition_at, changed_by, note,
        created_at.

    Example:
        >>> history = get_phase_history_for_market(42)
        >>> for row in history:
        ...     print(f"{row['transition_at']}: "
        ...           f"{row['previous_phase']}->{row['new_phase']} "
        ...           f"by {row['changed_by']}")

    Reference:
        - Migration 0088 (idx_canonical_market_phase_log_market_transition)
    """
    query = """
        SELECT id, canonical_market_id, previous_phase, new_phase,
               transition_at, changed_by, note, created_at
        FROM canonical_market_phase_log
        WHERE canonical_market_id = %s
        ORDER BY transition_at DESC
        LIMIT %s
    """
    return fetch_all(query, (canonical_market_id, limit))


# =============================================================================
# Sentinel: CANONICAL_MARKET_LIFECYCLE_PHASES + DECIDED_BY_PREFIXES are imported
# and USED above in real-guard ``ValueError``-raising validation
# (_validate_append_market_phase_transition_args).  If a future refactor drops
# the validation, the imports become unused and ruff (F401) will fire -- closing
# the side-effect-only-import drift surface that #1085 finding #2 strengthening
# prevents (slot 0073/0079 inheritance).
# =============================================================================
