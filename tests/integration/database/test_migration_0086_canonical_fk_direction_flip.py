"""Integration tests for Migration 0086 -- canonical FK direction flip + denorm collapse (cleanup epic Slot 2).

Verifies the POST-MIGRATION state of the 9 schema-mutation surfaces shipped by
Migration 0086 (cleanup epic Slot 2; build spec
``memory/build_spec_slot_2_fk_direction_pm_memo.md``):

    1. ADD COLUMN ``teams.canonical_entity_id BIGINT NULL`` (1,034 existing
       rows start NULL).
    2. ADD CONSTRAINT FK NOT VALID ``teams_canonical_entity_id_fkey`` ->
       ``canonical_entities(id)`` ON DELETE SET NULL.
    3. VALIDATE CONSTRAINT (no-op against empty target; precedent-consistency).
    4. DROP TRIGGER ``trg_canonical_entity_team_backref`` ON
       ``canonical_entities``.
    5. DROP FUNCTION ``enforce_canonical_entity_team_backref()``.
    6. DROP CONSTRAINT ``canonical_entity_ref_team_id_fkey`` ON
       ``canonical_entities``.
    7. DROP COLUMN ``canonical_entities.ref_team_id``.
    8. DROP COLUMN ``canonical_events.game_id``.
    9. DROP COLUMN ``canonical_events.series_id``.

Test groups:
    - TestTeamsCanonicalEntityIdAdded: post-migration teams has the new
      BIGINT NULLABLE canonical_entity_id column with all 1,034 rows = NULL.
    - TestTeamsForeignKeyShape: FK constraint name +
      pg_constraint.confdeltype + convalidated = true (Pattern 84
      precedent-consistency).
    - TestTriggerAbsent: trg_canonical_entity_team_backref does not exist
      in pg_trigger; underlying function does not exist in pg_proc
      (orphan-function cleanup per build spec § 6 Sentinel risk #7).
    - TestRefTeamIdDropped: canonical_entities.ref_team_id column does not
      exist; the partial FK index is gone.
    - TestCanonicalEventsColumnsDropped: canonical_events.game_id and
      canonical_events.series_id columns do not exist; their associated
      FK constraints (canonical_events_game_id_fkey,
      canonical_events_series_id_fkey) do not exist.
    - TestEmptyStateSafety: the migration applied successfully against an
      empty FK target (canonical_entities at 0 rows); teams remained at
      1,034 rows (no row loss); canonical_events stays at 0 rows.

Round-trip CI gate inheritance (per session 94 close OQ-H1 verification):
    Slot 0086's ``downgrade()`` is a pure inverse of ``upgrade()``: every
    ADD has a matching DROP, the trigger function body is reproduced
    verbatim from Migration 0068.  The round-trip CI gate
    (``tests/integration/migrations/test_round_trip.py``) auto-discovers
    slot 0086 via its discovery loop and runs ``downgrade -> upgrade head``
    against it.  No separate downgrade-direction test is required here.

Pattern 91 V1.44 self-discipline:
    Tests assert against ``pg_constraint`` / ``pg_trigger`` / ``pg_proc``
    system catalogs and ``information_schema.columns``, NOT against
    indexname substring matches (per session 86 PG-truncation lesson in
    ``memory/feedback_pg_partition_index_name_truncation.md``).

Issue: #1155 (canonical-layer simplification epic)
Build spec: ``memory/build_spec_slot_2_fk_direction_pm_memo.md``
Precedent: ``tests/integration/database/test_migration_0085_naming_bundle.py``
ADR: ADR-118 V2.47 amendment lands at Slot 5 / session 99 (codifies the
    FK direction + denorm collapse rules this slot applies)

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# =============================================================================
# Group 1: ADD COLUMN teams.canonical_entity_id (Surface 1)
# =============================================================================


def test_teams_canonical_entity_id_column_exists(db_pool: Any) -> None:
    """``teams.canonical_entity_id`` exists post-Migration-0086 (Surface 1)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'teams'
              AND column_name = 'canonical_entity_id'
            """
        )
        row = cur.fetchone()
    assert row is not None, "teams.canonical_entity_id must exist post-Migration-0086 (Surface 1)"
    assert row["data_type"] == "bigint", (
        f"teams.canonical_entity_id must be BIGINT; got {row['data_type']!r}"
    )
    assert row["is_nullable"] == "YES", (
        f"teams.canonical_entity_id must be NULLABLE (1,034 existing rows start NULL); "
        f"got is_nullable={row['is_nullable']!r}"
    )


#: Documented minimum threshold for teams seed across dev + test envs.  Test
#: DB seeds ~984 teams; dev DB seeds ~1,034.  500 is a defensible floor that
#: catches non-catastrophic row loss without coupling to either env's exact
#: count.  Glokta Slot 2 Nit 1 (session 96) flagged the prior `> 0` check as
#: catching only catastrophic row loss (total=0); this threshold tightens
#: the row-preservation invariant.  A precise pre-vs-post count comparison
#: would require a pre-migration fixture snapshot (deferred to future
#: test-infra cleanup slot — not blocking).
_TEAMS_FIXTURE_SEED_MIN = 500


def test_teams_canonical_entity_id_all_rows_null(db_pool: Any) -> None:
    """All existing teams rows have NULL canonical_entity_id post-migration.

    Pattern 91 V1.44 premise verification at PM build time confirmed teams
    populated (1,034 rows in the dev DB; test DB seeds ~984); no rows
    populated on canonical_entity_id (Cohort 5+ matcher slot ~session 101
    populates this column when canonical_entities rows are first seeded).

    Three invariants are pinned:
      1. `total >= _TEAMS_FIXTURE_SEED_MIN` — row-preservation lower bound;
         catches both catastrophic loss (total=0) and non-trivial subset loss
         (e.g., total=200) without coupling to either env's exact seed count.
      2. `populated == 0` — FK-staging invariant: matcher slot has not yet
         run, so all canonical_entity_id values must be NULL.
      3. `total - populated == total` (algebraic complement) — collectively
         confirms every row has NULL canonical_entity_id, not just that no
         row has a non-NULL value.
    """
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total, COUNT(canonical_entity_id) AS populated FROM teams")
        row = cur.fetchone()
    assert row is not None, "teams count query must return a row"
    assert row["total"] >= _TEAMS_FIXTURE_SEED_MIN, (
        f"teams must preserve rows post-Migration-0086 (ADD COLUMN must not "
        f"drop rows); got total={row['total']}, expected >= "
        f"{_TEAMS_FIXTURE_SEED_MIN} (test DB ~984, dev DB ~1,034)"
    )
    assert row["populated"] == 0, (
        f"teams.canonical_entity_id must have 0 populated rows post-Migration-0086 "
        f"(Cohort 5+ matcher populates later); got {row['populated']}"
    )
    assert row["total"] - row["populated"] == row["total"], (
        f"algebraic complement: every teams row must have NULL "
        f"canonical_entity_id; got total={row['total']}, populated={row['populated']}"
    )


# =============================================================================
# Group 2: FK shape -- name + ON DELETE SET NULL polarity + VALIDATE state (Surface 2 + 3)
# =============================================================================


def test_teams_canonical_entity_id_fk_constraint_exists(db_pool: Any) -> None:
    """FK constraint ``teams_canonical_entity_id_fkey`` exists post-Migration-0086 (Surface 2)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT conname
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'teams'
              AND c.conname = 'teams_canonical_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "teams_canonical_entity_id_fkey must exist post-Migration-0086 (Surface 2)"
    )


