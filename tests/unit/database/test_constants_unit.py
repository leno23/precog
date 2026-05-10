"""Unit tests for ``precog.database.constants`` — canonical SSOT constants.

These tests verify that ``CANONICAL_EVENT_LIFECYCLE_PHASES`` matches both
its specification (ADR-118 V2.40 Cohort 1 carry-forward item 3) and the
DDL CHECK constraint shipped in Migration 0070.

The Pattern 73 SSOT discipline that motivates this module also applies
*within this test file*: the load-bearing cross-validation test reads the
CHECK clause from Migration 0070 via regex parse rather than hardcoding
the 8 values a third time.  The two locations are (1) the constant in
``constants.py`` and (2) the SQL string literal in Migration 0070; this
test verifies they match.

Reference:
    - Issue #1038 (this PR's spec)
    - ADR-118 V2.40 Cohort 1 carry-forward item 3
    - Migration 0070 (``canonical_events_lifecycle_phase_check``)
    - DEVELOPMENT_PATTERNS V1.37 Pattern 73
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_type_hints

import pytest

from precog.database import constants
from precog.database.constants import CANONICAL_EVENT_LIFECYCLE_PHASES

pytestmark = [pytest.mark.unit]

# Path to Migration 0070, the second SSOT location for the lifecycle_phase
# vocabulary.  Resolved relative to the repo root (parents[3] == repo root
# from tests/unit/database/test_constants_unit.py).
MIGRATION_0070_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "precog"
    / "database"
    / "alembic"
    / "versions"
    / "0070_cohort_1_carryforward_hardening.py"
)

# Expected canonical 8 values.  Hardcoded ONCE here as the test's own
# specification — this is the single point at which a human re-asserts
# "these are the 8 values per ADR-118 V2.40."  Updating this requires a new
# migration (alembic) + updating CANONICAL_EVENT_LIFECYCLE_PHASES + updating
# Migration 0070's CHECK constraint in lockstep.  All three must move
# together; a partial update is the failure mode this test is designed to
# catch.
EXPECTED_PHASES: tuple[str, ...] = (
    "proposed",
    "listed",
    "pre_event",
    "live",
    "completed",
)
"""Slot 4 (Migration 0088 R8) reduced 8->5; resolution-tier states moved to
canonical_markets.lifecycle_phase per R3 redistribution.  See
``CANONICAL_MARKET_LIFECYCLE_PHASES`` for the 5-value market vocabulary."""


class TestCanonicalEventLifecyclePhasesContents:
    """Direct value/shape assertions on the constant."""

    def test_constant_matches_expected_5_values(self) -> None:
        """Constant equals the spec's exact 5-tuple post-Slot-4 R8 reduction."""
        assert CANONICAL_EVENT_LIFECYCLE_PHASES == EXPECTED_PHASES

    def test_constant_length_is_exactly_5(self) -> None:
        """Drift detector — count must be exactly 5 (post-Slot-4 R8 reduction)."""
        assert len(CANONICAL_EVENT_LIFECYCLE_PHASES) == 5

    def test_all_values_are_str(self) -> None:
        """Every entry is a ``str`` — type discipline for DDL comparison."""
        for value in CANONICAL_EVENT_LIFECYCLE_PHASES:
            assert isinstance(value, str), f"Expected str, got {type(value).__name__}: {value!r}"

    def test_constant_is_tuple(self) -> None:
        """Container is a tuple (immutable) — not a list or set."""
        assert isinstance(CANONICAL_EVENT_LIFECYCLE_PHASES, tuple)

    def test_constant_is_immutable(self) -> None:
        """Tuples have no item-assignment — defensive check on the contract."""
        with pytest.raises(TypeError):
            CANONICAL_EVENT_LIFECYCLE_PHASES[0] = "mutated"  # type: ignore[index]


class TestCanonicalEventLifecyclePhasesTyping:
    """Type-annotation discipline."""

    def test_constant_annotated_as_final_tuple_of_str(self) -> None:
        """``Final[tuple[str, ...]]`` is preserved in module annotations.

        With ``include_extras=True``, ``get_type_hints`` keeps the outer
        ``Final[...]`` wrapper; we unwrap it to assert the inner
        ``tuple[str, ...]`` shape.

        Note: ``annotation.__origin__ is Final`` behavior is verified on
        CPython 3.12 + 3.14 (this project's supported targets per CLAUDE.md).
        Earlier CPython versions may unwrap differently; if support widens,
        loosen the outer-origin check or rely on mypy/pyright for Final
        correctness instead.
        """
        from typing import Final

        hints = get_type_hints(constants, include_extras=True)
        assert "CANONICAL_EVENT_LIFECYCLE_PHASES" in hints, (
            "Constant missing type annotation in module __annotations__"
        )
        annotation = hints["CANONICAL_EVENT_LIFECYCLE_PHASES"]

        # Outer wrapper must be ``Final`` — distinguishes ``Final[tuple[...]]``
        # from a bare ``tuple[...]``.  __origin__ on ``Final[X]`` is the
        # ``Final`` special form itself.
        outer_origin = getattr(annotation, "__origin__", None)
        assert outer_origin is Final, (
            f"Expected Final outer wrapper, got {outer_origin!r} (annotation={annotation!r})"
        )

        # Unwrap ``Final[X]`` -> ``X`` (the inner tuple[str, ...] type).
        inner_args = getattr(annotation, "__args__", ())
        assert len(inner_args) == 1, (
            f"Expected exactly one type arg inside Final[...], got {inner_args!r}"
        )
        inner = inner_args[0]

        # Inner must be ``tuple[str, ...]`` — origin tuple, args (str, ...).
        inner_origin = getattr(inner, "__origin__", None)
        inner_args_inner = getattr(inner, "__args__", ())
        assert inner_origin is tuple, (
            f"Expected tuple origin inside Final, got {inner_origin!r} (inner={inner!r})"
        )
        assert inner_args_inner == (str, Ellipsis), (
            f"Expected (str, ...) args, got {inner_args_inner!r}"
        )


