"""Cohort 5+ Slot A -- platform-prefix rename (V2.48 ADR realization).

Cohort 5+ Slot A (per ADR-118 V2.48, PR #1180 design lock).
Source: ``memory/build_spec_0090_platform_rename_pm_memo.md`` (PM Picard session 100) +
``memory/build_progress_0090_mcp_probe_results.md`` (Samwise probe results session 101).

Pattern 87: this file is immutable post-merge.

Scope (largest single migration in project history, ~700 touch points):

- 7 base table renames: ``events``, ``markets``, ``series``, ``market_snapshots``,
  ``market_trades``, ``orderbook_snapshots``, ``settlements`` -> ``platform_*``.
- 15 FK column renames per build spec § 0.2 (column ``*_id`` ->
  ``platform_*_id``) across the 7 renaming tables PLUS the 6 leave-alone
  trading-state tables (orders/positions/trades/predictions/edges).
- ~22 FK constraint drop+recreate cycles (column-attached drops, target
  retargeting after table rename, recreate with new column + new constraint
  name).
- 7 sequence renames (preserving the ``markets_id_seq1`` trailing ``1``
  suffix per OQ-H1 / Pattern 87 carve-out: don't fix unrelated issues in
  the rename slot).
- 7 PK index renames via ``ALTER INDEX ... RENAME TO``.
- 5 unique constraint renames via ``ALTER TABLE ... RENAME CONSTRAINT``.
- ~30 ``idx_*`` index renames via ``ALTER INDEX ... RENAME TO``.
- 12 view DROP + recreate (NOT 9 as build spec § 2.1 step 1 originally
  estimated; pg_views probe found 12 -- ``current_markets``,
  ``current_series``, ``current_edges``, ``edge_lifecycle``, plus 4
  trades-related views and 4 positions-related views).

Pattern 93 (Galadriel session 98 V1.45): ALL 12 views recreated with
EXPLICIT column lists captured from ``pg_views.definition`` at MCP probe
time (NOT ``SELECT *``).  ``SELECT *`` froze view column dependencies at
CREATE-time, blocking later DROP COLUMN operations downstream
(slot 0089 R5' fix-pass discipline).  Slot 0090 self-applies the lesson.

Pattern 91 V1.45+ (MCP-first premise verification): every constraint
name, sequence name, index name, and view definition in this file was
MCP-grounded at session 101 build time and persisted to
``memory/build_progress_0090_mcp_probe_results.md`` for cross-session
continuity.

OQ adjudications applied inline (per build spec § 1):

- OQ-O1: ``match_algorithm`` and ``observation_source`` lookup tables
  stay unprefixed (grandfathered carve-out).
- OQ-O2: ``canonical_events.series_id`` rename is MOOT (column dropped
  by slot 0086 denorm collapse).
- OQ-H1: ``markets_id_seq1`` trailing ``1`` suffix preserved.
- OQ-H4: ``circuit_breaker_events.event_id`` is NOT a FK to events --
  leave alone.
- OQ-H5: ``predictions.event_id`` IS a FK to events -- rename to
  ``platform_event_id`` even though the parent ``predictions`` table
  stays unprefixed (internal trading state, OQ-H5 carve-out).
- OQ-H6: 0 SQLAlchemy ``__tablename__`` matches -- no ORM updates.

Already-correctly-named canonical-side FKs (auto-retargeted by PG when
the target table renames; constraint names unchanged):

- ``canonical_event_links_platform_event_id_fkey`` -> retargets to platform_events
- ``canonical_market_links_platform_market_id_fkey`` -> retargets to platform_markets
- ``canonical_match_overrides_platform_market_id_fkey`` -> retargets to platform_markets

NOTE on ``canonical_match_log.platform_market_id``: per build spec § 0.1
Axis 2 narrative, this column was listed as a 4th already-correctly-named
canonical-side FK.  MCP-verified at probe time: this column is plain
INTEGER (NO FK constraint to markets).  No DDL impact.  Narrative
correction will land in #1177 follow-up (see V2.48 ADR amendment
candidate); no edit to ARCHITECTURE_DECISIONS.md in this PR per PM
Tyrion session 101 adjudication.

Downgrade discipline (per ``feedback_idempotent_migration_drops.md``):
reverse exact order; ``IF EXISTS`` on every DROP for partial-rollback
safety.

Revision ID: 0090
Revises: 0089
Create Date: 2026-05-11

Issues: closes Cohort 5+ Slot A; advances ADR-118 V2.48
ADR: ADR-118 V2.48 (platform-prefix rename design lock, PR #1180)
Build spec: ``memory/build_spec_0090_platform_rename_pm_memo.md``
Probe results: ``memory/build_progress_0090_mcp_probe_results.md``
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0090"
down_revision: str = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Module-level view DDL constants (Pattern 93: explicit column lists)
#
# Two sets:
#   * _VIEW_DDL_POST_RENAME -- column lists reference platform_* tables and
#     platform_*_id columns (used by upgrade()'s recreate-after-rename step)
#   * _VIEW_DDL_PRE_RENAME -- column lists reference pre-rename names
#     (used by downgrade()'s recreate-after-revert step)
#
# Source: pg_views.definition MCP probe captured session 101 at
# alembic_head=0089 + post-substitution per the FK column rename matrix.
# ---------------------------------------------------------------------------

_VIEW_DDL_POST_RENAME: dict[str, str] = {
    "current_markets": (
        "CREATE VIEW current_markets AS "
        "SELECT m.id, m.platform_id, m.platform_event_id, m.external_id, "
        "m.ticker, m.title, m.subtitle, m.market_type, m.status, "
        "m.settlement_value, m.open_time, m.close_time, m.expiration_time, "
        "m.outcome_label, m.subcategory, m.bracket_count, m.source_url, "
        "m.expiration_value, m.notional_value, m.metadata, m.created_at, "
        "m.updated_at, ms.yes_ask_price, ms.no_ask_price, ms.yes_bid_price, "
        "ms.no_bid_price, ms.last_price, ms.spread, ms.volume, "
        "ms.open_interest, ms.liquidity, ms.volume_24h, ms.previous_yes_bid, "
        "ms.previous_yes_ask, ms.previous_price, ms.yes_bid_size, "
        "ms.yes_ask_size, ms.row_start_ts, ms.row_end_ts, ms.row_current_ind "
        "FROM platform_markets m "
        "LEFT JOIN platform_market_snapshots ms "
        "ON ms.platform_market_id = m.id AND ms.row_current_ind = TRUE"
    ),
    "current_series": (
        "CREATE VIEW current_series AS "
        "SELECT series_key, platform_id, external_id, category, subcategory, "
        "title, frequency, metadata, created_at, updated_at, tags, id, "
        "row_current_ind, row_start_ts, row_end_ts "
        "FROM platform_series WHERE row_current_ind = TRUE"
    ),
    "current_edges": (
        "CREATE VIEW current_edges AS "
        "SELECT id, edge_key, model_id, expected_value, true_win_probability, "
        "market_implied_probability, market_price, confidence_level, "
        "confidence_metrics, recommended_action, created_at, row_start_ts, "
        "row_end_ts, row_current_ind, platform_market_id, actual_outcome, "
        "settlement_value, resolved_at, strategy_id, edge_status, "
        "yes_ask_price, no_ask_price, spread, volume, open_interest, "
        "last_price, liquidity, category, subcategory, execution_environment, "
        "platform_market_snapshot_id, prediction_id, "
        "platform_orderbook_snapshot_id "
        "FROM edges WHERE row_current_ind = TRUE"
    ),
    "edge_lifecycle": (
        "CREATE VIEW edge_lifecycle AS "
        "SELECT id, edge_key, platform_market_id, model_id, strategy_id, "
        "expected_value, true_win_probability, market_implied_probability, "
        "market_price, yes_ask_price, no_ask_price, edge_status, "
        "actual_outcome, settlement_value, confidence_level, "
        "execution_environment, created_at, resolved_at, "
        "CASE "
        "WHEN actual_outcome::text = 'yes'::text "
        "THEN settlement_value - market_price "
        "WHEN actual_outcome::text = 'no'::text "
        "THEN settlement_value - market_price "
        "ELSE NULL::numeric END AS realized_pnl, "
        "CASE "
        "WHEN resolved_at IS NOT NULL AND created_at IS NOT NULL "
        "THEN EXTRACT(epoch FROM (resolved_at - created_at)) / 3600.0 "
        "ELSE NULL::numeric END AS hours_to_resolution "
        "FROM edges e WHERE row_current_ind = TRUE"
    ),
    "live_trades": (
        "CREATE VIEW live_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, platform_market_id, order_id, "
        "is_taker "
        "FROM trades WHERE execution_environment::text = 'live'::text"
    ),
    "paper_trades": (
        "CREATE VIEW paper_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, platform_market_id, order_id, "
        "is_taker "
        "FROM trades WHERE execution_environment::text = 'paper'::text"
    ),
    "backtest_trades": (
        "CREATE VIEW backtest_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, platform_market_id, order_id, "
        "is_taker "
        "FROM trades WHERE execution_environment::text = 'backtest'::text"
    ),
    "training_data_trades": (
        "CREATE VIEW training_data_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, platform_market_id, order_id, "
        "is_taker "
        "FROM trades WHERE execution_environment::text = ANY "
        "((ARRAY['paper'::character varying, "
        "'backtest'::character varying])::text[])"
    ),
    "live_positions": (
        "CREATE VIEW live_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, platform_market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'live'::text "
        "AND row_current_ind = TRUE"
    ),
    "paper_positions": (
        "CREATE VIEW paper_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, platform_market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'paper'::text "
        "AND row_current_ind = TRUE"
    ),
    "backtest_positions": (
        "CREATE VIEW backtest_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, platform_market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'backtest'::text "
        "AND row_current_ind = TRUE"
    ),
    "open_positions": (
        "CREATE VIEW open_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, platform_market_id "
        "FROM positions "
        "WHERE status::text = 'open'::text AND row_current_ind = TRUE"
    ),
}

_VIEW_DDL_PRE_RENAME: dict[str, str] = {
    "current_markets": (
        "CREATE VIEW current_markets AS "
        "SELECT m.id, m.platform_id, m.event_id, m.external_id, m.ticker, "
        "m.title, m.subtitle, m.market_type, m.status, m.settlement_value, "
        "m.open_time, m.close_time, m.expiration_time, m.outcome_label, "
        "m.subcategory, m.bracket_count, m.source_url, m.expiration_value, "
        "m.notional_value, m.metadata, m.created_at, m.updated_at, "
        "ms.yes_ask_price, ms.no_ask_price, ms.yes_bid_price, ms.no_bid_price, "
        "ms.last_price, ms.spread, ms.volume, ms.open_interest, ms.liquidity, "
        "ms.volume_24h, ms.previous_yes_bid, ms.previous_yes_ask, "
        "ms.previous_price, ms.yes_bid_size, ms.yes_ask_size, ms.row_start_ts, "
        "ms.row_end_ts, ms.row_current_ind "
        "FROM markets m "
        "LEFT JOIN market_snapshots ms "
        "ON ms.market_id = m.id AND ms.row_current_ind = TRUE"
    ),
    "current_series": (
        "CREATE VIEW current_series AS "
        "SELECT series_key, platform_id, external_id, category, subcategory, "
        "title, frequency, metadata, created_at, updated_at, tags, id, "
        "row_current_ind, row_start_ts, row_end_ts "
        "FROM series WHERE row_current_ind = TRUE"
    ),
    "current_edges": (
        "CREATE VIEW current_edges AS "
        "SELECT id, edge_key, model_id, expected_value, true_win_probability, "
        "market_implied_probability, market_price, confidence_level, "
        "confidence_metrics, recommended_action, created_at, row_start_ts, "
        "row_end_ts, row_current_ind, market_id, actual_outcome, "
        "settlement_value, resolved_at, strategy_id, edge_status, "
        "yes_ask_price, no_ask_price, spread, volume, open_interest, "
        "last_price, liquidity, category, subcategory, execution_environment, "
        "market_snapshot_id, prediction_id, orderbook_snapshot_id "
        "FROM edges WHERE row_current_ind = TRUE"
    ),
    "edge_lifecycle": (
        "CREATE VIEW edge_lifecycle AS "
        "SELECT id, edge_key, market_id, model_id, strategy_id, "
        "expected_value, true_win_probability, market_implied_probability, "
        "market_price, yes_ask_price, no_ask_price, edge_status, "
        "actual_outcome, settlement_value, confidence_level, "
        "execution_environment, created_at, resolved_at, "
        "CASE "
        "WHEN actual_outcome::text = 'yes'::text "
        "THEN settlement_value - market_price "
        "WHEN actual_outcome::text = 'no'::text "
        "THEN settlement_value - market_price "
        "ELSE NULL::numeric END AS realized_pnl, "
        "CASE "
        "WHEN resolved_at IS NOT NULL AND created_at IS NOT NULL "
        "THEN EXTRACT(epoch FROM (resolved_at - created_at)) / 3600.0 "
        "ELSE NULL::numeric END AS hours_to_resolution "
        "FROM edges e WHERE row_current_ind = TRUE"
    ),
    "live_trades": (
        "CREATE VIEW live_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, market_id, order_id, is_taker "
        "FROM trades WHERE execution_environment::text = 'live'::text"
    ),
    "paper_trades": (
        "CREATE VIEW paper_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, market_id, order_id, is_taker "
        "FROM trades WHERE execution_environment::text = 'paper'::text"
    ),
    "backtest_trades": (
        "CREATE VIEW backtest_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, market_id, order_id, is_taker "
        "FROM trades WHERE execution_environment::text = 'backtest'::text"
    ),
    "training_data_trades": (
        "CREATE VIEW training_data_trades AS "
        "SELECT id, platform_id, side, price, quantity, fees, execution_time, "
        "calculated_probability, market_price, edge_value, edge_at_execution, "
        "confidence_at_execution, trade_metadata, fill_time_ms, slippage, "
        "created_at, execution_environment, market_id, order_id, is_taker "
        "FROM trades WHERE execution_environment::text = ANY "
        "((ARRAY['paper'::character varying, "
        "'backtest'::character varying])::text[])"
    ),
    "live_positions": (
        "CREATE VIEW live_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'live'::text "
        "AND row_current_ind = TRUE"
    ),
    "paper_positions": (
        "CREATE VIEW paper_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'paper'::text "
        "AND row_current_ind = TRUE"
    ),
    "backtest_positions": (
        "CREATE VIEW backtest_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, market_id "
        "FROM positions "
        "WHERE execution_environment::text = 'backtest'::text "
        "AND row_current_ind = TRUE"
    ),
    "open_positions": (
        "CREATE VIEW open_positions AS "
        "SELECT id, position_key, platform_id, strategy_id, model_id, side, "
        "entry_price, quantity, current_price, fees, status, unrealized_pnl, "
        "unrealized_pnl_pct, realized_pnl, trailing_stop_state, target_price, "
        "stop_loss_price, entry_time, exit_time, last_check_time, exit_price, "
        "position_metadata, exit_reason, exit_priority, "
        "calculated_probability, edge_at_entry, market_price_at_entry, "
        "last_update, created_at, updated_at, row_start_ts, row_end_ts, "
        "row_current_ind, execution_environment, market_id "
        "FROM positions "
        "WHERE status::text = 'open'::text AND row_current_ind = TRUE"
    ),
}

# Ordering of view DROP/RECREATE matters when views reference each other.
# Verified via pg_views: no view-on-view dependencies among these 12.
# Recreate order matches drop-reverse order (alphabetical is safe).
_VIEW_NAMES: tuple[str, ...] = (
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
)


def upgrade() -> None:
    """Apply platform-prefix rename per build spec § 2.1 11-step order.

    Order (strictly enforced -- view-pin dependency on column references):

    1. DROP all 12 affected views (frees columns + tables for rename).
    2. Rename FK columns on leave-alone tables (so when the target tables
       rename, the FK constraint can be recreated against the new column
       names with new constraint names).
    3. Rename FK columns on renaming tables (e.g., markets.event_id ->
       markets.platform_event_id) BEFORE table rename so the column lives
       on the source table at rename time.
    4. Drop FK constraints that need recreating with new column names.
       PG auto-retargets FK references when the target table renames,
       but constraint names do NOT auto-rename and column names attached
       to FK constraints are preserved at constraint-definition time --
       so we must DROP-and-recreate the constraints we want to rename.
    5. Rename 7 base tables (RENAME TO).
    6. Rename 7 PK indexes (ALTER INDEX RENAME TO).
    7. Rename 5 unique constraints (ALTER TABLE RENAME CONSTRAINT).
    8. Rename idx_* indexes (~30 ALTER INDEX RENAME TO).
    9. Rename 7 sequences (ALTER SEQUENCE RENAME TO).
    10. Recreate FK constraints with new column + constraint names.
    11. Recreate 12 views with EXPLICIT column lists (Pattern 93).

    Single transaction (Alembic default; env.py uses
    ``context.begin_transaction()``).  Either all 12 steps (0 + 1-11)
    succeed and commit atomically, or none persist.
    """
    # ------------------------------------------------------------------
    # Step 0: Normalize trailing-`1` cruft on markets-table objects.
    #
    # Historical cruft from pre-arc DB activity left 9 markets-table
    # objects (PK constraint, 7 other constraints, 1 sequence) with
    # trailing `1` in their names on at least one DB.  Phase 6 (session
    # 103) discovered cross-env drift: dev DB had clean ``markets_pkey``
    # but trailing-1 on the other 8 objects; test DB had trailing-1 on
    # all 9.  Build spec OQ-H1 PRESERVE adjudication is OVERRIDDEN to
    # NORMALIZE per session-103 user adjudication (B forward-only:
    # downgrade leaves names clean; does NOT re-introduce trailing-1).
    #
    # Idempotent via DO/EXCEPTION: each rename is a no-op on DBs where
    # the trailing-1 version doesn't exist OR cannot be renamed (e.g.,
    # NOT NULL constraints on PG < 17).  Survivors of any failed rename
    # are tracked as Cohort 5+ follow-on cleanup (see PR-Y body).
    # ------------------------------------------------------------------
    op.execute("""
