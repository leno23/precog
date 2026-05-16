"""Integration tests for Migration 0092 -- rename
``cohort5_event_matcher_v1`` -> ``event_matcher_v1``.

Verifies the POST-MIGRATION state of the ``match_algorithm`` seed row
introduced by Migration 0091 (Cohort 5+ Slot B) after Migration 0092's
rename UPDATE has run.  Per session-110 user direction (scope B,
symmetric rename dropping session-planning shorthand from production
data values) + build spec at
``memory/build_spec_migration_0092_rename_event_matcher.md``.

Test groups:
    - Seed row present under the NEW name (``event_matcher_v1``).
    - Seed row ABSENT under the OLD name (``cohort5_event_matcher_v1``).
    - Seed row id is stable across the rename (UPDATE, not DELETE +
      INSERT) -- load-bearing for ``canonical_event_match_log.algorithm_id``
      FK references.

Pattern 87 (Append-Only Migrations) note:

    Migration 0091's seed-row test
    (``test_migration_0091_canonical_event_match_log.py``) was UPDATED
    in the same PR as Migration 0092 to look up the row by the NEW
    name (``event_matcher_v1``) -- that test runs against head state,
    which is post-0092 after this migration ships.  Updating that
    test is NOT a Pattern 87 violation: Pattern 87 covers MIGRATION
    files (the .py files under ``alembic/versions/``), not tests
    that verify migration outcomes.  The migration's docstring +
    SQL literals describing the historical name STAY AS WRITTEN.

ADR: ADR-118 V2.44 (atomicity contract; matcher infrastructure).
Build spec: ``memory/build_spec_migration_0092_rename_event_matcher.md``

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# =============================================================================
# Group 1: post-rename seed-row state
# =============================================================================


def test_match_algorithm_seed_renamed_to_event_matcher_v1(
    db_pool: Any,
) -> None:
    """Post-Migration-0092: the matcher seed row carries the NEW name.

    Verifies the UPDATE in 0092's upgrade() landed.  The lookup keys
    on ``code_ref`` (immutable, set at Migration 0091 INSERT time) so
    the assertion is name-agnostic at the SELECT side -- if the rename
    didn't run, the assertion would fail naturally.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, version, code_ref
            FROM match_algorithm
            WHERE code_ref = 'precog.matching.canonical_event_matcher'
              AND version = '1.0.0'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "match_algorithm seed row for canonical_event_matcher missing -- "
        "Migration 0091 INSERT or Migration 0092 UPDATE did not run"
    )
    assert row["name"] == "event_matcher_v1", (
        f"Migration 0092 rename did not land; got name={row['name']!r}, expected 'event_matcher_v1'"
    )


def test_match_algorithm_seed_old_name_absent(db_pool: Any) -> None:
    """Post-Migration-0092: NO row exists under the OLD name.

    Verifies the rename is exclusive (the row moved, did not duplicate).
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM match_algorithm
            WHERE name = 'cohort5_event_matcher_v1' AND version = '1.0.0'
            """
        )
        row = cur.fetchone()
    assert row["n"] == 0, (
        f"Found {row['n']} row(s) still carrying the old "
        "'cohort5_event_matcher_v1' name -- Migration 0092 rename "
        "did not land cleanly"
    )


def test_match_algorithm_seed_id_stable_across_rename(db_pool: Any) -> None:
    """Post-Migration-0092: exactly one matcher seed row carries the new name.

    Load-bearing invariant: the rename uses UPDATE (not DELETE +
    INSERT), so the row's BIGSERIAL ``id`` allocated at Migration
    0091 INSERT time is preserved.  Every
    ``canonical_event_match_log.algorithm_id`` FK reference (zero
    today; matcher not yet activated) continues resolving to the
    same physical row.

    We assert the post-rename invariant rather than a specific id
    value because BIGSERIAL allocation order varies between dev /
    test / production DBs depending on each environment's seed
    insertion history.  The invariants:

        1. Exactly ONE row matches the matcher's code_ref + version
           (no duplicate INSERT happened).
        2. That row carries the NEW name ``event_matcher_v1``.
        3. That row's ``id`` is strictly greater than ``manual_v1.id``
           (proving it was inserted AFTER Migration 0071's seed,
           consistent with Migration 0091's post-0071 INSERT ordering).
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name
            FROM match_algorithm
            WHERE code_ref = 'precog.matching.canonical_event_matcher'
              AND version = '1.0.0'
            """
        )
        matcher_rows = cur.fetchall()
        cur.execute("SELECT id FROM match_algorithm WHERE name = 'manual_v1' AND version = '1.0.0'")
        manual_row = cur.fetchone()

    assert len(matcher_rows) == 1, (
        f"Expected exactly 1 matcher seed row; got {len(matcher_rows)}.  "
        "If 0, Migration 0091 INSERT did not run.  If >1, an unexpected "
        "duplicate INSERT happened (e.g., DELETE + INSERT instead of "
        "UPDATE for the rename, which would break algorithm_id FK stability)."
    )
    matcher_row = matcher_rows[0]
    assert matcher_row["name"] == "event_matcher_v1", (
        f"Migration 0092 rename did not land; got name={matcher_row['name']!r}, "
        "expected 'event_matcher_v1'"
    )
    assert manual_row is not None, "manual_v1 seed row from Migration 0071 missing"
    assert matcher_row["id"] > manual_row["id"], (
        f"matcher seed row id={matcher_row['id']} not greater than manual_v1 "
        f"id={manual_row['id']}; ordering invariant broken (suggests the "
        "matcher seed was inserted before Migration 0071's manual_v1, which "
        "violates the migration sequence)"
    )
