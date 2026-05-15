"""Unit tests for crud_canonical_event_links.create_link helper (Cohort 5+ Slot B).

Slot 0072 originally shipped read + retire helpers only (deliberate Phase
1 gap per Glokta gap awareness).  Slot B (Migration 0091 + matcher
module) ADDS ``create_link()`` + ``create_link_in_cursor()`` for the
matcher's V2.44 atomic transaction-spanning two-table-write.

Covers:
    - create_link: happy path, link_state validation, decided_by prefix
      + length validation, confidence Decimal-only enforcement,
      confidence NaN guard, confidence bounds.
    - create_link_in_cursor: same validation on cursor-aware path.

Pattern 73 SSOT real-guard discipline (#1085 finding #2 strengthening):
    ``LINK_STATE_VALUES`` and ``DECIDED_BY_PREFIXES`` are imported and
    used in real-guard validation.

Reference:
    - ``src/precog/database/crud_canonical_event_links.py``
      (``create_link`` helper added by Slot B)
    - ``src/precog/matching/canonical_event_matcher.py`` (consumer)
    - ``tests/unit/database/test_crud_canonical_event_links_unit.py``
      (slot 0072 sibling test module; complements rather than overlaps)
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from precog.database.crud_canonical_event_links import (
    _validate_create_link_args,
    create_link,
    create_link_in_cursor,
)
from tests.unit.database._crud_unit_helpers import wire_get_cursor_mock

# =============================================================================
# Group 1: validation helper
# =============================================================================


class TestValidateCreateLinkArgs:
    """Pattern 73 SSOT + Decimal-only enforcement on create_link path."""

    def test_valid_args_no_raise(self) -> None:
        _validate_create_link_args(
            decided_by="service:matcher:slot-B:v1",
            link_state="active",
            confidence=Decimal("0.95"),
        )

    def test_invalid_link_state_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pattern 73 SSOT vocabulary violation"):
            _validate_create_link_args(
                decided_by="service:matcher:slot-B:v1",
                link_state="invalid",
                confidence=Decimal("0.95"),
            )

    def test_invalid_decided_by_prefix_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pattern 73 SSOT vocabulary violation"):
            _validate_create_link_args(
                decided_by="invalid:prefix",
                link_state="active",
                confidence=Decimal("0.95"),
            )

    def test_decided_by_length_boundary_raises_value_error(self) -> None:
        long_id = "service:" + "x" * 60
        with pytest.raises(ValueError, match="VARCHAR\\(64\\) column boundary"):
            _validate_create_link_args(
                decided_by=long_id,
                link_state="active",
                confidence=Decimal("0.95"),
            )

    def test_confidence_float_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Critical Pattern #1"):
            _validate_create_link_args(
                decided_by="service:matcher:slot-B:v1",
                link_state="active",
                confidence=0.95,  # type: ignore[arg-type]
            )

    def test_confidence_nan_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must not be Decimal\\('NaN'\\)"):
            _validate_create_link_args(
                decided_by="service:matcher:slot-B:v1",
                link_state="active",
                confidence=Decimal("NaN"),
            )

    def test_confidence_out_of_bounds_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            _validate_create_link_args(
                decided_by="service:matcher:slot-B:v1",
                link_state="active",
                confidence=Decimal("1.5"),
            )


# =============================================================================
# Group 2: create_link -- top-level write path
# =============================================================================


class TestCreateLink:
    """create_link commits via get_cursor; validates before opening cursor."""

    @patch("precog.database.crud_canonical_event_links.get_cursor")
    def test_happy_path_returns_link_id(self, mock_get_cursor: MagicMock) -> None:
        """Valid args INSERT and return the new link's id."""
        mock_cursor = wire_get_cursor_mock(mock_get_cursor, returning_id=17)
        result = create_link(
            canonical_event_id=42,
            platform_event_id=89,
            confidence=Decimal("0.987"),
            algorithm_id=2,
            decided_by="service:matcher:slot-B:v1",
        )
        assert result == 17
        assert mock_cursor.execute.call_count == 1
        sql, params = mock_cursor.execute.call_args.args
        assert "INSERT INTO canonical_event_links" in sql
        assert "RETURNING id" in sql
        # Param positions per INSERT statement.
        assert params[0] == 42  # canonical_event_id
        assert params[1] == 89  # platform_event_id
        assert params[2] == "active"  # link_state (default)

    @patch("precog.database.crud_canonical_event_links.get_cursor")
    def test_invalid_link_state_raises_before_sql(self, mock_get_cursor: MagicMock) -> None:
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            create_link(
                canonical_event_id=42,
                platform_event_id=89,
                confidence=Decimal("0.987"),
                algorithm_id=2,
                decided_by="service:matcher:slot-B:v1",
                link_state="invalid",
            )
        mock_get_cursor.assert_not_called()

    @patch("precog.database.crud_canonical_event_links.get_cursor")
    def test_float_confidence_raises_type_error_before_sql(
        self, mock_get_cursor: MagicMock
    ) -> None:
        with pytest.raises(TypeError, match="Critical Pattern #1"):
            create_link(
                canonical_event_id=42,
                platform_event_id=89,
                confidence=0.987,  # type: ignore[arg-type]
                algorithm_id=2,
                decided_by="service:matcher:slot-B:v1",
            )
        mock_get_cursor.assert_not_called()


# =============================================================================
# Group 3: create_link_in_cursor -- V2.44 atomic transaction-spanning path
# =============================================================================


class TestCreateLinkInCursor:
    """Cursor-aware variant returns id without opening a new cursor."""

    def test_happy_path_calls_execute_on_provided_cursor(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 25}
        result = create_link_in_cursor(
            cursor,
            canonical_event_id=42,
            platform_event_id=89,
            confidence=Decimal("0.987"),
            algorithm_id=2,
            decided_by="service:matcher:slot-B:v1",
        )
        assert result == 25
        assert cursor.execute.call_count == 1

    def test_validation_runs_before_execute(self) -> None:
        cursor = MagicMock()
        with pytest.raises(ValueError, match="pattern 73 SSOT"):
            create_link_in_cursor(
                cursor,
                canonical_event_id=42,
                platform_event_id=89,
                confidence=Decimal("0.987"),
                algorithm_id=2,
                decided_by="service:matcher:slot-B:v1",
                link_state="invalid_state",
            )
        cursor.execute.assert_not_called()