class TestMigration0070CheckHistoricalShape:
    """Historical artifact: Migration 0070 carries the ORIGINAL 8-value CHECK.

    Slot 4 (Migration 0088) reduced the canonical_events.lifecycle_phase CHECK
    from 8 values to 5 (R8 bundle); per Pattern 87 the original 0070 file
    keeps its at-ship-time 8-value CHECK (immutable).  The current
    constant value reflects the post-0088 state (5 values).  Live DB parity
    is enforced by ``test_lifecycle_phase_vocabulary_ssot.py``
    (integration test that queries the live CHECKs, not parses migration
    file text).

    This test class now verifies the migration FILE's at-ship-time shape
    (8 values) as a historical artifact.  Drift between current constant
    and 0070 file is EXPECTED post-Slot-4 (the constant moved; the file
    didn't, by Pattern 87 design).
    """

    # Slot 4 R8 bundle dropped these 4 values from the canonical_events vocabulary
    # (they migrated to canonical_markets.lifecycle_phase per R3).
    SLOT_4_R8_DROPPED_VALUES: frozenset[str] = frozenset(
        {"suspended", "settling", "resolved", "voided"}
    )

    # Slot 4 R8 added this value to canonical_events.lifecycle_phase.
    SLOT_4_R8_ADDED_VALUE: str = "completed"

    def test_migration_0070_file_exists(self) -> None:
        """Migration 0070 path resolves — guard for the regex parse below."""
        assert MIGRATION_0070_PATH.is_file(), f"Migration 0070 not found at {MIGRATION_0070_PATH}"

    def test_migration_0070_at_ship_time_shape_preserved(self) -> None:
        """Migration 0070 file content (Pattern 87 immutable) has its at-ship-time 8 values.

        Slot 4 (Migration 0088) reduced the LIVE CHECK to 5 values; this
        test verifies the FILE was not edited (Pattern 87 discipline).
        """
        source = MIGRATION_0070_PATH.read_text(encoding="utf-8")

        check_match = re.search(
            r"CHECK\s*\(\s*lifecycle_phase\s+IN\s*\((.*?)\)\)",
            source,
            re.DOTALL | re.IGNORECASE,
        )
        assert check_match is not None, (
            "Could not find ``CHECK (lifecycle_phase IN (...))`` clause "
            f"in {MIGRATION_0070_PATH.name} — has the migration been "
            "rewritten?  Pattern 87 forbids editing shipped migrations."
        )

        check_body = check_match.group(1)
        migration_values = tuple(re.findall(r"'([^']+)'", check_body))

        # At-ship-time vocabulary: the 8-value original.
        expected_at_ship = {
            "proposed",
            "listed",
            "pre_event",
            "live",
            "suspended",
            "settling",
            "resolved",
            "voided",
        }
        assert set(migration_values) == expected_at_ship, (
            "Migration 0070 file has been edited (Pattern 87 violation).  "
            "The at-ship-time 8-value vocabulary must be preserved verbatim; "
            f"reductions/additions land in subsequent migrations.\n"
            f"  File now contains: {sorted(migration_values)}\n"
            f"  At-ship-time:      {sorted(expected_at_ship)}"
        )

    def test_constant_reflects_post_slot_4_state_not_migration_0070(self) -> None:
        """The constant tracks the LIVE CHECK shape (post-0088), not 0070.

        Pattern 87: 0070 is immutable; subsequent migrations (0088 R8)
        reduce the live CHECK and the constant moves in lockstep with
        the live state.  The 4 dropped values from 0070 are NOT in the
        current constant; the 1 added value IS.
        """
        constant_set = set(CANONICAL_EVENT_LIFECYCLE_PHASES)
        # The 4 dropped values must NOT be in the current constant.
        for dropped in self.SLOT_4_R8_DROPPED_VALUES:
            assert dropped not in constant_set, (
                f"Post-Slot-4 R8 reduction: {dropped!r} should NOT be in "
                f"CANONICAL_EVENT_LIFECYCLE_PHASES; got {constant_set!r}"
            )
        # The 1 added value must be present.
        assert self.SLOT_4_R8_ADDED_VALUE in constant_set, (
            f"Post-Slot-4 R8: {self.SLOT_4_R8_ADDED_VALUE!r} should be in "
            f"CANONICAL_EVENT_LIFECYCLE_PHASES; got {constant_set!r}"
        )
