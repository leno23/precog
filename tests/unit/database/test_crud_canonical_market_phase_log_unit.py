"""Unit tests for crud_canonical_market_phase_log module -- Cleanup epic Slot 4 (Migration 0088).

Mirror of ``test_crud_canonical_event_phase_log_unit.py`` (slot 0079
sister-module reference per build spec § 0d D-3).

Covers (function-by-function):
    - append_market_phase_transition: happy path, new_phase validation,
      previous_phase nullable + validation, changed_by prefix validation,
      changed_by length boundary (slot-0073/0079 #1085 finding #3
      inheritance).
    - get_phase_history_for_market: query shape + parameters.

Pattern 73 SSOT real-guard discipline (slot 0073/0079 inheritance):
    Both ``CANONICAL_MARKET_LIFECYCLE_PHASES`` and ``DECIDED_BY_PREFIXES``
    are imported and USED in real-guard ValueError-raising validation in
    the SUT.  These tests assert that the validation fires.

Pattern 43 (mock fidelity) discipline: mocks return the EXACT shape that
the real query returns.  Mocks of ``get_cursor`` use the
``__enter__`` / ``__exit__`` protocol consistent with sibling unit tests.

Reference:
    - ``src/precog/database/crud_canonical_market_phase_log.py``
    - ``src/precog/database/alembic/versions/0088_canonical_lifecycle_phase_redistribution.py``
    - ``tests/unit/database/test_crud_canonical_event_phase_log_unit.py`` (mirror reference)
    - Slot 4 build spec § 0d D-3 + § 6 (K1-K8 binding test list)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from precog.database.constants import (
    CANONICAL_EVENT_LIFECYCLE_PHASES,
    CANONICAL_MARKET_LIFECYCLE_PHASES,
    DECIDED_BY_PREFIXES,
)
from precog.database.crud_canonical_market_phase_log import (
    append_market_phase_transition,
    get_phase_history_for_market,
)
from tests.unit.database._crud_unit_helpers import wire_get_cursor_mock

pytestmark = [pytest.mark.unit]


def _full_market_phase_log_row_dict(
    *,
    id: int = 7,
    canonical_market_id: int = 42,
    previous_phase: str | None = "open",
    new_phase: str = "settling",
    transition_at: datetime | None = None,
    changed_by: str = "system:trigger",
    note: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Build a full canonical_market_phase_log row dict matching real query shape."""
    if transition_at is None:
        transition_at = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    if created_at is None:
        created_at = transition_at
    return {
        "id": id,
        "canonical_market_id": canonical_market_id,
        "previous_phase": previous_phase,
        "new_phase": new_phase,
        "transition_at": transition_at,
        "changed_by": changed_by,
        "note": note,
        "created_at": created_at,
    }


_ALL_COLUMNS = (
    "id",
    "canonical_market_id",
    "previous_phase",
    "new_phase",
    "transition_at",
    "changed_by",
    "note",
    "created_at",
)


# =============================================================================
# K4: append_market_phase_transition -- happy path (mirror slot 0079)
# =============================================================================


class TestAppendMarketPhaseTransitionValidInputs:
    """Happy-path: valid inputs produce a single INSERT with returned id."""

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_k4_append_market_phase_transition_writes_row(self, mock_get_cursor_factory):
        """K4 (build spec § 6): operator-supplied changed_by writes audit row."""
        mock_cursor = wire_get_cursor_mock(mock_get_cursor_factory, returning_id=42)

        result = append_market_phase_transition(
            canonical_market_id=10,
            new_phase="settling",
            changed_by="human:operator",
            previous_phase="open",
            note="Manual correction: market locked at announced settlement time",
        )

        assert result == 42
        mock_get_cursor_factory.assert_called_once_with(commit=True)
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO canonical_market_phase_log" in sql
        assert "RETURNING id" in sql
        # All 5 column slots in the INSERT.
        for col in (
            "canonical_market_id",
            "previous_phase",
            "new_phase",
            "changed_by",
            "note",
        ):
            assert col in sql, f"INSERT must include column {col!r}"
        # Param order matches the INSERT column order.
        assert params == (
            10,
            "open",
            "settling",
            "human:operator",
            "Manual correction: market locked at announced settlement time",
        )

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_append_market_phase_transition_minimal_inputs(self, mock_get_cursor_factory):
        """append_market_phase_transition with only required args."""
        wire_get_cursor_mock(mock_get_cursor_factory, returning_id=99)

        result = append_market_phase_transition(
            canonical_market_id=5,
            new_phase="open",
            changed_by="service:matching-v1",
        )

        assert result == 99


