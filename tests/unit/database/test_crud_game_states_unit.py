"""Unit tests for crud_game_states module — extracted from test_crud_operations_unit.py (split per #893 Option 1).

Covers game_states SCD Type 2 and the games dimension (both live in crud_game_states.py):
- create_game_state / get_current_game_state / get_game_state_history / upsert_game_state
- get_live_games / get_games_by_date
- get_or_create_game (games dimension upsert)
- update_game_result (derived final-score fields)
- game_state_changed (change detection, incl. sport-aware filtering)
- find_game_by_matchup (league-to-sport mapping + lookup)
- derive_game_status (Slot 4 SSOT helper -- new)

Slot 4 (Migration 0089) note:
    ``game_states.game_status`` was DROPPED.  ``upsert_game_state`` /
    ``create_game_state`` / ``game_state_changed`` no longer accept
    a ``game_status`` kwarg.  Tests below were updated en-masse to
    drop the kwarg + drop ``game_status`` keys from ``current`` row
    fixtures.  Status-change-detection assertions were rewritten to
    exercise score / period / situation deltas (the remaining 3 SCD2
    change-detection signals).

    ``get_or_create_game(... game_status=...)`` calls remain unchanged
    -- that function writes to the ``games`` table (NOT game_states),
    and Slot 4 deliberately preserves ``games.game_status`` as the
    authoritative source.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from precog.database.crud_game_states import (
    LEAGUE_SPORT_CATEGORY,
    TRACKED_SITUATION_KEYS,
    create_game_state,
    derive_game_status,
    find_game_by_matchup,
    game_state_changed,
    get_current_game_state,
    get_game_state_history,
    get_games_by_date,
    get_live_games,
    get_or_create_game,
    update_game_result,
    upsert_game_state,
)


@pytest.mark.unit
class TestCreateGameStateUnit:
    """Unit tests for create_game_state function."""

    @patch("precog.database.crud_game_states.get_cursor")
    def test_create_game_state_returns_id(self, mock_get_cursor):
        """Test create_game_state returns surrogate id."""
        mock_cursor = MagicMock()
        # Note: RETURNING id returns "id" key (surrogate PK)
        mock_cursor.fetchone.return_value = {"id": 500}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_game_state(
            espn_event_id="401547417",
            home_team_id=1,
            away_team_id=2,
            venue_id=42,
            home_score=0,
            away_score=0,
            league="nfl",
        )

        assert result == 500

    @patch("precog.database.crud_game_states.get_cursor")
    def test_create_game_state_with_situation_jsonb(self, mock_get_cursor):
        """Test create_game_state serializes situation to JSONB."""
        mock_cursor = MagicMock()
        # Note: RETURNING id returns "id" key
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        situation = {"possession": "KC", "down": 2, "distance": 7, "yardLine": 35}

        create_game_state(espn_event_id="401547417", situation=situation)

        # Verify JSON serialization in params
        # create_game_state makes 1 execute call: INSERT RETURNING id
        insert_call = mock_cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        # situation is near the end of params
        assert '{"possession": "KC"' in str(params)

    @patch("precog.database.crud_game_states.get_cursor")
    def test_create_game_state_with_decimal_clock(self, mock_get_cursor):
        """Test create_game_state handles Decimal clock_seconds."""
        mock_cursor = MagicMock()
        # Note: RETURNING id returns "id" key
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_game_state(
            espn_event_id="401547417",
            clock_seconds=Decimal("332"),
            clock_display="5:32",
        )

        # create_game_state makes 1 execute call: INSERT RETURNING id
        insert_call = mock_cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        # Verify Decimal is passed (not float)
        assert any(isinstance(p, Decimal) for p in params)

    @patch("precog.database.crud_game_states.get_cursor")
    def test_create_game_state_passes_game_id(self, mock_get_cursor):
        """Test create_game_state passes game_id FK to INSERT."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_game_state(
            espn_event_id="401547417",
            league="nfl",
            game_id=42,
        )

        insert_call = mock_cursor.execute.call_args_list[0]
        sql = insert_call[0][0]
        params = insert_call[0][1]
        # Verify game_id column is in the SQL
        assert "game_id" in sql
        # Verify game_id=42 is in the params (last before row_current_ind)
        assert 42 in params


@pytest.mark.unit
class TestGetGameStateUnit:
    """Unit tests for game state retrieval functions."""

    @patch("precog.database.crud_game_states.fetch_one")
    def test_get_current_game_state_returns_current(self, mock_fetch_one):
        """Test get_current_game_state returns current version only."""
        mock_fetch_one.return_value = {
            "id": 500,
            "espn_event_id": "401547417",
            "home_score": 14,
            "away_score": 7,
            "row_current_ind": True,
        }

        result = get_current_game_state("401547417")

        assert result is not None
        assert result["row_current_ind"] is True
        assert result["home_score"] == 14

    @patch("precog.database.crud_game_states.fetch_one")
    def test_get_current_game_state_not_found_returns_none(self, mock_fetch_one):
        """Test get_current_game_state returns None when not found."""
        mock_fetch_one.return_value = None

        result = get_current_game_state("nonexistent")

        assert result is None

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_game_state_history_returns_all_versions(self, mock_fetch_all):
        """Test get_game_state_history returns all versions."""
        mock_fetch_all.return_value = [
            {"id": 503, "home_score": 21, "row_current_ind": True},
            {"id": 502, "home_score": 14, "row_current_ind": False},
            {"id": 501, "home_score": 7, "row_current_ind": False},
        ]

        result = get_game_state_history("401547417")

        assert len(result) == 3
        assert result[0]["home_score"] == 21  # Most recent first

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_game_state_history_respects_limit(self, mock_fetch_all):
        """Test get_game_state_history respects limit parameter."""
        mock_fetch_all.return_value = [{"id": 1}]

        get_game_state_history("401547417", limit=5)

        call_args = mock_fetch_all.call_args
        params = call_args[0][1]
        assert params[-1] == 5  # Limit is last param


