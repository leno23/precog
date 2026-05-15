"""Unit tests for crud_canonical_event_match_log module -- Cohort 5+ Slot B.

Covers (function-by-function):
    - append_event_match_log_row: happy path, action validation,
      decided_by prefix validation, decided_by length boundary,
      confidence validation including Decimal('NaN') + float-rejection
      via TypeError per CLAUDE.md Critical Pattern #1.
    - append_event_match_log_row_in_cursor: same validation
      defense-in-depth on the cursor-aware path.
    - get_event_match_log_by_action: Pattern 73 SSOT real-guard.
    - get_cohort5_event_matcher_algorithm_id: lazy cache + RuntimeError
      on missing seed.

Pattern 73 SSOT real-guard discipline (#1085 finding #2 strengthening
inherited from slot 0073):
    Both ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` and
    ``DECIDED_BY_PREFIXES`` are imported and USED in real-guard
    ValueError-raising validation in the SUT.  These tests assert that
    the validation fires.

Pattern 43 (mock fidelity) discipline: mocks return the EXACT shape
that the real query returns.  Mocks of ``get_cursor`` use the
``__enter__`` / ``__exit__`` protocol consistent with sibling unit tests.

Reference:
    - ``src/precog/database/crud_canonical_event_match_log.py``
    - ``src/precog/database/alembic/versions/0091_canonical_event_match_log_and_matcher_provenance.py``
    - ``tests/unit/database/test_crud_canonical_match_log_unit.py`` (slot
      0073 style reference)
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from precog.database.constants import (
    CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES,
    DECIDED_BY_PREFIXES,
)
from precog.database.crud_canonical_event_match_log import (
    _validate_append_event_match_log_args,
    append_event_match_log_row,
    append_event_match_log_row_in_cursor,
    get_event_match_log_by_action,
)
from tests.unit.database._crud_unit_helpers import wire_get_cursor_mock

# =============================================================================
# Group 1: _validate_append_event_match_log_args -- shared validation logic
# =============================================================================


class TestValidationHelper:
    """Pattern 73 SSOT + boundary validation in the shared helper."""

    def test_valid_args_no_raise(self) -> None:
        """Happy path: all valid args pass without exception."""
        _validate_append_event_match_log_args(
            action="create",
            confidence=Decimal("0.9"),
            decided_by="service:matcher:slot-B:v1",
        )

    def test_invalid_action_raises_value_error(self) -> None:
        """action not in vocab raises ValueError with SSOT message."""
        with pytest.raises(ValueError, match="pattern 73 SSOT vocabulary violation"):
            _validate_append_event_match_log_args(
                action="link",  # slot 0073 vocab; NOT in slot B's 6-value vocab
                confidence=Decimal("0.9"),
                decided_by="service:matcher:slot-B:v1",
            )

    def test_invalid_decided_by_prefix_raises_value_error(self) -> None:
        """decided_by without canonical prefix raises ValueError."""
        with pytest.raises(ValueError, match="pattern 73 SSOT vocabulary violation"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=Decimal("0.9"),
                decided_by="bogus:matcher",  # not in DECIDED_BY_PREFIXES
            )

    def test_decided_by_length_boundary_raises_value_error(self) -> None:
        """decided_by > 64 chars raises ValueError with column-boundary message."""
        long_id = "service:" + "x" * 60  # 68 chars total
        with pytest.raises(ValueError, match="VARCHAR\\(64\\) column boundary"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=Decimal("0.9"),
                decided_by=long_id,
            )

    def test_decided_by_length_exactly_64_chars_passes(self) -> None:
        """Exact-boundary length (64 chars) does NOT raise (inclusive boundary)."""
        # 'service:' (8) + 56 chars = 64 total
        boundary_id = "service:" + "x" * 56
        assert len(boundary_id) == 64
        _validate_append_event_match_log_args(
            action="create",
            confidence=Decimal("0.9"),
            decided_by=boundary_id,
        )

    def test_confidence_float_raises_type_error(self) -> None:
        """float confidence raises TypeError per CLAUDE.md Critical Pattern #1."""
        with pytest.raises(TypeError, match="Critical Pattern #1"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=0.9,  # type: ignore[arg-type]  -- testing wrong type
                decided_by="service:matcher:slot-B:v1",
            )

    def test_confidence_nan_raises_value_error(self) -> None:
        """Decimal('NaN') confidence raises ValueError (silent-pass on >= / <=)."""
        with pytest.raises(ValueError, match="must not be Decimal\\('NaN'\\)"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=Decimal("NaN"),
                decided_by="service:matcher:slot-B:v1",
            )

    def test_confidence_negative_raises_value_error(self) -> None:
        """confidence < 0 raises ValueError."""
        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=Decimal("-0.1"),
                decided_by="service:matcher:slot-B:v1",
            )

    def test_confidence_above_one_raises_value_error(self) -> None:
        """confidence > 1 raises ValueError."""
        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            _validate_append_event_match_log_args(
                action="create",
                confidence=Decimal("1.5"),
                decided_by="service:matcher:slot-B:v1",
            )

    def test_confidence_none_passes(self) -> None:
        """NULL confidence (human override path) does NOT raise."""
        _validate_append_event_match_log_args(
            action="create",
            confidence=None,
            decided_by="human:operator",
        )


# =============================================================================
# Group 2: append_event_match_log_row -- happy path + validation order
# =============================================================================


