"""Integration tests for Migration 0090 -- platform-prefix rename (V2.48 ADR).

Cohort 5+ Slot A.  Verifies the POST-MIGRATION state of:
    1. Renamed tables exist (platform_events, platform_markets, ...).
    2. Pre-rename tables do NOT exist (events, markets, ...).
    3. FK columns renamed on leave-alone tables (edges.platform_market_id,
       predictions.platform_event_id, etc.).
    4. FK constraints renamed (e.g., edges_platform_market_id_fkey).
    5. Sequences renamed AND trailing `1` suffixes normalized away
       (session 103 Phase 6 OQ-H1 PRESERVE -> NORMALIZE flip, forward-only).
    6. PK indexes renamed (platform_events_pkey, ...).
    7. All 12 views recreated.
    8. Canonical-side FKs auto-retargeted to platform_* tables.

Test groups:
    - J1: 7 base tables renamed (exist under platform_* names)
    - J2: 7 pre-rename tables absent
    - J3: FK columns renamed on leave-alone tables
    - J4: FK constraints renamed
    - J5: Sequences renamed (markets_id_seq1 -> platform_markets_id_seq;
          trailing `1` normalized per Phase 6 OQ-H1 flip)
    - J6: PK indexes renamed
    - J7: All 12 views exist post-rename
    - J8: Canonical-side FK auto-retargeting (target = platform_markets / platform_events)

Markers:
    @pytest.mark.integration: real DB required.

Reference:
    - ``src/precog/database/alembic/versions/0090_platform_prefix_rename.py``
    - ``memory/build_spec_0090_platform_rename_pm_memo.md``
    - ``memory/build_progress_0090_mcp_probe_results.md``
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# Tables renamed by Migration 0090 (pre-rename -> post-rename).
_RENAMED_TABLES: list[tuple[str, str]] = [
    ("events", "platform_events"),
    ("markets", "platform_markets"),
    ("series", "platform_series"),
    ("market_snapshots", "platform_market_snapshots"),
    ("market_trades", "platform_market_trades"),
    ("orderbook_snapshots", "platform_orderbook_snapshots"),
    ("settlements", "platform_settlements"),
]

# All 12 views recreated by Migration 0090.
_RECREATED_VIEWS: list[str] = [
    "current_markets",
    "current_series",
    "current_edges",
    "edge_lifecycle",
    "live_trades",
    "paper_trades",
    "backtest_trades",
    "training_data_trades",
    "live_positions",
    "paper_positions",
    "backtest_positions",
    "open_positions",
]


# =============================================================================
# J1: 7 base tables renamed -- platform_* tables exist
# =============================================================================


@pytest.mark.parametrize("post_name", [post for _pre, post in _RENAMED_TABLES])
def test_migration_0090_platform_tables_exist(db_pool: Any, post_name: str) -> None:
    """All 7 renamed base tables exist under platform_* names."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name = %s
            """,
            (post_name,),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"Table {post_name!r} must exist post-Migration-0090 (V2.48 platform-prefix rename)"
    )


# =============================================================================
# J2: 7 pre-rename tables are gone
# =============================================================================


@pytest.mark.parametrize("pre_name", [pre for pre, _post in _RENAMED_TABLES])
def test_migration_0090_pre_rename_tables_absent(db_pool: Any, pre_name: str) -> None:
    """Pre-rename tables must NOT exist post-0090."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name = %s
            """,
            (pre_name,),
        )
        row = cur.fetchone()
    assert row is None, f"Pre-rename table {pre_name!r} must NOT exist post-Migration-0090"


# =============================================================================
# J3: FK columns renamed on leave-alone tables
# =============================================================================


@pytest.mark.parametrize(
    ("table", "column"),
    [
        # Leave-alone tables: FK columns rename to platform_*_id
        ("edges", "platform_market_id"),
        ("edges", "platform_market_snapshot_id"),
        ("edges", "platform_orderbook_snapshot_id"),
        ("orders", "platform_market_id"),
        ("orders", "platform_orderbook_snapshot_id"),
        ("positions", "platform_market_id"),
        ("trades", "platform_market_id"),
        ("predictions", "platform_event_id"),
        ("predictions", "platform_market_id"),
        # Renamed tables: FK columns also rename
        ("platform_markets", "platform_event_id"),
        ("platform_events", "platform_series_id"),
        ("platform_market_snapshots", "platform_market_id"),
        ("platform_market_trades", "platform_market_id"),
        ("platform_orderbook_snapshots", "platform_market_id"),
        ("platform_settlements", "platform_market_id"),
    ],
)
def test_migration_0090_fk_columns_renamed(db_pool: Any, table: str, column: str) -> None:
    """FK columns renamed to platform_*_id pattern per OQ-H5 + build spec § 0.2."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            (table, column),
        )
        row = cur.fetchone()
    assert row is not None, f"{table}.{column} must exist post-Migration-0090 (FK column rename)"


