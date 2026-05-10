"""Pattern 73 SSOT five-way parity: 2 vocabularies vs 5 DDL CHECKs.

Verifies the lifecycle_phase vocabularies are consistent across all
authoritative locations, post-Slot-4 (Migration 0088) R3 redistribution
+ R8 bundle reduction:

    EVENT vocabulary (5-value, post-R8 reduction; was 8-value pre-Slot-4):

        1. ``src/precog/database/constants.py:CANONICAL_EVENT_LIFECYCLE_PHASES``
           -- Python canonical home (the SSOT anchor).
        2. ``canonical_events.lifecycle_phase`` CHECK constraint.  Originally
           shipped 8-value in Migration 0070 (V2.40 Cohort 1 carry-forward
           Item 3); reduced to 5-value in Migration 0088 R8 bundle.
        3. ``canonical_event_phase_log.new_phase`` CHECK.  Originally shipped
           in Migration 0079 (slot 0079, Cohort 4); mirror reduction in
           Migration 0088.
        4. ``canonical_event_phase_log.previous_phase`` CHECK (NULL-tolerant).
           Same provenance as #3.

    MARKET vocabulary (5-value, NEW post-Slot-4):

        5. ``src/precog/database/constants.py:CANONICAL_MARKET_LIFECYCLE_PHASES``
           -- Python canonical home (NEW SSOT anchor).
        6. ``canonical_markets.lifecycle_phase`` CHECK.  NEW column shipped
           by Migration 0088.
        7. ``canonical_market_phase_log.new_phase`` CHECK.  NEW table
           shipped by Migration 0088 (mirror of slot 0079 shape).
        8. ``canonical_market_phase_log.previous_phase`` CHECK (NULL-tolerant).
           Same provenance as #7.

Drift between any of these locations would silently produce state-machine
bugs (a CRUD-side write of an unknown phase rejected by DDL but passing
Python validation, or vice versa).  The Pattern 73 SSOT discipline says
"any rule, value, formula, or logic that appears in more than one location
MUST have ONE canonical definition plus pointers/imports".

This test queries the LIVE PG ``pg_get_constraintdef`` output for each
CHECK constraint and asserts set-equality with the corresponding Python
constant.

Pattern 73 SSOT (CLAUDE.md Critical Pattern #8):
    Each constant in constants.py is the canonical source for its
    vocabulary.  All matching DDL CHECKs must mirror it.  Adding a new
    phase to either vocabulary requires lockstep update of:
        - the constant in constants.py
        - the dim-table CHECK (canonical_events OR canonical_markets)
        - the audit-log new_phase CHECK
        - the audit-log previous_phase CHECK (NULL-tolerant)

Slot 4 (cleanup epic #1155) post-Migration-0088 state:
    Pre-0088: 3-way parity on event vocabulary only (the 8-value enum).
    Post-0088: 5-way parity on EACH vocabulary -- event 5-value (R8
    reduction) + market 5-value (R3 redistribution).

Reference:
    - ``src/precog/database/constants.py:CANONICAL_EVENT_LIFECYCLE_PHASES``
    - ``src/precog/database/constants.py:CANONICAL_MARKET_LIFECYCLE_PHASES``
    - ``src/precog/database/alembic/versions/0070_cohort_1_carryforward_hardening.py``
    - ``src/precog/database/alembic/versions/0079_canonical_event_phase_log.py``
    - ``src/precog/database/alembic/versions/0088_canonical_lifecycle_phase_redistribution.py``
    - ``tests/unit/database/test_constants_unit.py``
    - Slot 4 build spec § 6 + § 8 + § 0d D-4
    - DEVELOPMENT_PATTERNS V1.40 Pattern 73 SSOT

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from precog.database.connection import get_cursor
from precog.database.constants import (
    CANONICAL_EVENT_LIFECYCLE_PHASES,
    CANONICAL_MARKET_LIFECYCLE_PHASES,
)

pytestmark = [pytest.mark.integration]


def _extract_check_values(constraint_def: str) -> set[str]:
    """Extract every single-quoted string literal from a CHECK constraint def.

    Pattern: PG returns CHECK constraints in the form
        ``CHECK (((col)::text = ANY ((ARRAY['v1'::character varying, ...])::text[])))``.
    The single-quoted tokens are the IN-list values.  Set-form because
    the order is irrelevant at the SQL layer.
    """
    return set(re.findall(r"'([^']+)'", constraint_def))


# =============================================================================
# Group 1: EVENT vocabulary (5-value, post-Slot-4 R8 reduction)
# =============================================================================


def test_canonical_events_lifecycle_phase_check_matches_constant(db_pool: Any) -> None:
    """canonical_events.lifecycle_phase CHECK contains exactly the 5 event-vocab values (post-R8)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_events'::regclass
              AND contype = 'c'
              AND conname = 'canonical_events_lifecycle_phase_check'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_events_lifecycle_phase_check must exist post-Migration 0070"
    db_values = _extract_check_values(row["def"])
    constant_values = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_events.lifecycle_phase CHECK diverged from "
        "CANONICAL_EVENT_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