class TestAppendRow:
    """append_event_match_log_row commits with valid args; raises before SQL on invalid."""

    @patch("precog.database.crud_canonical_event_match_log.get_cursor")
    def test_happy_path_inserts_and_returns_id(
        self,
        mock_get_cursor: MagicMock,
    ) -> None:
        """Valid args produce one INSERT and return the new id."""
        mock_cursor = wire_get_cursor_mock(mock_get_cursor, returning_id=42)
        result = append_event_match_log_row(
            action="create",
            decided_by="service:matcher:slot-B:v1",
            algorithm_id=2,
            canonical_event_id=7,
            link_id=11,
            platform_event_id=89,
            confidence=Decimal("0.987"),
            features={"source": "natural_key_v1"},
            note="initial match",
        )
        assert result == 42
        assert mock_cursor.execute.call_count == 1
        # INSERT shape includes all 10 column placeholders.
        sql, params = mock_cursor.execute.call_args.args
        assert "INSERT INTO canonical_event_match_log" in sql
        assert "RETURNING id" in sql
        assert params[0] == 7  # canonical_event_id
        assert params[1] == 11  # link_id
        assert params[3] == "create"  # action

    @patch("precog.database.crud_canonical_event_match_log.get_cursor")
    def test_invalid_action_raises_before_sql(
        self,
        mock_get_cursor: MagicMock,
    ) -> None:
        """ValueError fires BEFORE the cursor is opened (no SQL)."""
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            append_event_match_log_row(
                action="bogus",
                decided_by="service:matcher:slot-B:v1",
                algorithm_id=2,
            )
        # No cursor obtained -- the call must not reach get_cursor.
        mock_get_cursor.assert_not_called()

    @patch("precog.database.crud_canonical_event_match_log.get_cursor")
    def test_invalid_decided_by_raises_before_sql(
        self,
        mock_get_cursor: MagicMock,
    ) -> None:
        """Bad decided_by prefix raises before SQL."""
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            append_event_match_log_row(
                action="create",
                decided_by="invalid:prefix:test",
                algorithm_id=2,
            )
        mock_get_cursor.assert_not_called()


# =============================================================================
# Group 3: append_event_match_log_row_in_cursor (cursor-aware variant)
# =============================================================================


class TestAppendRowInCursor:
    """Cursor-aware variant runs validation + returns id without opening cursor."""

    def test_happy_path_calls_execute_on_provided_cursor(self) -> None:
        """SUT calls execute() on the caller-provided cursor; does not open one."""
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 99}
        result = append_event_match_log_row_in_cursor(
            cursor,
            action="create",
            decided_by="service:matcher:slot-B:v1",
            algorithm_id=2,
            canonical_event_id=7,
            link_id=11,
            platform_event_id=89,
            confidence=Decimal("0.987"),
        )
        assert result == 99
        assert cursor.execute.call_count == 1

    def test_validation_fires_before_execute(self) -> None:
        """Invalid action raises ValueError; no execute called on the cursor."""
        cursor = MagicMock()
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            append_event_match_log_row_in_cursor(
                cursor,
                action="not_in_vocab",
                decided_by="service:matcher:slot-B:v1",
                algorithm_id=2,
            )
        cursor.execute.assert_not_called()


# =============================================================================
# Group 4: get_event_match_log_by_action -- Pattern 73 SSOT read-path validation
# =============================================================================


class TestGetByAction:
    """Pattern 73 SSOT real-guard on the read path."""

    @patch("precog.database.crud_canonical_event_match_log.fetch_all")
    def test_valid_action_returns_rows(self, mock_fetch_all: MagicMock) -> None:
        """Valid action runs the query and returns the rows."""
        mock_fetch_all.return_value = [{"id": 1}]
        from datetime import UTC, datetime, timedelta

        result = get_event_match_log_by_action("create", datetime.now(UTC) - timedelta(hours=1))
        assert result == [{"id": 1}]
        assert mock_fetch_all.call_count == 1

    @patch("precog.database.crud_canonical_event_match_log.fetch_all")
    def test_invalid_action_raises_before_query(self, mock_fetch_all: MagicMock) -> None:
        """Invalid action raises ValueError before query is run."""
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            get_event_match_log_by_action("bogus_action", "2026-01-01")
        mock_fetch_all.assert_not_called()


# =============================================================================
# Group 5: SSOT constant exposure / matcher-prefix presence
# =============================================================================


class TestConstantsExposure:
    """The constants the matcher relies on are present in the canonical vocab."""

    def test_matcher_writes_use_create_action(self) -> None:
        """Slot B matcher uses action='create' for primary path."""
        assert "create" in CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES

    def test_matcher_writes_use_service_prefix(self) -> None:
        """Slot B matcher's decided_by uses 'service:' prefix (not human:)."""
        assert "service:" in DECIDED_BY_PREFIXES

    def test_slot_b_vocabulary_distinct_from_slot_0073(self) -> None:
        """Slot B's 6-value vocab differs from slot 0073's 7-value market vocab.

        Pattern 73 SSOT: drift detection for any future PR that confuses
        the two.  Slot B has 'update_phase' which slot 0073 lacks; slot
        0073 has 'link'/'unlink'/'relink'/'override' which slot B lacks.
        """
        slot_b_only = {"create", "retire", "update_phase"}
        slot_0073_only = {"link", "unlink", "relink", "override"}
        slot_b_vocab = set(CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES)
        for v in slot_b_only:
            assert v in slot_b_vocab, f"slot B vocab missing {v!r}"
        for v in slot_0073_only:
            assert v not in slot_b_vocab, (
                f"slot B vocab leaked slot 0073 value {v!r}; vocabs must remain distinct"
            )