def test_teams_canonical_entity_id_fk_target_is_canonical_entities(db_pool: Any) -> None:
    """FK references ``canonical_entities(id)`` post-Migration-0086.

    Pinning the FK direction (CL-1: teams -> canonical_entities, NOT
    canonical_entities -> teams) is the load-bearing schema invariant of
    Slot 2.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'teams'
              AND c.conname = 'teams_canonical_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is not None, "teams_canonical_entity_id_fkey definition must be retrievable"
    fk_def = row["def"]
    assert "REFERENCES canonical_entities(id)" in fk_def, (
        f"FK must reference canonical_entities(id) (CL-1 direction flip); got: {fk_def}"
    )


def test_teams_canonical_entity_id_fk_on_delete_set_null(db_pool: Any) -> None:
    """ON DELETE SET NULL polarity verified via ``pg_constraint.confdeltype = 'n'``.

    Per ADR-118 V2.42-B precedent: when a canonical_entity is hard-deleted,
    the teams row preserves its identity via SET NULL (canonical-outlives-
    platform contract).  ``confdeltype`` codes: 'a'=NO ACTION, 'r'=RESTRICT,
    'c'=CASCADE, 'n'=SET NULL, 'd'=SET DEFAULT.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT confdeltype
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'teams'
              AND c.conname = 'teams_canonical_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is not None, "FK must exist for confdeltype check"
    assert row["confdeltype"] == "n", (
        f"FK ON DELETE polarity must be SET NULL (confdeltype='n'); "
        f"got confdeltype={row['confdeltype']!r}"
    )