def test_canonical_event_phase_log_new_phase_check_matches_constant(db_pool: Any) -> None:
    """canonical_event_phase_log.new_phase CHECK contains exactly the 5 event-vocab values (post-R8)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_phase_log'::regclass
              AND contype = 'c'
              AND conname = 'ck_canonical_event_phase_log_new_phase'
            """
        )
        row = cur.fetchone()
    assert row is not None, "ck_canonical_event_phase_log_new_phase must exist post-Migration 0079"
    db_values = _extract_check_values(row["def"])
    constant_values = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_event_phase_log.new_phase CHECK diverged from "
        "CANONICAL_EVENT_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


def test_canonical_event_phase_log_previous_phase_check_matches_constant(
    db_pool: Any,
) -> None:
    """canonical_event_phase_log.previous_phase CHECK contains exactly the 5 event-vocab values (post-R8).

    Note: previous_phase CHECK is NULL-tolerant; the value-set inside the
    OR-IN clause still mirrors the same 5 values.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_phase_log'::regclass
              AND contype = 'c'
              AND conname = 'ck_canonical_event_phase_log_previous_phase'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "ck_canonical_event_phase_log_previous_phase must exist post-Migration 0079"
    )
    constraint_def = row["def"]
    # NULL-tolerant CHECK form must include "IS NULL".
    assert "IS NULL" in constraint_def.upper(), (
        f"previous_phase CHECK must be NULL-tolerant (IS NULL OR ...); got: {constraint_def!r}"
    )

    db_values = _extract_check_values(constraint_def)
    constant_values = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_event_phase_log.previous_phase CHECK diverged from "
        "CANONICAL_EVENT_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


# =============================================================================
# Group 2: MARKET vocabulary (5-value, NEW post-Slot-4 R3 redistribution)
# =============================================================================


def test_canonical_markets_lifecycle_phase_check_matches_constant(db_pool: Any) -> None:
    """canonical_markets.lifecycle_phase CHECK contains exactly the 5 market-vocab values."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_markets'::regclass
              AND contype = 'c'
              AND conname LIKE '%lifecycle_phase%'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_markets.lifecycle_phase CHECK must exist post-Migration 0088"
    db_values = _extract_check_values(row["def"])
    constant_values = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_markets.lifecycle_phase CHECK diverged from "
        "CANONICAL_MARKET_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


def test_canonical_market_phase_log_new_phase_check_matches_constant(db_pool: Any) -> None:
    """canonical_market_phase_log.new_phase CHECK contains exactly the 5 market-vocab values."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_market_phase_log'::regclass
              AND contype = 'c'
              AND conname = 'ck_canonical_market_phase_log_new_phase'
            """
        )
        row = cur.fetchone()
    assert row is not None, "ck_canonical_market_phase_log_new_phase must exist post-Migration 0088"
    db_values = _extract_check_values(row["def"])
    constant_values = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_market_phase_log.new_phase CHECK diverged from "
        "CANONICAL_MARKET_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