@pytest.mark.parametrize(
    ("table", "column"),
    [
        # Old column names should be gone
        ("edges", "market_id"),
        ("edges", "market_snapshot_id"),
        ("edges", "orderbook_snapshot_id"),
        ("orders", "market_id"),
        ("orders", "orderbook_snapshot_id"),
        ("positions", "market_id"),
        ("trades", "market_id"),
        ("predictions", "event_id"),
        ("predictions", "market_id"),
    ],
)
def test_migration_0090_old_fk_columns_absent(db_pool: Any, table: str, column: str) -> None:
    """Pre-rename FK columns must NOT exist post-0090."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            (table, column),
        )
        row = cur.fetchone()
    assert row is None, f"Pre-rename column {table}.{column} must NOT exist post-Migration-0090"


# =============================================================================
# J4: FK constraints renamed with platform_* prefix
# =============================================================================


@pytest.mark.parametrize(
    ("constraint_name", "table"),
    [
        # platform_events
        ("platform_events_game_id_fkey", "platform_events"),
        ("platform_events_platform_id_fkey", "platform_events"),
        ("platform_events_platform_series_id_fkey", "platform_events"),
        # platform_markets
        ("platform_markets_platform_event_id_fkey", "platform_markets"),
        ("platform_markets_platform_id_fkey", "platform_markets"),
        # platform_market_snapshots
        (
            "platform_market_snapshots_platform_market_id_fkey",
            "platform_market_snapshots",
        ),
        # platform_market_trades
        (
            "platform_market_trades_platform_market_id_fkey",
            "platform_market_trades",
        ),
        ("platform_market_trades_platform_id_fkey", "platform_market_trades"),
        # platform_orderbook_snapshots
        (
            "platform_orderbook_snapshots_platform_market_id_fkey",
            "platform_orderbook_snapshots",
        ),
        # platform_series
        ("platform_series_platform_id_fkey", "platform_series"),
        # platform_settlements
        (
            "platform_settlements_platform_market_id_fkey",
            "platform_settlements",
        ),
        ("platform_settlements_order_id_fkey", "platform_settlements"),
        ("platform_settlements_platform_id_fkey", "platform_settlements"),
        ("platform_settlements_position_id_fkey", "platform_settlements"),
        # edges (leave-alone, FK targets renamed)
        ("edges_platform_market_id_fkey", "edges"),
        ("edges_platform_market_snapshot_id_fkey", "edges"),
        ("edges_platform_orderbook_snapshot_id_fkey", "edges"),
        # orders
        ("orders_platform_market_id_fkey", "orders"),
        ("orders_platform_orderbook_snapshot_id_fkey", "orders"),
        # positions
        ("positions_platform_market_id_fkey", "positions"),
        # trades
        ("trades_platform_market_id_fkey", "trades"),
        # predictions
        ("predictions_platform_event_id_fkey", "predictions"),
        ("predictions_platform_market_id_fkey", "predictions"),
    ],
)
def test_migration_0090_fk_constraints_renamed(
    db_pool: Any, constraint_name: str, table: str
) -> None:
    """FK constraints renamed with platform_* prefix per build spec § 0.2."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE contype = 'f'
              AND conname = %s
              AND conrelid = %s::regclass
            """,
            (constraint_name, table),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"FK constraint {constraint_name!r} on {table!r} must exist post-Migration-0090"
    )


# =============================================================================
# J5: Sequences renamed (markets_id_seq1 -> platform_markets_id_seq;
#     trailing `1` normalized per session 103 Phase 6 OQ-H1 flip)
# =============================================================================


@pytest.mark.parametrize(
    "sequence_name",
    [
        "platform_events_id_seq",
        "platform_markets_id_seq",  # OQ-H1 Phase 6 NORMALIZE: trailing `1` removed
        "platform_series_id_seq",
        "platform_market_snapshots_id_seq",
        "platform_market_trades_id_seq",
        "platform_orderbook_snapshots_id_seq",
        "platform_settlements_settlement_id_seq",
    ],
)
def test_migration_0090_sequences_renamed(db_pool: Any, sequence_name: str) -> None:
    """7 sequences renamed; markets_id_seq1 -> platform_markets_id_seq normalizes
    the trailing `1` away per session 103 Phase 6 OQ-H1 PRESERVE -> NORMALIZE flip
    (forward-only, user-adjudicated). See Migration 0090 upgrade() Step 0 pre-step."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = 'public'
              AND sequence_name = %s
            """,
            (sequence_name,),
        )
        row = cur.fetchone()
    assert row is not None, f"Sequence {sequence_name!r} must exist post-Migration-0090"


def test_migration_0090_markets_id_seq_trailing_one_normalized(db_pool: Any) -> None:
    """OQ-H1 Phase 6 invariant: platform_markets_id_seq (no trailing `1`) exists;
    platform_markets_id_seq1 (with trailing `1`) does NOT exist post-normalization.

    Phase 6 flipped the original V2.48 PRESERVE adjudication to NORMALIZE
    (forward-only) under user direction, after cross-env drift in session 103
    revealed the trailing-`1` legacy was actively blocking the rename in
    environments where the seed sequence had already been normalized.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = 'public'
              AND sequence_name IN (
                'platform_markets_id_seq',
                'platform_markets_id_seq1'
              )
            ORDER BY sequence_name
            """
        )
        rows = cur.fetchall()
    names = {row["sequence_name"] for row in rows}
    assert "platform_markets_id_seq" in names, (
        "platform_markets_id_seq (no trailing `1`) must exist per Phase 6 OQ-H1 NORMALIZE"
    )
    assert "platform_markets_id_seq1" not in names, (
        "platform_markets_id_seq1 (with trailing `1`) must NOT exist; Phase 6 "
        "normalization should have stripped the legacy `1` suffix"
    )


# =============================================================================
# J6: PK indexes renamed
# =============================================================================


@pytest.mark.parametrize(
    ("pk_name", "table"),
    [
        ("platform_events_pkey", "platform_events"),
        ("platform_markets_pkey", "platform_markets"),
        ("platform_series_pkey", "platform_series"),
        ("platform_market_snapshots_pkey", "platform_market_snapshots"),
        ("platform_market_trades_pkey", "platform_market_trades"),
        ("platform_orderbook_snapshots_pkey", "platform_orderbook_snapshots"),
        ("platform_settlements_pkey", "platform_settlements"),
    ],
)
def test_migration_0090_pk_indexes_renamed(db_pool: Any, pk_name: str, table: str) -> None:
    """7 PK indexes renamed via ALTER INDEX RENAME TO."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = %s
              AND indexname = %s
            """,
            (table, pk_name),
        )
        row = cur.fetchone()
    assert row is not None, f"PK index {pk_name!r} on {table!r} must exist post-Migration-0090"


# =============================================================================
# J7: All 12 views recreated post-rename
# =============================================================================


@pytest.mark.parametrize("view_name", _RECREATED_VIEWS)
def test_migration_0090_views_recreated(db_pool: Any, view_name: str) -> None:
    """All 12 affected views recreated with explicit column lists (Pattern 93)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name = %s
            """,
            (view_name,),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"View {view_name!r} must exist post-Migration-0090 "
        "(Pattern 93 explicit column list recreation)"
    )


# =============================================================================
# J8: Canonical-side FK auto-retargeting (constraint names unchanged;
#     target tables are now platform_* tables per PG auto-retarget on table rename)
# =============================================================================


@pytest.mark.parametrize(
    ("constraint_name", "referencing_table", "expected_target_regex"),
    [
        (
            "canonical_event_links_platform_event_id_fkey",
            "canonical_event_links",
            "REFERENCES platform_events",
        ),
        (
            "canonical_market_links_platform_market_id_fkey",
            "canonical_market_links",
            "REFERENCES platform_markets",
        ),
        (
            "canonical_match_overrides_platform_market_id_fkey",
            "canonical_match_overrides",
            "REFERENCES platform_markets",
        ),
    ],
)
def test_migration_0090_canonical_side_fks_auto_retargeted(
    db_pool: Any,
    constraint_name: str,
    referencing_table: str,
    expected_target_regex: str,
) -> None:
    """Already-correctly-named canonical-side FK constraints auto-retarget to
    platform_* tables when the target table renames in step 5.

    Constraint names DO NOT change (already-correctly-named); FK target is
    automatically re-pointed at the post-rename table name by PG's metadata.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE contype = 'f'
              AND conname = %s
              AND conrelid = %s::regclass
            """,
            (constraint_name, referencing_table),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"FK constraint {constraint_name!r} on {referencing_table!r} must exist "
        "post-Migration-0090 (auto-retargeted, name unchanged)"
    )
    assert expected_target_regex in row["def"], (
        f"FK {constraint_name!r} must auto-retarget to {expected_target_regex!r}; "
        f"got definition: {row['def']!r}"
    )