def test_teams_canonical_entity_id_fk_is_validated(db_pool: Any) -> None:
    """FK constraint is VALIDATED (``pg_constraint.convalidated = true``) post-migration.

    Pattern 84 V1.42 (NOT VALID + VALIDATE for FK by-analogy) -- the 3rd
    by-analogy use after slots 0080 + 0082.  Empty target (canonical_entities
    at 0 rows MCP-verified at session 96 start) means VALIDATE is a no-op
    against the data, but the queryable post-condition is convalidated=true.
    Build spec § 0 Pattern 91 catch #1: Pattern 84 application here is
    precedent-consistency (style choice), not safety against blocking
    writes.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT convalidated
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'teams'
              AND c.conname = 'teams_canonical_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is not None, "FK must exist for convalidated check"
    assert row["convalidated"] is True, (
        "FK must be VALIDATED post-migration (convalidated=true); Pattern 84 "
        "precedent-consistency requires VALIDATE CONSTRAINT to run after the "
        "NOT VALID add"
    )


# =============================================================================
# Group 3: Trigger + function absent (Surfaces 4 + 5)
# =============================================================================


def test_trg_canonical_entity_team_backref_trigger_absent(db_pool: Any) -> None:
    """``trg_canonical_entity_team_backref`` trigger does NOT exist post-Migration-0086.

    Hard ordering invariant: trigger MUST be dropped before the column it
    references (ref_team_id).  This test pins the post-state.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_canonical_entity_team_backref'
              AND tgrelid IN (
                  SELECT oid FROM pg_class
                  WHERE relname IN ('canonical_entities', 'canonical_entity')
              )
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "trg_canonical_entity_team_backref must NOT exist post-Migration-0086 "
        "(Surface 4: DROP TRIGGER); pre-Slot-2 polymorphic enforcement is retired."
    )


def test_enforce_canonical_entity_team_backref_function_absent(db_pool: Any) -> None:
    """``enforce_canonical_entity_team_backref()`` function does NOT exist post-Migration-0086.

    Build spec § 6 Sentinel risk #7: DROP TRIGGER does not auto-DROP the
    underlying function.  Without explicit DROP FUNCTION the function
    survives as orphan in pg_proc with zero callers.  This test pins the
    cleanup -- if the migration ever ships without DROP FUNCTION, this
    test fails loudly.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_proc
            WHERE proname = 'enforce_canonical_entity_team_backref'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "enforce_canonical_entity_team_backref() function must NOT survive as "
        "orphan post-Migration-0086 (Surface 5: DROP FUNCTION).  PG semantic: "
        "DROP TRIGGER does not auto-DROP the underlying function -- the "
        "migration MUST DROP FUNCTION explicitly.  If this test fails, the "
        "migration is leaking a dead trigger function."
    )


# =============================================================================
# Group 4: ref_team_id column + FK + index dropped (Surfaces 6 + 7)
# =============================================================================


def test_canonical_entities_ref_team_id_column_absent(db_pool: Any) -> None:
    """``canonical_entities.ref_team_id`` column does NOT exist post-Migration-0086 (Surface 7)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'canonical_entities'
              AND column_name = 'ref_team_id'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "canonical_entities.ref_team_id column must NOT exist post-Migration-0086 "
        "(Surface 7: DROP COLUMN); FK direction flip retires this column."
    )