def test_canonical_market_phase_log_previous_phase_check_matches_constant(
    db_pool: Any,
) -> None:
    """canonical_market_phase_log.previous_phase CHECK contains exactly the 5 market-vocab values.

    Note: previous_phase CHECK is NULL-tolerant.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_market_phase_log'::regclass
              AND contype = 'c'
              AND conname = 'ck_canonical_market_phase_log_previous_phase'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "ck_canonical_market_phase_log_previous_phase must exist post-Migration 0088"
    )
    constraint_def = row["def"]
    assert "IS NULL" in constraint_def.upper(), (
        f"previous_phase CHECK must be NULL-tolerant (IS NULL OR ...); got: {constraint_def!r}"
    )

    db_values = _extract_check_values(constraint_def)
    constant_values = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
    assert db_values == constant_values, (
        "canonical_market_phase_log.previous_phase CHECK diverged from "
        "CANONICAL_MARKET_LIFECYCLE_PHASES.\n"
        f"  DB CHECK:  {sorted(db_values)}\n"
        f"  Constant:  {sorted(constant_values)}\n"
        "  Fix: update both the migration (new alembic revision) and "
        "the constant in lockstep, per Pattern 73 SSOT."
    )


# =============================================================================
# Group 3: load-bearing five-way parity tests (one per vocabulary)
# =============================================================================


def test_event_vocabulary_three_way_parity(db_pool: Any) -> None:
    """LOAD-BEARING three-way SSOT parity test for the EVENT vocabulary (post-R8 reduction).

    Asserts ALL THREE event-vocabulary locations agree:
        constants.py:CANONICAL_EVENT_LIFECYCLE_PHASES
        ==
        canonical_events.lifecycle_phase CHECK values
        ==
        canonical_event_phase_log.new_phase CHECK values
        ==
        canonical_event_phase_log.previous_phase CHECK values (NULL-tolerant subset)

    Drift between any of these would have produced silent state-machine
    bugs.  This is the canonical Pattern 73 SSOT enforcement gate for the
    event vocabulary, post-Slot-4 R8 reduction (8->5).
    """
    constant_values = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
    assert len(constant_values) == 5, (
        f"CANONICAL_EVENT_LIFECYCLE_PHASES must have 5 values post-Slot-4 R8; "
        f"got {len(constant_values)}"
    )

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname IN (
                'canonical_events_lifecycle_phase_check',
                'ck_canonical_event_phase_log_new_phase',
                'ck_canonical_event_phase_log_previous_phase'
            )
            ORDER BY conname
            """
        )
        rows = cur.fetchall()
    assert len(rows) == 3, (
        f"All 3 event-vocab CHECK constraints must exist; got {len(rows)}: "
        f"{[r['conname'] for r in rows]}"
    )

    constraint_value_sets = {row["conname"]: _extract_check_values(row["def"]) for row in rows}

    for conname, db_values in constraint_value_sets.items():
        assert db_values == constant_values, (
            f"Three-way SSOT parity violation: {conname} value-set diverged "
            f"from CANONICAL_EVENT_LIFECYCLE_PHASES.\n"
            f"  DB CHECK ({conname}):  {sorted(db_values)}\n"
            f"  Constant:               {sorted(constant_values)}"
        )


def test_market_vocabulary_three_way_parity(db_pool: Any) -> None:
    """LOAD-BEARING three-way SSOT parity test for the MARKET vocabulary (NEW post-Slot-4).

    Asserts ALL THREE market-vocabulary locations agree:
        constants.py:CANONICAL_MARKET_LIFECYCLE_PHASES
        ==
        canonical_markets.lifecycle_phase CHECK values
        ==
        canonical_market_phase_log.new_phase CHECK values
        ==
        canonical_market_phase_log.previous_phase CHECK values (NULL-tolerant subset)

    Drift between any of these would have produced silent state-machine
    bugs.  This is the canonical Pattern 73 SSOT enforcement gate for the
    market vocabulary, NEW post-Slot-4 R3 redistribution.
    """
    constant_values = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
    assert len(constant_values) == 5, (
        f"CANONICAL_MARKET_LIFECYCLE_PHASES must have 5 values; got {len(constant_values)}"
    )

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid IN ('canonical_markets'::regclass, 'canonical_market_phase_log'::regclass)
              AND contype = 'c'
              AND (conname LIKE '%lifecycle_phase%'
                   OR conname IN (
                       'ck_canonical_market_phase_log_new_phase',
                       'ck_canonical_market_phase_log_previous_phase'
                   ))
            ORDER BY conname
            """
        )
        rows = cur.fetchall()
    assert len(rows) == 3, (
        f"All 3 market-vocab CHECK constraints must exist post-Migration 0088; got {len(rows)}: "
        f"{[r['conname'] for r in rows]}"
    )

    constraint_value_sets = {row["conname"]: _extract_check_values(row["def"]) for row in rows}

    for conname, db_values in constraint_value_sets.items():
        assert db_values == constant_values, (
            f"Three-way SSOT parity violation: {conname} value-set diverged "
            f"from CANONICAL_MARKET_LIFECYCLE_PHASES.\n"
            f"  DB CHECK ({conname}):  {sorted(db_values)}\n"
            f"  Constant:               {sorted(constant_values)}"
        )


def test_two_vocabularies_are_disjoint_modulo_completed(db_pool: Any) -> None:
    """The 2 vocabularies share no common values modulo intentional design.

    Post-Slot-4 R3 redistribution + R8 reduction:
        EVENT vocab: proposed, listed, pre_event, live, completed
        MARKET vocab: open, suspended, settling, resolved, voided

    These are intentionally disjoint sets -- the redistribution split
    resolution-tier states (suspended/settling/resolved/voided) into the
    market vocabulary so that per-canonical-market resolution divergence
    has its own state machine.  ``completed`` (event-completion) and
    ``resolved`` (market-resolution) are the load-bearing distinct
    semantics: an event can be completed (game ended) while individual
    markets are still settling (e.g., score corrections pending).

    A future amendment that introduces overlap MUST carry an ADR-118
    amendment narrating the rationale.
    """
    event_set = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
    market_set = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
    overlap = event_set & market_set
    assert overlap == set(), (
        f"Event + market vocabularies must be disjoint per Slot-4 R3+R8 design; "
        f"overlap = {sorted(overlap)}"
    )