@pytest.mark.unit
class TestUpsertGameStateUnit:
    """Unit tests for upsert_game_state SCD Type 2 function."""

    @patch("precog.database.crud_game_states.get_cursor")
    def test_upsert_game_state_closes_current_row(self, mock_get_cursor):
        """Test upsert_game_state closes current row before inserting.

        Educational Note:
            Issue #623: upsert_game_state now wraps its mutation body in
            retry_on_scd_unique_conflict. The closure executes:
                1. SELECT NOW() AS ts  → {"ts": datetime}
                2. SELECT id ... FOR UPDATE (lock query) → fetchone not consumed
                3. close_query - SET row_current_ind = FALSE, row_end_ts = %s
                4. insert_query - INSERT new row RETURNING id → {"id": ...}
            The close SQL asserts now use %s placeholder (was NOW()).
        """
        from datetime import datetime as _dt

        mock_cursor = MagicMock()
        # Mock fetchone to return: NOW() ts row, FOR UPDATE lock row (supersede
        # path — current row exists), then INSERT RETURNING id for the new
        # superseding row. Migration 0062 expanded the lock query SELECT list to
        # include game_state_key, and _attempt_close_and_insert reads that value
        # to carry forward verbatim (Pattern 18 / SCD2 rule).
        mock_cursor.fetchone.side_effect = [
            {"ts": _dt(2026, 1, 15, 12, 0, 0, tzinfo=UTC)},  # SELECT NOW() AS ts
            {"id": 99, "game_state_key": "GST-99"},  # FOR UPDATE lock — current row (supersede)
            {"id": 100},  # INSERT RETURNING id — new superseding row
        ]
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = upsert_game_state(
            espn_event_id="401547417",
            home_score=7,
            away_score=3,
            skip_if_unchanged=False,  # Bypass state check to avoid DB call
        )

        # Issue #623: execute call sequence is now
        # [0] SELECT NOW() AS ts
        # [1] SELECT id ... FOR UPDATE (lock query)
        # [2] UPDATE ... SET row_current_ind = FALSE (close query)
        # [3] INSERT ... RETURNING id
        close_sql = mock_cursor.execute.call_args_list[2][0][0]
        assert "row_current_ind = FALSE" in close_sql
        # Issue #623: row_end_ts now uses %s placeholder (was NOW())
        assert "row_end_ts = %s" in close_sql
        assert result == 100


@pytest.mark.unit
class TestGetLiveGamesUnit:
    """Unit tests for get_live_games function."""

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_live_games_filters_in_progress(self, mock_fetch_all):
        """Test get_live_games filters by in_progress status.

        Slot 4 (Migration 0089): get_live_games now reads
        ``g.game_status = 'in_progress'`` via INNER JOIN to the games
        table (the authoritative source post-column-drop).
        """
        mock_fetch_all.return_value = [
            {"espn_event_id": "1"},
            {"espn_event_id": "2"},
        ]

        result = get_live_games()

        assert len(result) == 2
        call_args = mock_fetch_all.call_args
        sql = call_args[0][0]
        assert "g.game_status = 'in_progress'" in sql, (
            f"Expected JOIN-qualified games.game_status filter; got SQL: {sql}"
        )
        assert "INNER JOIN games g" in sql, f"Expected INNER JOIN to games table; got SQL: {sql}"

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_live_games_filters_by_league(self, mock_fetch_all):
        """Test get_live_games filters by league when provided."""
        mock_fetch_all.return_value = []

        get_live_games(league="nfl")

        call_args = mock_fetch_all.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "gs.league = %s" in sql
        assert "nfl" in params


@pytest.mark.unit
class TestGetGamesByDateUnit:
    """Unit tests for get_games_by_date function."""

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_games_by_date_filters_by_date(self, mock_fetch_all):
        """Test get_games_by_date filters by date correctly."""
        mock_fetch_all.return_value = [
            {"espn_event_id": "1", "game_date": datetime(2024, 11, 28, 16, 30)}
        ]

        test_date = datetime(2024, 11, 28)
        result = get_games_by_date(test_date)

        assert len(result) == 1
        call_args = mock_fetch_all.call_args
        sql = call_args[0][0]
        assert "DATE(gs.game_date) = DATE(%s)" in sql

    @patch("precog.database.crud_game_states.fetch_all")
    def test_get_games_by_date_filters_by_league(self, mock_fetch_all):
        """Test get_games_by_date filters by league when provided."""
        mock_fetch_all.return_value = []

        get_games_by_date(datetime(2024, 11, 28), league="nba")

        call_args = mock_fetch_all.call_args
        params = call_args[0][1]
        assert "nba" in params