def test_canonical_entity_ref_team_id_fkey_constraint_absent(db_pool: Any) -> None:
    """FK constraint ``canonical_entity_ref_team_id_fkey`` does NOT exist post-Migration-0086 (Surface 6)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'canonical_entity_ref_team_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "canonical_entity_ref_team_id_fkey FK constraint must NOT exist "
        "post-Migration-0086 (Surface 6: DROP CONSTRAINT)."
    )


def test_idx_canonical_entity_ref_team_id_index_absent(db_pool: Any) -> None:
    """Partial index ``idx_canonical_entity_ref_team_id`` is gone post-Migration-0086.

    PG semantic: indexes that reference only the dropped column drop along
    with the column.  This test pins the post-state; if the migration ever
    leaks an orphan index against a dropped column, this fails (PG would
    actually have already errored at DROP COLUMN time, but pinning the
    post-state is cheap and informative).
    """
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_canonical_entity_ref_team_id'")
        row = cur.fetchone()
    assert row is None, (
        "idx_canonical_entity_ref_team_id partial index must NOT exist "
        "post-Migration-0086 (auto-dropped with the ref_team_id column)."
    )


# =============================================================================
# Group 5: canonical_events.game_id + series_id columns + FKs dropped (Surfaces 8 + 9)
# =============================================================================


@pytest.mark.parametrize("dropped_column", ["game_id", "series_id"])
def test_canonical_events_denorm_columns_absent(
    db_pool: Any,
    dropped_column: str,
) -> None:
    """``canonical_events.{game_id, series_id}`` columns do NOT exist post-Migration-0086 (Surfaces 8 + 9)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'canonical_events'
              AND column_name = %s
            """,
            (dropped_column,),
        )
        row = cur.fetchone()
    assert row is None, (
        f"canonical_events.{dropped_column} column must NOT exist post-Migration-0086 "
        "(denorm collapse: canonical_events does not carry platform-side FKs)."
    )


@pytest.mark.parametrize(
    "dropped_constraint",
    ["canonical_events_game_id_fkey", "canonical_events_series_id_fkey"],
)
def test_canonical_events_denorm_fk_constraints_absent(
    db_pool: Any,
    dropped_constraint: str,
) -> None:
    """The denorm FK constraints (game_id, series_id) do NOT exist post-Migration-0086.

    PG drops associated FK constraints when their column is dropped.  This
    test pins the post-state; the failure mode it catches is a stale FK
    constraint surviving against a dropped column (PG would error at
    DROP COLUMN time, but pinning the post-state is the cleaner assertion
    for reviewer comprehension).
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = %s",
            (dropped_constraint,),
        )
        row = cur.fetchone()
    assert row is None, (
        f"{dropped_constraint} must NOT exist post-Migration-0086 (auto-dropped with its column)."
    )


# =============================================================================
# Group 6: Empty-state safety -- migration succeeded against empty FK target
# =============================================================================


@pytest.mark.parametrize(
    "table",
    ["canonical_entities", "canonical_events"],
)
def test_canonical_layer_tables_remain_empty_post_migration(
    db_pool: Any,
    table: str,
) -> None:
    """``canonical_entities`` and ``canonical_events`` remain at 0 rows post-Migration-0086.

    Pattern 91 V1.44 premise verification at PM build time confirmed both at
    0 rows pre-migration -- DROP COLUMN against empty tables is trivial; FK
    target empty means VALIDATE is no-op.  This test pins the post-state;
    if a regression starts seeding data into these tables before the
    Cohort 5+ matcher slot, the failure surfaces here.
    """
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")  # noqa: S608
        row = cur.fetchone()
    assert row is not None, f"{table} count query must return a row"
    assert row["cnt"] == 0, (
        f"{table} must have 0 rows post-Migration-0086 (canonical layer is empty "
        f"until Cohort 5+ writers ship); got {row['cnt']}"
    )