# =============================================================================
# K5: append_market_phase_transition -- new_phase validation (Pattern 73 SSOT)
# =============================================================================


class TestAppendMarketPhaseTransitionNewPhaseValidation:
    """K5: Pattern 73 SSOT -- new_phase MUST be in CANONICAL_MARKET_LIFECYCLE_PHASES."""

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_k5_invalid_new_phase_raises_value_error(self, mock_get_cursor_factory):
        """new_phase='not_a_real_phase' raises ValueError before SQL."""
        with pytest.raises(ValueError, match="new_phase"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="not_a_real_phase",
                changed_by="human:eric",
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_k5_event_vocab_value_rejected_in_market_validator(self, mock_get_cursor_factory):
        """Event-vocab value (e.g., 'pre_event') REJECTED for market validator.

        Pattern 91 V1.45+ table-target verification: even though 'pre_event'
        is in CANONICAL_EVENT_LIFECYCLE_PHASES, it is NOT in
        CANONICAL_MARKET_LIFECYCLE_PHASES -- the two vocabularies are
        disjoint by design.
        """
        with pytest.raises(ValueError, match="new_phase"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="pre_event",  # event vocab, NOT market vocab
                changed_by="human:eric",
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_each_canonical_market_phase_value_accepted(self, mock_get_cursor_factory):
        """Every value in CANONICAL_MARKET_LIFECYCLE_PHASES is accepted by validation."""
        wire_get_cursor_mock(mock_get_cursor_factory, returning_id=1)

        for phase in CANONICAL_MARKET_LIFECYCLE_PHASES:
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase=phase,
                changed_by="system:test",
            )


# =============================================================================
# K6: changed_by validation
# =============================================================================


class TestAppendMarketPhaseTransitionChangedByValidation:
    """K6: changed_by MUST start with one of DECIDED_BY_PREFIXES + length <= 64."""

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_k6_changed_by_no_prefix_raises_value_error(self, mock_get_cursor_factory):
        """changed_by='nopfx' raises ValueError BEFORE INSERT."""
        with pytest.raises(ValueError, match="changed_by"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="open",
                changed_by="nopfx",
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_k6_changed_by_wrong_prefix_raises_value_error(self, mock_get_cursor_factory):
        """changed_by='admin:eric' raises ValueError."""
        with pytest.raises(ValueError, match="changed_by"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="open",
                changed_by="admin:eric",
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_changed_by_too_long_raises_value_error(self, mock_get_cursor_factory):
        """changed_by length > 64 raises ValueError."""
        too_long = "human:" + "x" * 60  # 6 + 60 = 66, > 64
        with pytest.raises(ValueError, match="changed_by length"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="open",
                changed_by=too_long,
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_each_decided_by_prefix_accepted(self, mock_get_cursor_factory):
        """Every prefix in DECIDED_BY_PREFIXES is acceptable."""
        wire_get_cursor_mock(mock_get_cursor_factory, returning_id=1)

        for prefix in DECIDED_BY_PREFIXES:
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="open",
                changed_by=f"{prefix}example_actor",
            )


# =============================================================================
# previous_phase validation (mirror slot 0079)
# =============================================================================


class TestAppendMarketPhaseTransitionPreviousPhaseValidation:
    """previous_phase nullable + validation when non-NULL."""

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_invalid_previous_phase_raises_value_error(self, mock_get_cursor_factory):
        """Non-NULL previous_phase='not_real' raises ValueError."""
        with pytest.raises(ValueError, match="previous_phase"):
            append_market_phase_transition(
                canonical_market_id=1,
                new_phase="settling",
                changed_by="human:eric",
                previous_phase="not_real",
            )
        mock_get_cursor_factory.assert_not_called()

    @patch("precog.database.crud_canonical_market_phase_log.get_cursor")
    def test_none_previous_phase_accepted(self, mock_get_cursor_factory):
        """previous_phase=None is accepted."""
        wire_get_cursor_mock(mock_get_cursor_factory, returning_id=1)

        append_market_phase_transition(
            canonical_market_id=1,
            new_phase="open",
            changed_by="human:eric",
            previous_phase=None,
        )


# =============================================================================
# K7: constants module exposes CANONICAL_MARKET_LIFECYCLE_PHASES correctly
# =============================================================================


class TestConstantsCanonicalMarketLifecyclePhases:
    """K7 (build spec § 6 + § 0d D-4): constant exposed correctly."""

    def test_k7_constants_canonical_market_lifecycle_phases_5_value(self):
        """Constants module exposes CANONICAL_MARKET_LIFECYCLE_PHASES as 5-tuple Final."""
        assert isinstance(CANONICAL_MARKET_LIFECYCLE_PHASES, tuple)
        assert len(CANONICAL_MARKET_LIFECYCLE_PHASES) == 5
        # Verify exact contents
        expected = ("open", "suspended", "settling", "resolved", "voided")
        assert expected == CANONICAL_MARKET_LIFECYCLE_PHASES, (
            f"CANONICAL_MARKET_LIFECYCLE_PHASES drift; expected {expected!r}, "
            f"got {CANONICAL_MARKET_LIFECYCLE_PHASES!r}"
        )

    def test_event_constant_reduced_to_5_values_post_r8(self):
        """CANONICAL_EVENT_LIFECYCLE_PHASES reduced 8->5 in lockstep with Migration 0088 R8."""
        assert len(CANONICAL_EVENT_LIFECYCLE_PHASES) == 5
        expected = ("proposed", "listed", "pre_event", "live", "completed")
        assert expected == CANONICAL_EVENT_LIFECYCLE_PHASES

    def test_canonical_market_lifecycle_phases_imported_for_validation(self):
        """Sentinel: the SUT actually uses CANONICAL_MARKET_LIFECYCLE_PHASES."""
        assert "open" in CANONICAL_MARKET_LIFECYCLE_PHASES
        assert "voided" in CANONICAL_MARKET_LIFECYCLE_PHASES


# =============================================================================
# K8: 5-way SSOT parity (constant existence only; live-DB parity in
#     test_lifecycle_phase_vocabulary_ssot.py integration test)
# =============================================================================


class TestLifecyclePhaseVocabulary5WayParity:
    """K8 (build spec § 6 + § 0d D-4): the 5-way parity model is structurally sound."""

    def test_k8_two_vocabularies_exist_and_are_disjoint(self):
        """The 2 vocabularies share no values (R3 redistribution discipline)."""
        event_set = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
        market_set = set(CANONICAL_MARKET_LIFECYCLE_PHASES)
        overlap = event_set & market_set
        assert overlap == set(), (
            f"Event + market vocabularies must be disjoint; overlap = {sorted(overlap)}"
        )

    def test_k8_each_vocabulary_has_5_values(self):
        """Both vocabularies are 5-tuples post-Slot-4."""
        assert len(CANONICAL_EVENT_LIFECYCLE_PHASES) == 5
        assert len(CANONICAL_MARKET_LIFECYCLE_PHASES) == 5


# =============================================================================
# get_phase_history_for_market -- read query shape (mirror slot 0079)
# =============================================================================


class TestGetPhaseHistoryForMarket:
    """K1-K3: read query returns rows newest-first, parameterized."""

    @patch("precog.database.crud_canonical_market_phase_log.fetch_all")
    def test_k1_get_phase_history_returns_list_of_dicts(self, mock_fetch_all):
        """Function returns the fetch_all result verbatim (list of row dicts)."""
        expected_rows = [
            _full_market_phase_log_row_dict(id=2, new_phase="settling", previous_phase="open"),
            _full_market_phase_log_row_dict(id=1, new_phase="open", previous_phase=None),
        ]
        mock_fetch_all.return_value = expected_rows

        result = get_phase_history_for_market(42)

        assert result == expected_rows
        sql, params = mock_fetch_all.call_args[0]
        assert "SELECT" in sql
        assert "FROM canonical_market_phase_log" in sql
        assert "WHERE canonical_market_id = %s" in sql
        assert "ORDER BY transition_at DESC" in sql
        assert "LIMIT %s" in sql
        assert params == (42, 50)  # default limit=50

    @patch("precog.database.crud_canonical_market_phase_log.fetch_all")
    def test_k2_get_phase_history_custom_limit(self, mock_fetch_all):
        """Custom limit propagates into the query parameters."""
        mock_fetch_all.return_value = []
        get_phase_history_for_market(7, limit=10)
        _, params = mock_fetch_all.call_args[0]
        assert params == (7, 10)

    @patch("precog.database.crud_canonical_market_phase_log.fetch_all")
    def test_k3_get_phase_history_empty_result(self, mock_fetch_all):
        """Empty result returns empty list (no exception)."""
        mock_fetch_all.return_value = []
        result = get_phase_history_for_market(99999)
        assert result == []

    @patch("precog.database.crud_canonical_market_phase_log.fetch_all")
    def test_get_phase_history_query_projects_all_columns(self, mock_fetch_all):
        """SELECT projects every column the table exposes (Pattern 43 fidelity)."""
        mock_fetch_all.return_value = []
        get_phase_history_for_market(1)
        sql, _ = mock_fetch_all.call_args[0]
        for col in _ALL_COLUMNS:
            assert col in sql, f"Query must project column {col!r}"