@pytest.mark.unit
class TestGetOrCreateGameUnit:
    """Unit tests for get_or_create_game — games dimension upsert."""

    @patch("precog.database.crud_game_states.get_cursor")
    def test_get_or_create_game_returns_id(self, mock_get_cursor):
        """Test get_or_create_game returns the game id."""
        mock_cursor = MagicMock()
        # Migration 0062: RETURNING clause now includes game_key. Returning a
        # canonical (non-TEMP) key simulates the ON CONFLICT branch and avoids
        # the follow-up UPDATE firing (keeping execute.call_count at 1).
        mock_cursor.fetchone.return_value = {"id": 42, "game_key": "GAM-42"}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = get_or_create_game(
            sport="football",
            game_date=date(2024, 9, 8),
            home_team_code="KC",
            away_team_code="BAL",
            season=2024,
            league="nfl",
        )

        assert result == 42
        assert mock_cursor.execute.call_count == 1

    @patch("precog.database.crud_game_states.get_cursor")
    def test_get_or_create_game_derives_season_from_date(self, mock_get_cursor):
        """Test season is derived from game_date.year when not provided."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1, "game_key": "GAM-1"}  # 0062
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        get_or_create_game(
            sport="football",
            game_date=date(2025, 11, 15),
            home_team_code="KC",
            away_team_code="BAL",
            league="nfl",
        )

        # Check season param (5th positional param after sport, date, home, away)
        insert_call = mock_cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        assert params[4] == 2025  # season derived from game_date.year

    def test_get_or_create_game_raises_if_league_missing(self):
        """Test ValueError raised when league is not provided (latent trap after Phase B)."""
        with pytest.raises(ValueError, match="league is required"):
            get_or_create_game(
                sport="football",
                game_date=date(2024, 10, 5),
                home_team_code="OSU",
                away_team_code="MICH",
            )

    @patch("precog.database.crud_game_states.get_cursor")
    def test_get_or_create_game_on_conflict_sql_has_case_guard(self, mock_get_cursor):
        """Test the ON CONFLICT clause has CASE guard for game_status.

        The CASE expression prevents regressing game_status from 'final'/'final_ot'
        back to an earlier status when the same game is upserted again.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1, "game_key": "GAM-1"}  # 0062
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        get_or_create_game(
            sport="football",
            game_date=date(2024, 9, 8),
            home_team_code="KC",
            away_team_code="BAL",
            league="nfl",
            game_status="scheduled",
        )

        # Verify the SQL contains the CASE guard
        insert_call = mock_cursor.execute.call_args_list[0]
        sql = insert_call[0][0]
        assert "CASE" in sql
        assert "final" in sql
        assert "final_ot" in sql

    @patch("precog.database.crud_game_states.get_cursor")
    def test_get_or_create_game_passes_all_fields(self, mock_get_cursor):
        """Test all optional fields are passed through to SQL."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 99, "game_key": "GAM-99"}  # 0062
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = get_or_create_game(
            sport="football",
            game_date=date(2024, 9, 8),
            home_team_code="KC",
            away_team_code="BAL",
            season=2024,
            league="nfl",
            season_type="regular",
            week_number=1,
            home_team_id=10,
            away_team_id=20,
            venue_id=5,
            venue_name="Arrowhead Stadium",
            neutral_site=False,
            espn_event_id="401547417",
            game_status="pre",
            data_source="espn_poller",
        )

        assert result == 99
        insert_call = mock_cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        # Verify key fields are in params
        assert "nfl" in params  # sport
        assert "KC" in params  # home_team_code
        assert "BAL" in params  # away_team_code
        assert "401547417" in params  # espn_event_id
        assert "espn_poller" in params  # data_source


@pytest.mark.unit
class TestUpdateGameResultUnit:
    """Unit tests for update_game_result — final score + derived fields."""

    @patch("precog.database.crud_game_states.get_cursor")
    def test_update_game_result_home_win(self, mock_get_cursor):
        """Test home win sets correct margin and result."""
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        update_game_result(game_id=42, home_score=27, away_score=20)

        call_args = mock_cursor.execute.call_args_list[0]
        params = call_args[0][1]
        assert params[0] == 27  # home_score
        assert params[1] == 20  # away_score
        assert params[2] == 7  # actual_margin (27-20)
        assert params[3] == "home_win"  # result
        assert params[4] == 42  # game_id

    @patch("precog.database.crud_game_states.get_cursor")
    def test_update_game_result_away_win(self, mock_get_cursor):
        """Test away win sets correct margin and result."""
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        update_game_result(game_id=99, home_score=10, away_score=24)

        call_args = mock_cursor.execute.call_args_list[0]
        params = call_args[0][1]
        assert params[2] == -14  # actual_margin (10-24)
        assert params[3] == "away_win"

    @patch("precog.database.crud_game_states.get_cursor")
    def test_update_game_result_draw(self, mock_get_cursor):
        """Test draw sets correct margin and result."""
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        update_game_result(game_id=1, home_score=17, away_score=17)

        call_args = mock_cursor.execute.call_args_list[0]
        params = call_args[0][1]
        assert params[2] == 0  # actual_margin
        assert params[3] == "draw"


@pytest.mark.unit
class TestGameStateChangedUnit:
    """Unit tests for game_state_changed function.

    Educational Note:
        The game_state_changed function determines if a game state has
        meaningfully changed to avoid creating duplicate SCD Type 2 rows.

        We intentionally DO NOT compare clock_seconds or clock_display because:
        - Clock changes every few seconds during play
        - This would create ~1000+ rows per game instead of ~50-100

        We DO compare:
        - home_score, away_score: Core game state
        - period: Quarter/half transitions
        - situation: Possession, down/distance changes (significant for NFL)

        Slot 4 (Migration 0089): game_status was DROPPED from game_states;
        change-detection no longer compares it.  In practice status
        transitions (pre -> in_progress -> halftime -> final) coincide
        with period transitions, so SCD2 row creation density is
        materially unchanged.

    Reference:
        - Issue #234: ESPNGamePoller for Live Game State Collection
        - REQ-DATA-001: Game State Data Collection (SCD Type 2)
        - Slot 4 build spec § 0d-bis B-1
    """

    def test_game_state_changed_no_current_state_returns_true(self):
        """Test that new game (no current state) always returns True."""

        result = game_state_changed(
            current=None,
            home_score=0,
            away_score=0,
            period=1,
        )

        assert result is True

    def test_game_state_changed_same_state_returns_false(self):
        """Test that identical state returns False (no change)."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
        }

        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
        )

        assert result is False

    def test_game_state_changed_score_change_returns_true(self):
        """Test that score change is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
        }

        # Home team scores
        result = game_state_changed(
            current=current,
            home_score=21,  # Changed from 14 to 21
            away_score=7,
            period=2,
        )

        assert result is True

    def test_game_state_changed_away_score_change_returns_true(self):
        """Test that away score change is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
        }

        # Away team scores
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=14,  # Changed from 7 to 14
            period=2,
        )

        assert result is True

    def test_game_state_changed_period_change_returns_true(self):
        """Test that period change is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
        }

        # Quarter change
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=3,  # Changed from 2 to 3
        )

        assert result is True

    def test_game_state_changed_period_zero_to_one_transition_returns_true(self):
        """Test that pre->in_progress transition is detected via period delta.

        Slot 4 (Migration 0089): game_status was DROPPED, but in practice
        the pre->in_progress transition coincides with period 0->1 (first
        snap of the game), so SCD2 row creation density is materially
        unchanged.  This test exercises that period-driven detection path
        (which previously was redundant with the game_status comparison
        but is now load-bearing).
        """

        current = {
            "home_score": 0,
            "away_score": 0,
            "period": 0,
        }

        # First snap: period 0 -> 1
        result = game_state_changed(
            current=current,
            home_score=0,
            away_score=0,
            period=1,  # Changed from 0 to 1
        )

        assert result is True

    def test_game_state_changed_situation_possession_change_returns_true(self):
        """Test that possession change in situation is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"possession": "KC", "down": 1, "distance": 10},
        }

        # Possession change (turnover)
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"possession": "DEN", "down": 1, "distance": 10},  # Changed
        )

        assert result is True

    def test_game_state_changed_situation_down_change_returns_true(self):
        """Test that down change in situation is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"possession": "KC", "down": 1, "distance": 10},
        }

        # Down change
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"possession": "KC", "down": 2, "distance": 7},  # Changed
        )

        assert result is True

    def test_game_state_changed_situation_red_zone_change_returns_true(self):
        """Test that red zone change in situation is detected."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"is_red_zone": False},
        }

        # Entered red zone
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"is_red_zone": True},  # Changed
        )

        assert result is True

    def test_game_state_changed_no_situation_change_returns_false(self):
        """Test that same situation returns False."""

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"possession": "KC", "down": 2, "distance": 7},
        }

        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"possession": "KC", "down": 2, "distance": 7},  # Same
        )

        assert result is False

    def test_game_state_changed_new_situation_no_current_returns_true(self):
        """Test that new situation when current has none returns True."""

        current = {
            "home_score": 0,
            "away_score": 0,
            "period": 1,
            "situation": None,
        }

        result = game_state_changed(
            current=current,
            home_score=0,
            away_score=0,
            period=1,
            situation={"possession": "KC", "down": 1, "distance": 10},
        )

        assert result is True

    def test_game_state_changed_ignores_clock_seconds(self):
        """Test that clock_seconds changes are intentionally ignored.

        Educational Note:
            We explicitly DO NOT track clock_seconds changes because:
            - Clock changes every few seconds during live play
            - Tracking clock would create ~1000+ rows per game
            - Score, period, and situation capture meaningful state

            This test verifies the design decision from Issue #234.
        """

        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "clock_seconds": 845,  # This should be ignored
        }

        # Only clock changed - should return False (no meaningful change)
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            # clock_seconds is not a parameter of game_state_changed
        )

        assert result is False