DO $$
BEGIN
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_pkey1 TO markets_pkey;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_external_id_not_null1 TO markets_external_id_not_null;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_id_not_null1 TO markets_id_not_null;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_market_type_check1 TO markets_market_type_check;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_platform_id_fkey1 TO markets_platform_id_fkey;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_settlement_value_check1 TO markets_settlement_value_check;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_status_check1 TO markets_status_check;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER TABLE markets RENAME CONSTRAINT markets_title_not_null1 TO markets_title_not_null;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
    BEGIN ALTER SEQUENCE markets_id_seq1 RENAME TO markets_id_seq;
    EXCEPTION WHEN undefined_object THEN NULL;
              WHEN undefined_table THEN NULL;
              WHEN duplicate_object THEN NULL; END;
END $$;
""")

    # ------------------------------------------------------------------
    # Step 1: DROP all 12 affected views.
    # ------------------------------------------------------------------
    for view in _VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS {view}")

    # ------------------------------------------------------------------
    # Step 2: Rename FK columns on leave-alone tables (target retargets
    # via PG auto-FK update during step 5; column names update here).
    # ------------------------------------------------------------------
    # edges (3 column renames)
    op.execute("ALTER TABLE edges RENAME COLUMN market_id TO platform_market_id")
    op.execute("ALTER TABLE edges RENAME COLUMN market_snapshot_id TO platform_market_snapshot_id")
    op.execute(
        "ALTER TABLE edges RENAME COLUMN orderbook_snapshot_id TO platform_orderbook_snapshot_id"
    )
    # orders (2 column renames)
    op.execute("ALTER TABLE orders RENAME COLUMN market_id TO platform_market_id")
    op.execute(
        "ALTER TABLE orders RENAME COLUMN orderbook_snapshot_id TO platform_orderbook_snapshot_id"
    )
    # positions (1 column rename)
    op.execute("ALTER TABLE positions RENAME COLUMN market_id TO platform_market_id")
    # trades (1 column rename)
    op.execute("ALTER TABLE trades RENAME COLUMN market_id TO platform_market_id")
    # predictions (2 column renames; OQ-H5: rename FK cols even though
    # parent table stays unprefixed)
    op.execute("ALTER TABLE predictions RENAME COLUMN event_id TO platform_event_id")
    op.execute("ALTER TABLE predictions RENAME COLUMN market_id TO platform_market_id")

    # ------------------------------------------------------------------
    # Step 3: Rename FK columns on renaming tables (BEFORE table rename
    # so column lives on the source-name table).
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE markets RENAME COLUMN event_id TO platform_event_id")
    op.execute("ALTER TABLE events RENAME COLUMN series_id TO platform_series_id")
    op.execute("ALTER TABLE market_snapshots RENAME COLUMN market_id TO platform_market_id")
    op.execute("ALTER TABLE market_trades RENAME COLUMN market_id TO platform_market_id")
    op.execute("ALTER TABLE orderbook_snapshots RENAME COLUMN market_id TO platform_market_id")
    op.execute("ALTER TABLE settlements RENAME COLUMN market_id TO platform_market_id")

    # ------------------------------------------------------------------
    # Step 4: Drop FK constraints we want to rename (the constraint
    # names embed the old table name, so they must be dropped and
    # recreated with new names).  PG auto-retargets the FK destination
    # when the target table renames in step 5 -- but constraint names
    # are NOT auto-rewritten.  We drop here, rename tables in step 5,
    # then recreate in step 10 with platform_*-prefixed constraint
    # names.
    #
    # NOTE on canonical-side FKs (canonical_event_links,
    # canonical_market_links, canonical_match_overrides): these are
    # already-correctly-named with ``platform_*`` columns; their
    # constraint names also start with the canonical table name (which
    # is not changing).  PG auto-retargets these when the target table
    # renames in step 5 -- no DROP/recreate needed.
    # ------------------------------------------------------------------
    fks_to_drop: list[tuple[str, str]] = [
        # (table, constraint_name)
        # FKs into renaming tables
        ("events", "events_game_id_fkey"),
        ("events", "events_platform_id_fkey"),
        ("events", "events_series_id_fkey"),
        ("markets", "markets_event_id_fkey"),
        ("markets", "markets_platform_id_fkey"),  # post step-0 normalize
        ("market_snapshots", "market_snapshots_market_id_fkey"),
        ("market_trades", "market_trades_market_id_fkey"),
        ("market_trades", "market_trades_platform_id_fkey"),
        ("orderbook_snapshots", "orderbook_snapshots_market_id_fkey"),
        ("series", "series_platform_id_fkey"),
        ("settlements", "settlements_market_id_fkey"),
        ("settlements", "settlements_order_id_fkey"),
        ("settlements", "settlements_platform_id_fkey"),
        ("settlements", "settlements_position_id_fkey"),
        # FKs on leave-alone tables that point at renaming tables
        ("edges", "edges_market_id_fkey"),
        ("edges", "edges_market_snapshot_id_fkey"),
        ("edges", "edges_orderbook_snapshot_id_fkey"),
        ("orders", "orders_market_id_fkey"),
        ("orders", "orders_orderbook_snapshot_id_fkey"),
        ("positions", "positions_market_id_fkey"),
        ("trades", "trades_market_id_fkey"),
        ("predictions", "predictions_event_id_fkey"),
        ("predictions", "predictions_market_id_fkey"),
    ]
    for tbl, con in fks_to_drop:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT {con}")

    # ------------------------------------------------------------------
    # Step 5: Rename 7 base tables.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE events RENAME TO platform_events")
    op.execute("ALTER TABLE markets RENAME TO platform_markets")
    op.execute("ALTER TABLE series RENAME TO platform_series")
    op.execute("ALTER TABLE market_snapshots RENAME TO platform_market_snapshots")
    op.execute("ALTER TABLE market_trades RENAME TO platform_market_trades")
    op.execute("ALTER TABLE orderbook_snapshots RENAME TO platform_orderbook_snapshots")
    op.execute("ALTER TABLE settlements RENAME TO platform_settlements")

    # ------------------------------------------------------------------
    # Step 6: Rename 7 PK indexes.
    # ------------------------------------------------------------------
    op.execute("ALTER INDEX events_pkey RENAME TO platform_events_pkey")
    op.execute("ALTER INDEX markets_pkey RENAME TO platform_markets_pkey")
    op.execute("ALTER INDEX series_pkey RENAME TO platform_series_pkey")
    op.execute("ALTER INDEX market_snapshots_pkey RENAME TO platform_market_snapshots_pkey")
    op.execute("ALTER INDEX market_trades_pkey RENAME TO platform_market_trades_pkey")
    op.execute("ALTER INDEX orderbook_snapshots_pkey RENAME TO platform_orderbook_snapshots_pkey")
    op.execute("ALTER INDEX settlements_pkey RENAME TO platform_settlements_pkey")

    # ------------------------------------------------------------------
    # Step 7: Rename 5 unique constraints.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE platform_events "
        "RENAME CONSTRAINT uq_events_platform_external "
        "TO uq_platform_events_platform_external"
    )
    op.execute(
        "ALTER TABLE platform_market_trades "
        "RENAME CONSTRAINT uq_market_trades_platform_external "
        "TO uq_platform_market_trades_platform_external"
    )
    op.execute(
        "ALTER TABLE platform_markets "
        "RENAME CONSTRAINT markets_platform_id_external_id_key "
        "TO platform_markets_platform_id_external_id_key"
    )
    op.execute(
        "ALTER TABLE platform_markets "
        "RENAME CONSTRAINT markets_ticker_key "
        "TO platform_markets_ticker_key"
    )
    # idx_markets_market_key is a UNIQUE INDEX (not constraint); rename
    # via ALTER INDEX in step 8.

    # ------------------------------------------------------------------
    # Step 8: Rename idx_* indexes (~30).  Verified one-to-one by
    # pg_indexes probe.
    # ------------------------------------------------------------------
    idx_renames: list[tuple[str, str]] = [
        # events table indexes
        ("idx_events_event_key", "idx_platform_events_event_key"),
        ("idx_events_game_id", "idx_platform_events_game_id"),
        ("idx_events_platform", "idx_platform_events_platform"),
        ("idx_events_series", "idx_platform_events_series"),
        ("idx_events_start_time", "idx_platform_events_start_time"),
        ("idx_events_status", "idx_platform_events_status"),
        # markets table indexes
        ("idx_markets_close_time", "idx_platform_markets_close_time"),
        ("idx_markets_event", "idx_platform_markets_event"),
        ("idx_markets_expiration_time", "idx_platform_markets_expiration_time"),
        ("idx_markets_market_key", "idx_platform_markets_market_key"),
        ("idx_markets_platform", "idx_platform_markets_platform"),
        ("idx_markets_status", "idx_platform_markets_status"),
        ("idx_markets_subcategory", "idx_platform_markets_subcategory"),
        # market_snapshots table indexes
        ("idx_market_snapshots_current", "idx_platform_market_snapshots_current"),
        ("idx_market_snapshots_history", "idx_platform_market_snapshots_history"),
        ("idx_market_snapshots_market", "idx_platform_market_snapshots_market"),
        (
            "idx_market_snapshots_unique_current",
            "idx_platform_market_snapshots_unique_current",
        ),
        # market_trades table indexes
        ("idx_market_trades_market", "idx_platform_market_trades_market"),
        ("idx_market_trades_market_time", "idx_platform_market_trades_market_time"),
        ("idx_market_trades_time", "idx_platform_market_trades_time"),
        # orderbook_snapshots table indexes
        ("idx_orderbook_imbalance", "idx_platform_orderbook_snapshots_imbalance"),
        ("idx_orderbook_market", "idx_platform_orderbook_snapshots_market"),
        ("idx_orderbook_spread", "idx_platform_orderbook_snapshots_spread"),
        ("idx_orderbook_time", "idx_platform_orderbook_snapshots_time"),
        # series table indexes
        ("idx_series_category", "idx_platform_series_category"),
        ("idx_series_current", "idx_platform_series_current"),
        ("idx_series_platform", "idx_platform_series_platform"),
        (
            "idx_series_platform_external_current",
            "idx_platform_series_platform_external_current",
        ),
        ("idx_series_tags", "idx_platform_series_tags"),
        ("idx_series_unique_current", "idx_platform_series_unique_current"),
        # settlements table indexes
        ("idx_settlements_market", "idx_platform_settlements_market"),
        ("idx_settlements_order_id", "idx_platform_settlements_order_id"),
        ("idx_settlements_position_id", "idx_platform_settlements_position_id"),
    ]
    for old, new in idx_renames:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")

    # ------------------------------------------------------------------
    # Step 9: Rename 7 sequences (preserve markets_id_seq1 trailing `1`
    # per OQ-H1).
    # ------------------------------------------------------------------
    op.execute("ALTER SEQUENCE events_id_seq RENAME TO platform_events_id_seq")
    # post step-0 normalize: markets_id_seq1 -> markets_id_seq; now rename to platform form
    op.execute("ALTER SEQUENCE markets_id_seq RENAME TO platform_markets_id_seq")
    op.execute("ALTER SEQUENCE series_id_seq RENAME TO platform_series_id_seq")
    op.execute("ALTER SEQUENCE market_snapshots_id_seq RENAME TO platform_market_snapshots_id_seq")
    op.execute("ALTER SEQUENCE market_trades_id_seq RENAME TO platform_market_trades_id_seq")
    op.execute(
        "ALTER SEQUENCE orderbook_snapshots_id_seq RENAME TO platform_orderbook_snapshots_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE settlements_settlement_id_seq "
        "RENAME TO platform_settlements_settlement_id_seq"
    )

    # ------------------------------------------------------------------
    # Step 10: Recreate FK constraints with new column + new constraint
    # names (mirror of step 4 DROP list; columns now reference renamed
    # tables / renamed columns).  All ON DELETE clauses match the live
    # constraint definitions probed pre-upgrade.
    # ------------------------------------------------------------------
    # platform_events
    op.execute(
        "ALTER TABLE platform_events "
        "ADD CONSTRAINT platform_events_game_id_fkey "
        "FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_events "
        "ADD CONSTRAINT platform_events_platform_id_fkey "
        "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_events "
        "ADD CONSTRAINT platform_events_platform_series_id_fkey "
        "FOREIGN KEY (platform_series_id) REFERENCES platform_series(id) "
        "ON DELETE RESTRICT"
    )
    # platform_markets
    op.execute(
        "ALTER TABLE platform_markets "
        "ADD CONSTRAINT platform_markets_platform_event_id_fkey "
        "FOREIGN KEY (platform_event_id) REFERENCES platform_events(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_markets "
        "ADD CONSTRAINT platform_markets_platform_id_fkey "
        "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
        "ON DELETE RESTRICT"
    )
    # platform_series
    op.execute(
        "ALTER TABLE platform_series "
        "ADD CONSTRAINT platform_series_platform_id_fkey "
        "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
        "ON DELETE RESTRICT"
    )
    # platform_market_snapshots
    op.execute(
        "ALTER TABLE platform_market_snapshots "
        "ADD CONSTRAINT platform_market_snapshots_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    # platform_market_trades
    op.execute(
        "ALTER TABLE platform_market_trades "
        "ADD CONSTRAINT platform_market_trades_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_market_trades "
        "ADD CONSTRAINT platform_market_trades_platform_id_fkey "
        "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
        "ON DELETE RESTRICT"
    )
    # platform_orderbook_snapshots
    op.execute(
        "ALTER TABLE platform_orderbook_snapshots "
        "ADD CONSTRAINT platform_orderbook_snapshots_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    # platform_settlements
    op.execute(
        "ALTER TABLE platform_settlements "
        "ADD CONSTRAINT platform_settlements_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_settlements "
        "ADD CONSTRAINT platform_settlements_order_id_fkey "
        "FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_settlements "
        "ADD CONSTRAINT platform_settlements_platform_id_fkey "
        "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE platform_settlements "
        "ADD CONSTRAINT platform_settlements_position_id_fkey "
        "FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE RESTRICT"
    )
    # edges (leave-alone table, FKs target renamed tables)
    op.execute(
        "ALTER TABLE edges "
        "ADD CONSTRAINT edges_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE edges "
        "ADD CONSTRAINT edges_platform_market_snapshot_id_fkey "
        "FOREIGN KEY (platform_market_snapshot_id) "
        "REFERENCES platform_market_snapshots(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE edges "
        "ADD CONSTRAINT edges_platform_orderbook_snapshot_id_fkey "
        "FOREIGN KEY (platform_orderbook_snapshot_id) "
        "REFERENCES platform_orderbook_snapshots(id) ON DELETE RESTRICT"
    )
    # orders
    op.execute(
        "ALTER TABLE orders "
        "ADD CONSTRAINT orders_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE orders "
        "ADD CONSTRAINT orders_platform_orderbook_snapshot_id_fkey "
        "FOREIGN KEY (platform_orderbook_snapshot_id) "
        "REFERENCES platform_orderbook_snapshots(id) ON DELETE RESTRICT"
    )
    # positions
    op.execute(
        "ALTER TABLE positions "
        "ADD CONSTRAINT positions_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    # trades
    op.execute(
        "ALTER TABLE trades "
        "ADD CONSTRAINT trades_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )
    # predictions
    op.execute(
        "ALTER TABLE predictions "
        "ADD CONSTRAINT predictions_platform_event_id_fkey "
        "FOREIGN KEY (platform_event_id) REFERENCES platform_events(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE predictions "
        "ADD CONSTRAINT predictions_platform_market_id_fkey "
        "FOREIGN KEY (platform_market_id) REFERENCES platform_markets(id) "
        "ON DELETE RESTRICT"
    )

    # ------------------------------------------------------------------
    # Step 11: Recreate 12 views with EXPLICIT column lists referencing
    # platform_* tables / platform_*_id columns (Pattern 93).
    # ------------------------------------------------------------------
    for view in _VIEW_NAMES:
        op.execute(_VIEW_DDL_POST_RENAME[view])


def downgrade() -> None:
    """Reverse exact order; IF EXISTS on every DROP.

    Round-trip parity contract per Epic #1071: downgrade restores the
    pre-rename schema byte-equal at the structural level.  Sequence
    values + row data are preserved (rename is a metadata-only operation
    in PG).

    Idempotent-drops discipline per
    ``feedback_idempotent_migration_drops.md`` (slot 0061 incident):
    every DROP uses IF EXISTS so re-running downgrade on a partially
    downgraded DB is a no-op rather than a crash.
    """
    # ------------------------------------------------------------------
    # Step 11-rev: DROP all 12 views.
    # ------------------------------------------------------------------
    for view in _VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS {view}")

    # ------------------------------------------------------------------
    # Step 10-rev: DROP FK constraints (recreated in step 4-rev below).
    # ------------------------------------------------------------------
    fks_to_drop_for_revert: list[tuple[str, str]] = [
        ("platform_events", "platform_events_game_id_fkey"),
        ("platform_events", "platform_events_platform_id_fkey"),
        ("platform_events", "platform_events_platform_series_id_fkey"),
        ("platform_markets", "platform_markets_platform_event_id_fkey"),
        ("platform_markets", "platform_markets_platform_id_fkey"),
        (
            "platform_market_snapshots",
            "platform_market_snapshots_platform_market_id_fkey",
        ),
        (
            "platform_market_trades",
            "platform_market_trades_platform_market_id_fkey",
        ),
        ("platform_market_trades", "platform_market_trades_platform_id_fkey"),
        (
            "platform_orderbook_snapshots",
            "platform_orderbook_snapshots_platform_market_id_fkey",
        ),
        ("platform_series", "platform_series_platform_id_fkey"),
        ("platform_settlements", "platform_settlements_platform_market_id_fkey"),
        ("platform_settlements", "platform_settlements_order_id_fkey"),
        ("platform_settlements", "platform_settlements_platform_id_fkey"),
        ("platform_settlements", "platform_settlements_position_id_fkey"),
        ("edges", "edges_platform_market_id_fkey"),
        ("edges", "edges_platform_market_snapshot_id_fkey"),
        ("edges", "edges_platform_orderbook_snapshot_id_fkey"),
        ("orders", "orders_platform_market_id_fkey"),
        ("orders", "orders_platform_orderbook_snapshot_id_fkey"),
        ("positions", "positions_platform_market_id_fkey"),
        ("trades", "trades_platform_market_id_fkey"),
        ("predictions", "predictions_platform_event_id_fkey"),
        ("predictions", "predictions_platform_market_id_fkey"),
    ]
    for tbl, con in fks_to_drop_for_revert:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {con}")

    # ------------------------------------------------------------------
    # Step 9-rev: Revert 7 sequence renames.
    # ------------------------------------------------------------------
    op.execute("ALTER SEQUENCE IF EXISTS platform_events_id_seq RENAME TO events_id_seq")
    # Forward-only OQ-H1 normalization (session 103): no trailing-1 restore
    op.execute("ALTER SEQUENCE IF EXISTS platform_markets_id_seq RENAME TO markets_id_seq")
    op.execute("ALTER SEQUENCE IF EXISTS platform_series_id_seq RENAME TO series_id_seq")
    op.execute(
        "ALTER SEQUENCE IF EXISTS platform_market_snapshots_id_seq "
        "RENAME TO market_snapshots_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS platform_market_trades_id_seq RENAME TO market_trades_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS platform_orderbook_snapshots_id_seq "
        "RENAME TO orderbook_snapshots_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS platform_settlements_settlement_id_seq "
        "RENAME TO settlements_settlement_id_seq"
    )

    # ------------------------------------------------------------------
    # Step 8-rev: Revert idx_* index renames (reverse order).
    # ------------------------------------------------------------------
    idx_renames_revert: list[tuple[str, str]] = [
        # Reverse of upgrade step 8 list (target -> source).
        ("idx_platform_settlements_position_id", "idx_settlements_position_id"),
        ("idx_platform_settlements_order_id", "idx_settlements_order_id"),
        ("idx_platform_settlements_market", "idx_settlements_market"),
        ("idx_platform_series_unique_current", "idx_series_unique_current"),
        ("idx_platform_series_tags", "idx_series_tags"),
        (
            "idx_platform_series_platform_external_current",
            "idx_series_platform_external_current",
        ),
        ("idx_platform_series_platform", "idx_series_platform"),
        ("idx_platform_series_current", "idx_series_current"),
        ("idx_platform_series_category", "idx_series_category"),
        ("idx_platform_orderbook_snapshots_time", "idx_orderbook_time"),
        ("idx_platform_orderbook_snapshots_spread", "idx_orderbook_spread"),
        ("idx_platform_orderbook_snapshots_market", "idx_orderbook_market"),
        ("idx_platform_orderbook_snapshots_imbalance", "idx_orderbook_imbalance"),
        ("idx_platform_market_trades_time", "idx_market_trades_time"),
        ("idx_platform_market_trades_market_time", "idx_market_trades_market_time"),
        ("idx_platform_market_trades_market", "idx_market_trades_market"),
        (
            "idx_platform_market_snapshots_unique_current",
            "idx_market_snapshots_unique_current",
        ),
        ("idx_platform_market_snapshots_market", "idx_market_snapshots_market"),
        ("idx_platform_market_snapshots_history", "idx_market_snapshots_history"),
        ("idx_platform_market_snapshots_current", "idx_market_snapshots_current"),
        ("idx_platform_markets_subcategory", "idx_markets_subcategory"),
        ("idx_platform_markets_status", "idx_markets_status"),
        ("idx_platform_markets_platform", "idx_markets_platform"),
        ("idx_platform_markets_market_key", "idx_markets_market_key"),
        ("idx_platform_markets_expiration_time", "idx_markets_expiration_time"),
        ("idx_platform_markets_event", "idx_markets_event"),
        ("idx_platform_markets_close_time", "idx_markets_close_time"),
        ("idx_platform_events_status", "idx_events_status"),
        ("idx_platform_events_start_time", "idx_events_start_time"),
        ("idx_platform_events_series", "idx_events_series"),
        ("idx_platform_events_platform", "idx_events_platform"),
        ("idx_platform_events_game_id", "idx_events_game_id"),
        ("idx_platform_events_event_key", "idx_events_event_key"),
    ]
    for old, new in idx_renames_revert:
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")

    # ------------------------------------------------------------------
    # Step 7-rev: Revert 4 unique constraint renames (idx_markets_market_key
    # handled in step 8-rev).
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE platform_markets "
        "RENAME CONSTRAINT platform_markets_ticker_key TO markets_ticker_key"
    )
    op.execute(
        "ALTER TABLE platform_markets "
        "RENAME CONSTRAINT platform_markets_platform_id_external_id_key "
        "TO markets_platform_id_external_id_key"
    )
    op.execute(
        "ALTER TABLE platform_market_trades "
        "RENAME CONSTRAINT uq_platform_market_trades_platform_external "
        "TO uq_market_trades_platform_external"
    )
    op.execute(
        "ALTER TABLE platform_events "
        "RENAME CONSTRAINT uq_platform_events_platform_external "
        "TO uq_events_platform_external"
    )

    # ------------------------------------------------------------------
    # Step 6-rev: Revert 7 PK index renames.
    # ------------------------------------------------------------------
    op.execute("ALTER INDEX IF EXISTS platform_settlements_pkey RENAME TO settlements_pkey")
    op.execute(
        "ALTER INDEX IF EXISTS platform_orderbook_snapshots_pkey RENAME TO orderbook_snapshots_pkey"
    )
    op.execute("ALTER INDEX IF EXISTS platform_market_trades_pkey RENAME TO market_trades_pkey")
    op.execute(
        "ALTER INDEX IF EXISTS platform_market_snapshots_pkey RENAME TO market_snapshots_pkey"
    )
    op.execute("ALTER INDEX IF EXISTS platform_series_pkey RENAME TO series_pkey")
    op.execute("ALTER INDEX IF EXISTS platform_markets_pkey RENAME TO markets_pkey")
    op.execute("ALTER INDEX IF EXISTS platform_events_pkey RENAME TO events_pkey")

    # ------------------------------------------------------------------
    # Step 5-rev: Revert 7 base table renames.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE platform_settlements RENAME TO settlements")
    op.execute("ALTER TABLE platform_orderbook_snapshots RENAME TO orderbook_snapshots")
    op.execute("ALTER TABLE platform_market_trades RENAME TO market_trades")
    op.execute("ALTER TABLE platform_market_snapshots RENAME TO market_snapshots")
    op.execute("ALTER TABLE platform_series RENAME TO series")
    op.execute("ALTER TABLE platform_markets RENAME TO markets")
    op.execute("ALTER TABLE platform_events RENAME TO events")

    # ------------------------------------------------------------------
    # Step 4-rev: Recreate dropped FK constraints with original names +
    # original column names.  Source columns get renamed back in
    # steps 3-rev / 2-rev below; the columns currently reference the
    # post-rename names, so we revert column names FIRST then recreate
    # FKs.  WAIT -- order matters here: we need to revert the FK column
    # names BEFORE recreating the FK constraints (otherwise the FK
    # constraint definitions reference columns that don't exist under
    # those names anymore).  So Steps 4-rev moves to AFTER 3-rev / 2-rev.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Step 3-rev: Revert FK column renames on (now reverted) base
    # tables.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE settlements RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE orderbook_snapshots RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE market_trades RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE market_snapshots RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE events RENAME COLUMN platform_series_id TO series_id")
    op.execute("ALTER TABLE markets RENAME COLUMN platform_event_id TO event_id")

    # ------------------------------------------------------------------
    # Step 2-rev: Revert FK column renames on leave-alone tables.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE predictions RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE predictions RENAME COLUMN platform_event_id TO event_id")
    op.execute("ALTER TABLE trades RENAME COLUMN platform_market_id TO market_id")
    op.execute("ALTER TABLE positions RENAME COLUMN platform_market_id TO market_id")
    op.execute(
        "ALTER TABLE orders RENAME COLUMN platform_orderbook_snapshot_id TO orderbook_snapshot_id"
    )
    op.execute("ALTER TABLE orders RENAME COLUMN platform_market_id TO market_id")
    op.execute(
        "ALTER TABLE edges RENAME COLUMN platform_orderbook_snapshot_id TO orderbook_snapshot_id"
    )
    op.execute("ALTER TABLE edges RENAME COLUMN platform_market_snapshot_id TO market_snapshot_id")
    op.execute("ALTER TABLE edges RENAME COLUMN platform_market_id TO market_id")

    # ------------------------------------------------------------------
    # Step 4-rev (deferred): Recreate dropped FK constraints with
    # original names + original column names.  Columns now exist under
    # pre-rename names; tables exist under pre-rename names.
    # ------------------------------------------------------------------
    fks_to_recreate_for_revert: list[tuple[str, str]] = [
        (
            "events",
            "ALTER TABLE events ADD CONSTRAINT events_game_id_fkey "
            "FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE RESTRICT",
        ),
        (
            "events",
            "ALTER TABLE events ADD CONSTRAINT events_platform_id_fkey "
            "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
            "ON DELETE RESTRICT",
        ),
        (
            "events",
            "ALTER TABLE events ADD CONSTRAINT events_series_id_fkey "
            "FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE RESTRICT",
        ),
        (
            "markets",
            "ALTER TABLE markets ADD CONSTRAINT markets_event_id_fkey "
            "FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE RESTRICT",
        ),
        (
            "markets",
            # Forward-only OQ-H1 normalization (session 103): no trailing-1 restore
            "ALTER TABLE markets ADD CONSTRAINT markets_platform_id_fkey "
            "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
            "ON DELETE RESTRICT",
        ),
        (
            "market_snapshots",
            "ALTER TABLE market_snapshots ADD CONSTRAINT "
            "market_snapshots_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "market_trades",
            "ALTER TABLE market_trades ADD CONSTRAINT "
            "market_trades_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "market_trades",
            "ALTER TABLE market_trades ADD CONSTRAINT "
            "market_trades_platform_id_fkey "
            "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
            "ON DELETE RESTRICT",
        ),
        (
            "orderbook_snapshots",
            "ALTER TABLE orderbook_snapshots ADD CONSTRAINT "
            "orderbook_snapshots_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "series",
            "ALTER TABLE series ADD CONSTRAINT series_platform_id_fkey "
            "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
            "ON DELETE RESTRICT",
        ),
        (
            "settlements",
            "ALTER TABLE settlements ADD CONSTRAINT settlements_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "settlements",
            "ALTER TABLE settlements ADD CONSTRAINT settlements_order_id_fkey "
            "FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE RESTRICT",
        ),
        (
            "settlements",
            "ALTER TABLE settlements ADD CONSTRAINT settlements_platform_id_fkey "
            "FOREIGN KEY (platform_id) REFERENCES platforms(platform_id) "
            "ON DELETE RESTRICT",
        ),
        (
            "settlements",
            "ALTER TABLE settlements ADD CONSTRAINT settlements_position_id_fkey "
            "FOREIGN KEY (position_id) REFERENCES positions(id) "
            "ON DELETE RESTRICT",
        ),
        (
            "edges",
            "ALTER TABLE edges ADD CONSTRAINT edges_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "edges",
            "ALTER TABLE edges ADD CONSTRAINT edges_market_snapshot_id_fkey "
            "FOREIGN KEY (market_snapshot_id) REFERENCES market_snapshots(id) "
            "ON DELETE RESTRICT",
        ),
        (
            "edges",
            "ALTER TABLE edges ADD CONSTRAINT edges_orderbook_snapshot_id_fkey "
            "FOREIGN KEY (orderbook_snapshot_id) REFERENCES orderbook_snapshots(id) "
            "ON DELETE RESTRICT",
        ),
        (
            "orders",
            "ALTER TABLE orders ADD CONSTRAINT orders_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "orders",
            "ALTER TABLE orders ADD CONSTRAINT orders_orderbook_snapshot_id_fkey "
            "FOREIGN KEY (orderbook_snapshot_id) REFERENCES orderbook_snapshots(id) "
            "ON DELETE RESTRICT",
        ),
        (
            "positions",
            "ALTER TABLE positions ADD CONSTRAINT positions_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "trades",
            "ALTER TABLE trades ADD CONSTRAINT trades_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
        (
            "predictions",
            "ALTER TABLE predictions ADD CONSTRAINT predictions_event_id_fkey "
            "FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE RESTRICT",
        ),
        (
            "predictions",
            "ALTER TABLE predictions ADD CONSTRAINT predictions_market_id_fkey "
            "FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE RESTRICT",
        ),
    ]
    for _tbl, ddl in fks_to_recreate_for_revert:
        op.execute(ddl)

    # ------------------------------------------------------------------
    # Step 1-rev: Recreate 12 views with EXPLICIT column lists
    # referencing pre-rename names.
    # ------------------------------------------------------------------
    for view in _VIEW_NAMES:
        op.execute(_VIEW_DDL_PRE_RENAME[view])
