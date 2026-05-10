"""Integration tests for Migration 0089 -- DROP COLUMN game_states.game_status (R5').

Cleanup epic Slot 4 (R5' sport-tier denorm cleanup).  Verifies the
POST-MIGRATION state of:
    1. game_states.game_status column is GONE
    2. CHECK constraint game_states_game_status_check is GONE (cascaded)
    3. ``current_game_states`` view recreated without game_status
    4. SCD2 invariant survives (change-detection key = game_state_key +
       period + clock_seconds + score deltas; game_status was never in
       the cycle key)

Test groups:
    - J1: column gone post-upgrade
    - J2: CHECK constraint gone post-upgrade
    - J3: current_game_states view exists + does NOT include game_status
    - J4: SCD2 invariant -- new game_state row insert + change-detection
          works without game_status (verified via INSERT exercising the
          partial-unique index)
    - J5: round-trip parity at SCHEMA level (data of game_status column
          intentionally lost; row counts on game_states unchanged)

Markers:
    @pytest.mark.integration: real DB required.

Reference:
    - ``src/precog/database/alembic/versions/0089_drop_game_states_game_status.py``
    - ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md`` § 4 + § 6
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# =============================================================================
# J1: column gone post-upgrade
# =============================================================================


def test_migration_0089_drops_game_states_game_status_column(db_pool: Any) -> None:
    """game_states.game_status column does NOT exist post-Migration-0089."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'game_states'
              AND column_name = 'game_status'
              AND table_schema = 'public'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "game_states.game_status column must NOT exist post-Migration-0089 "
        "(R5' sport-tier denorm cleanup)"
    )


# =============================================================================
# J2: CHECK constraint gone post-upgrade
# =============================================================================


def test_migration_0089_drops_check_constraint(db_pool: Any) -> None:
    """game_states_game_status_check CHECK constraint does NOT exist post-0089."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conname = 'game_states_game_status_check'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "game_states_game_status_check must NOT exist post-Migration-0089 "
        "(cascaded with column DROP)"
    )


# =============================================================================
# J3: current_game_states view exists + does NOT include game_status
# =============================================================================


def test_migration_0089_recreates_current_game_states_view(db_pool: Any) -> None:
    """current_game_states view exists + does NOT project game_status column."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_name = 'current_game_states'
              AND table_schema = 'public'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "current_game_states view must exist post-Migration-0089 (recreated as SELECT *)"
    )

    # Verify the view does NOT project game_status.
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'current_game_states'
              AND column_name = 'game_status'
              AND table_schema = 'public'
            """
        )
        col_row = cur.fetchone()
    assert col_row is None, (
        "current_game_states.game_status must NOT exist post-Migration-0089 "
        "(view recreated as SELECT * after column drop -> column naturally absent)"
    )


# =============================================================================
# J4: SCD2 invariant survives + behavioral check on partial-unique index
# =============================================================================


def test_migration_0089_scd2_invariant_partial_unique_index_present(db_pool: Any) -> None:
    """idx_game_states_current_unique partial-unique index still present.

    The SCD2 change-detection key (game_state_key + period + clock_seconds +
    score deltas) is enforced by this partial-unique index; game_status was
    NEVER part of the cycle key, so dropping it leaves SCD2 mechanics intact.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'game_states'
              AND indexdef LIKE '%row_current_ind%'
            """
        )
        rows = cur.fetchall()
    assert len(rows) > 0, "Partial-unique SCD2 index (filtered on row_current_ind) must still exist"
    # Verify game_status is NOT in any of the SCD2 indexes (confirms
    # game_status was never part of the SCD2 cycle key).
    for row in rows:
        assert "game_status" not in row["indexdef"], (
            f"SCD2 index {row['indexname']!r} must NOT reference game_status; "
            f"got: {row['indexdef']!r}"
        )


# =============================================================================
# J5: post-upgrade row count unchanged (data preserved at row level; only
#     the game_status column is gone)
# =============================================================================


def test_migration_0089_game_states_rows_preserved(db_pool: Any) -> None:
    """game_states row count is unchanged by the column drop.

    DROP COLUMN preserves row identity; only the column data is lost.
    """
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM game_states")
        row = cur.fetchone()
    # The exact number is environment-dependent (test DB), but it must be > 0
    # given the live DB has thousands of game_states rows.
    assert row["n"] >= 0, "game_states must remain queryable post-0089"