@pytest.mark.unit
class TestGameStateChangedSportAwareUnit:
    """Unit tests for sport-aware situation filtering in game_state_changed.

    Educational Note:
        Different sports have different high-frequency situation fields that
        should NOT trigger SCD row creation. For example:
        - Basketball: foul counts change every few minutes
        - Hockey: shot counts change constantly
        Only sport-relevant keys (defined in TRACKED_SITUATION_KEYS) trigger
        new rows. Unknown leagues fall back to comparing ALL keys (safe default).

    Reference:
        - Issue #397: Game states SCD noise tuning
        - ESPNSituationData in api_connectors/espn_client.py
    """

    # --- Constants validation ---

    def test_tracked_situation_keys_has_all_sport_categories(self):
        """Verify TRACKED_SITUATION_KEYS covers football, basketball, hockey."""
        assert "football" in TRACKED_SITUATION_KEYS
        assert "basketball" in TRACKED_SITUATION_KEYS
        assert "hockey" in TRACKED_SITUATION_KEYS

    def test_league_sport_category_maps_all_leagues(self):
        """Verify LEAGUE_SPORT_CATEGORY maps all expected leagues."""
        expected = {"nfl", "ncaaf", "nba", "ncaab", "wnba", "ncaaw", "nhl", "mlb", "mls"}
        assert set(LEAGUE_SPORT_CATEGORY.keys()) == expected

    # --- Football (existing behavior preserved with league param) ---

    def test_football_down_change_triggers_with_league(self):
        """Football down change triggers new row when league='nfl'."""
        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"possession": "KC", "down": 1, "distance": 10},
        }
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"possession": "KC", "down": 2, "distance": 7},
            league="nfl",
        )
        assert result is True

    def test_football_timeout_change_does_not_trigger(self):
        """Football timeout change does NOT trigger new row (not tracked)."""
        current = {
            "home_score": 14,
            "away_score": 7,
            "period": 2,
            "situation": {"possession": "KC", "down": 1, "distance": 10, "home_timeouts": 3},
        }
        result = game_state_changed(
            current=current,
            home_score=14,
            away_score=7,
            period=2,
            situation={"possession": "KC", "down": 1, "distance": 10, "home_timeouts": 2},
            league="ncaaf",
        )
        assert result is False

    # --- Basketball ---

    def test_basketball_foul_change_triggers(self):
        """Basketball foul count change triggers new row (useful for model training)."""
        current = {
            "home_score": 55,
            "away_score": 52,
            "period": 3,
            "situation": {"possession": "home", "home_fouls": 3, "away_fouls": 2, "bonus": None},
        }
        result = game_state_changed(
            current=current,
            home_score=55,
            away_score=52,
            period=3,
            situation={"possession": "home", "home_fouls": 4, "away_fouls": 2, "bonus": None},
            league="nba",
        )
        assert result is True

    def test_basketball_bonus_change_triggers(self):
        """Basketball bonus status change DOES trigger new row."""
        current = {
            "home_score": 55,
            "away_score": 52,
            "period": 3,
            "situation": {"possession": "home", "bonus": None, "home_fouls": 4},
        }
        result = game_state_changed(
            current=current,
            home_score=55,
            away_score=52,
            period=3,
            situation={"possession": "home", "bonus": "home", "home_fouls": 5},
            league="nba",
        )
        assert result is True

    def test_basketball_possession_arrow_change_triggers(self):
        """Basketball possession arrow change DOES trigger new row."""
        current = {
            "home_score": 40,
            "away_score": 38,
            "period": 2,
            "situation": {"possession": "home", "possession_arrow": "home"},
        }
        result = game_state_changed(
            current=current,
            home_score=40,
            away_score=38,
            period=2,
            situation={"possession": "home", "possession_arrow": "away"},
            league="ncaab",
        )
        assert result is True

    def test_basketball_possession_change_triggers(self):
        """Basketball possession change DOES trigger new row."""
        current = {
            "home_score": 55,
            "away_score": 52,
            "period": 3,
            "situation": {"possession": "home", "home_fouls": 3},
        }
        result = game_state_changed(
            current=current,
            home_score=55,
            away_score=52,
            period=3,
            situation={"possession": "away", "home_fouls": 3},
            league="nba",
        )
        assert result is True

    def test_basketball_wnba_uses_basketball_keys(self):
        """WNBA uses basketball sport category keys."""
        current = {
            "home_score": 30,
            "away_score": 28,
            "period": 2,
            "situation": {"possession": "home", "home_timeouts": 3},
        }
        # Only timeouts changed - should NOT trigger (timeouts are noise)
        result = game_state_changed(
            current=current,
            home_score=30,
            away_score=28,
            period=2,
            situation={"possession": "home", "home_timeouts": 2},
            league="wnba",
        )
        assert result is False

    # --- Hockey ---

    def test_hockey_shot_change_does_not_trigger(self):
        """Hockey shot count change does NOT trigger new row."""
        current = {
            "home_score": 2,
            "away_score": 1,
            "period": 2,
            "situation": {
                "home_shots": 15,
                "away_shots": 12,
                "home_powerplay": False,
                "away_powerplay": False,
            },
        }
        result = game_state_changed(
            current=current,
            home_score=2,
            away_score=1,
            period=2,
            situation={
                "home_shots": 18,
                "away_shots": 14,
                "home_powerplay": False,
                "away_powerplay": False,
            },
            league="nhl",
        )
        assert result is False

    def test_hockey_powerplay_change_triggers(self):
        """Hockey power play change DOES trigger new row."""
        current = {
            "home_score": 2,
            "away_score": 1,
            "period": 2,
            "situation": {"home_powerplay": False, "away_powerplay": False, "home_shots": 15},
        }
        result = game_state_changed(
            current=current,
            home_score=2,
            away_score=1,
            period=2,
            situation={"home_powerplay": True, "away_powerplay": False, "home_shots": 16},
            league="nhl",
        )
        assert result is True

    def test_hockey_away_powerplay_change_triggers(self):
        """Hockey away power play change DOES trigger new row."""
        current = {
            "home_score": 2,
            "away_score": 1,
            "period": 2,
            "situation": {"home_powerplay": False, "away_powerplay": False},
        }
        result = game_state_changed(
            current=current,
            home_score=2,
            away_score=1,
            period=2,
            situation={"home_powerplay": False, "away_powerplay": True},
            league="nhl",
        )
        assert result is True

    # --- Fallback behavior ---

    def test_unknown_league_falls_back_to_full_comparison(self):
        """Unknown league compares ALL situation keys (safe fallback)."""
        current = {
            "home_score": 10,
            "away_score": 8,
            "period": 1,
            "situation": {"some_field": "a", "other_field": "b"},
        }
        # Changing any field should trigger with unknown league
        result = game_state_changed(
            current=current,
            home_score=10,
            away_score=8,
            period=1,
            situation={"some_field": "a", "other_field": "c"},
            league="cricket",
        )
        assert result is True

    def test_unknown_league_same_situation_returns_false(self):
        """Unknown league with same situation returns False."""
        current = {
            "home_score": 10,
            "away_score": 8,
            "period": 1,
            "situation": {"some_field": "a"},
        }
        result = game_state_changed(
            current=current,
            home_score=10,
            away_score=8,
            period=1,
            situation={"some_field": "a"},
            league="cricket",
        )
        assert result is False

    def test_none_league_falls_back_to_full_comparison(self):
        """league=None compares ALL situation keys (safe fallback)."""
        current = {
            "home_score": 10,
            "away_score": 8,
            "period": 1,
            "situation": {"arbitrary_key": "value1"},
        }
        result = game_state_changed(
            current=current,
            home_score=10,
            away_score=8,
            period=1,
            situation={"arbitrary_key": "value2"},
            league=None,
        )
        assert result is True

    def test_none_league_detects_new_keys_in_situation(self):
        """league=None detects keys present in new but not current situation."""
        current = {
            "home_score": 10,
            "away_score": 8,
            "period": 1,
            "situation": {},
        }
        result = game_state_changed(
            current=current,
            home_score=10,
            away_score=8,
            period=1,
            situation={"new_key": "value"},
            league=None,
        )
        assert result is True

    def test_league_case_insensitive(self):
        """League lookup is case-insensitive."""
        current = {
            "home_score": 55,
            "away_score": 52,
            "period": 3,
            "situation": {"possession": "home", "home_timeouts": 4},
        }
        # Only timeouts changed with uppercase league - should NOT trigger
        result = game_state_changed(
            current=current,
            home_score=55,
            away_score=52,
            period=3,
            situation={"possession": "home", "home_timeouts": 3},
            league="NBA",
        )
        assert result is False


@pytest.mark.unit
class TestFindGameByMatchupUnit:
    """Unit tests for find_game_by_matchup — league-to-sport mapping + game lookup."""

    @patch("precog.database.crud_game_states.fetch_one")
    def test_nfl_maps_to_football(self, mock_fetch_one):
        """NFL league maps to 'football' sport in query."""
        mock_fetch_one.return_value = {"id": 42}

        result = find_game_by_matchup(
            league="nfl",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result == 42
        # Verify the sport param passed to query is "football"
        call_args = mock_fetch_one.call_args[0]
        params = call_args[1]
        assert params[0] == "football"

    @patch("precog.database.crud_game_states.fetch_one")
    def test_nba_maps_to_basketball(self, mock_fetch_one):
        """NBA league maps to 'basketball' sport in query."""
        mock_fetch_one.return_value = {"id": 99}

        result = find_game_by_matchup(
            league="nba",
            game_date=date(2026, 3, 25),
            home_team_code="BOS",
            away_team_code="OKC",
        )

        assert result == 99
        call_args = mock_fetch_one.call_args[0]
        params = call_args[1]
        assert params[0] == "basketball"

    @patch("precog.database.crud_game_states.fetch_one")
    def test_ncaaf_maps_to_football(self, mock_fetch_one):
        """NCAAF league maps to 'football' sport."""
        mock_fetch_one.return_value = {"id": 7}

        result = find_game_by_matchup(
            league="ncaaf",
            game_date=date(2026, 1, 2),
            home_team_code="MSST",
            away_team_code="WAKE",
        )

        assert result == 7
        call_args = mock_fetch_one.call_args[0]
        params = call_args[1]
        assert params[0] == "football"

    @patch("precog.database.crud_game_states.fetch_one")
    def test_nhl_maps_to_hockey(self, mock_fetch_one):
        """NHL league maps to 'hockey' sport."""
        mock_fetch_one.return_value = {"id": 55}

        result = find_game_by_matchup(
            league="nhl",
            game_date=date(2026, 1, 15),
            home_team_code="TOR",
            away_team_code="BOS",
        )

        assert result == 55
        call_args = mock_fetch_one.call_args[0]
        params = call_args[1]
        assert params[0] == "hockey"

    @patch("precog.database.crud_game_states.fetch_all")
    @patch("precog.database.crud_game_states.fetch_one")
    def test_returns_none_when_no_match(self, mock_fetch_one, mock_fetch_all):
        """Returns None when no exact or fuzzy match found."""
        mock_fetch_one.return_value = None
        mock_fetch_all.return_value = []

        result = find_game_by_matchup(
            league="nfl",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result is None
        mock_fetch_one.assert_called_once()
        mock_fetch_all.assert_called_once()

    @patch("precog.database.crud_game_states.fetch_all")
    @patch("precog.database.crud_game_states.fetch_one")
    def test_fuzzy_match_finds_game_on_adjacent_day(self, mock_fetch_one, mock_fetch_all):
        """When exact date misses, +/-1 day window finds the game."""
        mock_fetch_one.return_value = None  # Exact date: no match
        mock_fetch_all.return_value = [{"id": 77, "game_date": date(2026, 1, 19)}]

        result = find_game_by_matchup(
            league="nfl",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result == 77
        # Verify fuzzy query uses correct date window
        fuzzy_params = mock_fetch_all.call_args[0][1]
        assert fuzzy_params[1] == date(2026, 1, 17)  # day before
        assert fuzzy_params[2] == date(2026, 1, 19)  # day after

    @patch("precog.database.crud_game_states.fetch_all")
    @patch("precog.database.crud_game_states.fetch_one")
    def test_fuzzy_match_returns_none_on_ambiguity(self, mock_fetch_one, mock_fetch_all):
        """When +/-1 day returns multiple games, returns None (ambiguous)."""
        mock_fetch_one.return_value = None
        mock_fetch_all.return_value = [
            {"id": 77, "game_date": date(2026, 1, 17)},
            {"id": 78, "game_date": date(2026, 1, 19)},
        ]

        result = find_game_by_matchup(
            league="nfl",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result is None

    @patch("precog.database.crud_game_states.fetch_one")
    def test_exact_match_skips_fuzzy(self, mock_fetch_one):
        """When exact date matches, fuzzy query is never called."""
        mock_fetch_one.return_value = {"id": 42}

        result = find_game_by_matchup(
            league="nfl",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result == 42
        # fetch_all should NOT have been called (exact match found)
        mock_fetch_one.assert_called_once()

    def test_returns_none_for_unknown_league(self):
        """Returns None for league not in LEAGUE_SPORT_CATEGORY (no DB call)."""
        result = find_game_by_matchup(
            league="xyz_unknown",
            game_date=date(2026, 1, 18),
            home_team_code="NE",
            away_team_code="HOU",
        )

        assert result is None

    @patch("precog.database.crud_game_states.fetch_one")
    def test_passes_all_params_to_query(self, mock_fetch_one):
        """Verify game_date, home_team_code, away_team_code all passed."""
        mock_fetch_one.return_value = {"id": 1}
        target_date = date(2026, 2, 14)

        find_game_by_matchup(
            league="nba",
            game_date=target_date,
            home_team_code="LAL",
            away_team_code="GSW",
        )

        call_args = mock_fetch_one.call_args[0]
        params = call_args[1]
        assert params == ("basketball", target_date, "LAL", "GSW")


# =============================================================================
# Slot 4 (Migration 0089) -- derive_game_status SSOT helper unit tests
# =============================================================================


@pytest.mark.unit
class TestDeriveGameStatusUnit:
    """Unit tests for derive_game_status (Slot 4 SSOT helper).

    The helper reconstructs sport-tier game_status for a specific
    game_states row from period + clock_seconds + situation + parent
    games.game_status (when known).  Pattern 73 SSOT: this is the
    canonical entry point for status derivation post-Migration-0089.

    Disambiguation rules (priority order):
        1. parent_game_status terminal -> propagate
        2. situation['period_complete'] = True -> 'end_of_period'
        3. period == 2 + clock_seconds == 0 -> 'halftime'
        4. period >= 1 + clock_seconds not None -> 'in_progress'
        5. default -> 'pre'
    """

    def test_u1_derive_game_status_terminal_from_parent_final(self):
        """Rule 1: parent='final' -> 'final' regardless of period/clock."""
        result = derive_game_status({"period": 4, "clock_seconds": 120}, parent_game_status="final")
        assert result == "final"

    def test_u2_derive_game_status_terminal_from_parent_final_ot(self):
        """Rule 1: parent='final_ot' -> 'final_ot'."""
        result = derive_game_status(
            {"period": 5, "clock_seconds": 0}, parent_game_status="final_ot"
        )
        assert result == "final_ot"

    def test_u3_derive_game_status_cancelled_from_parent(self):
        """Rule 1: parent='cancelled' -> 'cancelled'."""
        result = derive_game_status(
            {"period": 0, "clock_seconds": None}, parent_game_status="cancelled"
        )
        assert result == "cancelled"

    def test_u4_derive_game_status_postponed_from_parent(self):
        """Rule 1: parent='postponed' -> 'postponed'."""
        result = derive_game_status(
            {"period": 0, "clock_seconds": None}, parent_game_status="postponed"
        )
        assert result == "postponed"

    def test_u5_derive_game_status_delayed_from_parent(self):
        """Rule 1: parent='delayed' -> 'delayed'."""
        result = derive_game_status(
            {"period": 1, "clock_seconds": 600}, parent_game_status="delayed"
        )
        assert result == "delayed"

    def test_u6_derive_game_status_end_of_period_from_situation(self):
        """Rule 2: situation['period_complete']=True -> 'end_of_period'.

        Higher priority than rule 3 (halftime) for ESPN explicit signal.
        """
        result = derive_game_status(
            {"period": 1, "clock_seconds": 0, "situation": {"period_complete": True}},
        )
        assert result == "end_of_period"

    def test_u7_derive_game_status_halftime_from_period_2_clock_zero(self):
        """Rule 3: period=2, clock_seconds=0, no period_complete signal -> 'halftime'."""
        result = derive_game_status({"period": 2, "clock_seconds": 0})
        assert result == "halftime"

    def test_u8_derive_game_status_in_progress_from_period_clock(self):
        """Rule 4: period=1, clock_seconds=600 -> 'in_progress'."""
        result = derive_game_status({"period": 1, "clock_seconds": 600})
        assert result == "in_progress"

    def test_u9_derive_game_status_pre_default(self):
        """Rule 5: period=0, clock_seconds=None, parent=None -> 'pre'."""
        result = derive_game_status({"period": 0, "clock_seconds": None})
        assert result == "pre"

    def test_u10_derive_game_status_returns_only_valid_enum_value(self):
        """Across all paths, returned value is in the 10-value sport-tier vocabulary."""
        valid = {
            "pre",
            "in_progress",
            "halftime",
            "end_of_period",
            "final",
            "final_ot",
            "delayed",
            "postponed",
            "cancelled",
            "suspended",
        }
        # Exercise all rule paths
        cases = [
            ({"period": 4}, "final"),
            ({"period": 5, "clock_seconds": 0}, "final_ot"),
            ({"period": 0, "clock_seconds": None}, "cancelled"),
            ({"period": 0}, "postponed"),
            ({"period": 1, "clock_seconds": 600}, "delayed"),
            ({"period": 1, "clock_seconds": 0, "situation": {"period_complete": True}}, None),
            ({"period": 2, "clock_seconds": 0}, None),
            ({"period": 1, "clock_seconds": 600}, None),
            ({"period": 0, "clock_seconds": None}, None),
            ({"period": 0}, None),
        ]
        for row, parent in cases:
            result = derive_game_status(row, parent_game_status=parent)
            assert result in valid, (
                f"derive_game_status({row}, parent={parent}) returned {result!r}, "
                f"not in valid sport-tier vocabulary"
            )

    def test_derive_game_status_decimal_clock_seconds(self):
        """clock_seconds as Decimal (the production type) is handled correctly."""
        result = derive_game_status({"period": 2, "clock_seconds": Decimal("0")})
        assert result == "halftime"

    def test_derive_game_status_terminal_overrides_period_clock_signals(self):
        """Rule 1 wins over rules 2-4 even when row signals suggest otherwise."""
        # Row looks like in_progress, but parent says cancelled -> rule 1 wins.
        result = derive_game_status(
            {"period": 3, "clock_seconds": 300, "situation": {"period_complete": False}},
            parent_game_status="cancelled",
        )
        assert result == "cancelled"

    def test_derive_game_status_period_3_clock_zero_is_in_progress(self):
        """Rule 3 narrowing (#1174 P1 #1): period=3, clock=0, no period_complete -> 'in_progress'.

        Pre-fix `period >= 2` over-detected halftime on period 3 at clock=0
        when ESPN did not emit period_complete.  Post-fix `period == 2`
        narrows rule 3 to strictly end-of-period-2; period 3+ falls through
        to rule 4 ('in_progress') since clock_seconds is not None.
        """
        result = derive_game_status({"period": 3, "clock_seconds": 0})
        assert result == "in_progress"

    def test_derive_game_status_period_4_clock_zero_is_in_progress(self):
        """Rule 3 narrowing (#1174 P1 #1): period=4, clock=0, no period_complete -> 'in_progress'.

        Same over-detection bug as period=3; period 4 at clock=0 without
        an explicit ESPN period_complete signal is a live tick (rule 4),
        not halftime.
        """
        result = derive_game_status({"period": 4, "clock_seconds": 0})
        assert result == "in_progress"

    def test_derive_game_status_ot_period_clock_zero_is_in_progress(self):
        """Rule 3 narrowing (#1174 P1 #1): OT period (5), clock=0, no period_complete -> 'in_progress'.

        OT periods at clock=0 must not be mis-classified as halftime;
        they fall through to rule 4 absent an explicit period_complete
        signal or a terminal parent status.
        """
        result = derive_game_status({"period": 5, "clock_seconds": 0})
        assert result == "in_progress"

    def test_derive_game_status_suspended_from_parent(self):
        """Rule 1 (#1174 P1 #2): parent='suspended' -> 'suspended'.

        Option A per session 107 decision: 'suspended' joins the terminal
        parent-status set alongside delayed/postponed.  At a given tick,
        a 'suspended' parent overrides row-level signals (consistent with
        delayed/postponed treatment of "may-resume" statuses).
        """
        result = derive_game_status(
            {"period": 2, "clock_seconds": 300}, parent_game_status="suspended"
        )
        assert result == "suspended"
