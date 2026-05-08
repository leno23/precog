"""Integration tests for Migration 0085 -- canonical naming bundle (cleanup epic Slot 1).

Verifies the POST-MIGRATION state of the four naming surfaces shipped by
Migration 0085 (cleanup epic Slot 1; build spec
``memory/build_spec_slot_1_naming_bundle_pm_memo.md``):

    Surface 1: ``canonical_entity`` TABLE -> ``canonical_entities``.
    Surface 2: ``canonical_events.domain_id`` -> ``event_domain_id``.
    Surface 3: ``canonical_events.entities_sorted`` -> ``participants_sorted``.
    Surface 4a: ``canonical_event_participants.entity_id`` -> ``canonical_entity_id``.
    Surface 4b: FK constraint ``canonical_event_participants_entity_id_fkey``
                -> ``canonical_event_participants_canonical_entity_id_fkey``.

Test groups:
    - TestTableRename: post-rename ``canonical_entities`` table exists with
      the expected columns; pre-rename ``canonical_entity`` table is gone
      from ``information_schema`` (NOT a backup table; full rename).
    - TestColumnRenames: each of the 3 column renames is reflected in
      ``information_schema.columns`` -- new name present, old name absent
      on each table.
    - TestForeignKeyAutoRewrite: the inbound FK
      ``canonical_event_participants_canonical_entity_id_fkey`` references
      ``canonical_entities(id)`` (auto-rewritten by PG when the parent
      table renamed) with ON DELETE RESTRICT preserved.
    - TestForeignKeyConstraintNameRename: the FK constraint name is now
      ``canonical_event_participants_canonical_entity_id_fkey``; the
      pre-rename name ``canonical_event_participants_entity_id_fkey`` is
      absent.
    - TestIndexAutoRewrite: indexes referencing the renamed columns
      (``idx_canonical_events_domain_id`` and
      ``idx_canonical_event_participants_entity_id``) survive structurally
      and now reference the new column names in their ``indexdef``.  Index
      names themselves are unchanged in this slot (deferred to a future
      cosmetic slot per build spec § 11).
    - TestSiblingColumnsNotRenamed: ``canonical_event_types.domain_id`` and
      ``canonical_participant_roles.domain_id`` remain unchanged --
      Migration 0085 only renames the ``canonical_events.domain_id``
      column, NOT the same-named columns on sibling lookup tables.
    - TestRowZero: all 3 affected tables have 0 rows post-migration (no
      data backfill; rename is metadata-only).

Round-trip CI gate inheritance (per session 94 close OQ-H1 verification):
    Slot 0085's ``downgrade()`` is a pure inverse of ``upgrade()``: every
    RENAME has a matching reverse RENAME.  The round-trip CI gate
    (``tests/integration/migrations/test_round_trip.py``) auto-discovers
    slot 0085 via its discovery loop and runs ``downgrade -> upgrade
    head`` against it.  No separate downgrade test is required here.

Issue: #1155 (canonical-layer simplification epic)
Build spec: ``memory/build_spec_slot_1_naming_bundle_pm_memo.md``
Precedent: ``tests/integration/database/test_migration_0067_canonical_events_foundation.py``
ADR: ADR-118 V2.47 amendment lands at Slot 5 / session 99 (codifies the
    naming convention this slot applies)

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# =============================================================================
# Group 1: Table rename (Surface 1)
# =============================================================================


def test_canonical_entities_table_exists_post_rename(db_pool: Any) -> None:
    """``canonical_entities`` table exists post-Migration-0085 (Surface 1)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'canonical_entities'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_entities table must exist post-Migration-0085"


def test_canonical_entity_table_absent_post_rename(db_pool: Any) -> None:
    """Pre-rename ``canonical_entity`` table is gone (full rename, not a copy)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'canonical_entity'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "canonical_entity table must NOT exist post-Migration-0085 -- "
        "rename is a full move, not a copy.  Backup-table-style coexistence "
        "would indicate the rename did not complete."
    )


@pytest.mark.parametrize(
    "expected_column",
    # Pre-Migration-0086 the column inventory included ref_team_id; Slot 2
    # (cleanup epic / session 96) DROPped that column as part of the FK
    # direction flip.  The remaining 6 columns are the post-Slot-2 inventory
    # and were preserved across the Slot 1 rename (the assertion this test
    # actually pins -- rename is metadata-only).
    ["id", "entity_kind_id", "entity_key", "display_name", "metadata", "created_at"],
)
def test_canonical_entities_preserves_columns_post_rename(
    db_pool: Any,
    expected_column: str,
) -> None:
    """All pre-rename ``canonical_entity`` columns survive on ``canonical_entities``.

    Table rename is a metadata-only operation; column inventory is unchanged.

    Post-Slot-2 (Migration 0086) the ref_team_id column was DROPPED as part
    of the FK direction flip; the parametrize list above reflects the
    post-Slot-2 6-column inventory.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'canonical_entities'
              AND column_name = %s
            """,
            (expected_column,),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"canonical_entities.{expected_column} must exist post-Migration-0085 "
        "(table rename preserves all columns)"
    )


# =============================================================================
# Group 2: Column renames (Surfaces 2, 3, 4a)
# =============================================================================


@pytest.mark.parametrize(
    ("table", "new_column", "old_column", "expected_data_type", "expected_is_nullable"),
    [
        ("canonical_events", "event_domain_id", "domain_id", "integer", "NO"),
        ("canonical_events", "participants_sorted", "entities_sorted", "ARRAY", "NO"),
        (
            "canonical_event_participants",
            "canonical_entity_id",
            "entity_id",
            "bigint",
            "NO",
        ),
    ],
)
def test_column_renamed_with_preserved_shape(
    db_pool: Any,
    table: str,
    new_column: str,
    old_column: str,
    expected_data_type: str,
    expected_is_nullable: str,
) -> None:
    """Each renamed column appears under its new name with type+nullability preserved.

    PG ``ALTER TABLE ... RENAME COLUMN`` is a metadata-only operation; the
    column's data type, nullability, and any defaults survive the rename.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            (table, new_column),
        )
        new_row = cur.fetchone()

        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            (table, old_column),
        )
        old_row = cur.fetchone()

    assert new_row is not None, (
        f"{table}.{new_column} must exist post-Migration-0085 (rename target)"
    )
    assert new_row["data_type"] == expected_data_type, (
        f"{table}.{new_column} type must be {expected_data_type!r} post-rename; "
        f"got {new_row['data_type']!r}"
    )
    assert new_row["is_nullable"] == expected_is_nullable, (
        f"{table}.{new_column} nullability must be {expected_is_nullable!r} post-rename; "
        f"got {new_row['is_nullable']!r}"
    )
    assert old_row is None, (
        f"{table}.{old_column} must NOT exist post-Migration-0085 -- rename "
        "is a full move, not a copy."
    )


# =============================================================================
# Group 3: FK constraint auto-rewrite + explicit constraint rename (Surface 4)
# =============================================================================


def test_canonical_event_participants_fk_auto_rewrites_to_canonical_entities(
    db_pool: Any,
) -> None:
    """The inbound FK now references ``canonical_entities(id)`` (PG auto-rewrite on RENAME TABLE).

    PG semantic: when a parent table is renamed, all inbound FK constraint
    definitions auto-rewrite their target table reference.  No application-
    side action is required.  This test pins the post-rewrite shape so any
    future regression that breaks the auto-rewrite (or accidentally ships
    a manually-recreated FK against the old name) is caught.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'canonical_event_participants'
              AND c.conname = 'canonical_event_participants_canonical_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "canonical_event_participants_canonical_entity_id_fkey must exist post-Migration-0085 "
        "(constraint renamed in Surface 4b)"
    )
    fk_def = row["def"]
    assert "REFERENCES canonical_entities(id)" in fk_def, (
        f"FK definition must reference renamed table canonical_entities(id); got: {fk_def}"
    )
    assert "ON DELETE RESTRICT" in fk_def, (
        f"FK ON DELETE RESTRICT polarity must be preserved across rename; got: {fk_def}"
    )
    assert "canonical_entity_id" in fk_def, (
        f"FK column reference must use renamed column canonical_entity_id; got: {fk_def}"
    )


def test_pre_rename_fk_constraint_name_absent(db_pool: Any) -> None:
    """The pre-rename FK constraint name is absent post-Migration-0085 (Surface 4b)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class cl ON c.conrelid = cl.oid
            WHERE cl.relname = 'canonical_event_participants'
              AND c.conname = 'canonical_event_participants_entity_id_fkey'
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "Pre-rename constraint name canonical_event_participants_entity_id_fkey "
        "must NOT exist post-Migration-0085 -- explicit RENAME CONSTRAINT in "
        "Surface 4b removes it."
    )


# =============================================================================
# Group 4: Index auto-rewrite (column rename does NOT rename index)
# =============================================================================


@pytest.mark.parametrize(
    ("table", "indexname", "renamed_column"),
    [
        ("canonical_events", "idx_canonical_events_domain_id", "event_domain_id"),
        (
            "canonical_event_participants",
            "idx_canonical_event_participants_entity_id",
            "canonical_entity_id",
        ),
    ],
)
def test_index_def_references_renamed_column(
    db_pool: Any,
    table: str,
    indexname: str,
    renamed_column: str,
) -> None:
    """Indexes auto-rewrite their column references on ALTER TABLE RENAME COLUMN.

    PG semantic: index definitions reference columns by oid, not by name;
    the rendered ``indexdef`` text reflects the current column name at
    query time.  Index names themselves are unchanged in this slot
    (renaming indexes is deferred to a future cosmetic-cleanup slot).

    The build spec § 11 PM Picard adjudication explicitly defers index name
    cleanup; this test pins the auto-rewrite behavior + the deferred-rename
    decision so any future regression is caught.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexname = %s",
            (table, indexname),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"{table}.{indexname} must exist post-Migration-0085 -- rename does not drop indexes"
    )
    indexdef = row["indexdef"]
    assert renamed_column in indexdef, (
        f"{indexname} indexdef must reference renamed column {renamed_column!r} (PG auto-rewrite); "
        f"got: {indexdef}"
    )


# =============================================================================
# Group 5: Sibling tables NOT renamed (Pattern 91 self-discipline)
# =============================================================================


@pytest.mark.parametrize(
    ("table", "preserved_column"),
    [
        ("canonical_event_types", "domain_id"),
        ("canonical_participant_roles", "domain_id"),
    ],
)
def test_sibling_table_domain_id_not_renamed(
    db_pool: Any,
    table: str,
    preserved_column: str,
) -> None:
    """Sibling lookup tables' ``domain_id`` columns are NOT renamed.

    Migration 0085 renames ONLY ``canonical_events.domain_id`` ->
    ``event_domain_id`` (FK column naming rule application).  The sibling
    columns ``canonical_event_types.domain_id`` and
    ``canonical_participant_roles.domain_id`` are local FKs into
    ``canonical_event_domains.id``; their column names are appropriately
    scoped within their own tables and are NOT touched by this slot.

    This test pins the narrow scope of the rename so any future regression
    that mass-renames ``domain_id`` across the canonical layer is caught
    at PR time.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            (table, preserved_column),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"{table}.{preserved_column} must STILL exist post-Migration-0085 -- "
        "Migration 0085 only renames canonical_events.domain_id, NOT this column."
    )


# =============================================================================
# Group 6: 0-row sanity (no data backfill needed; rename is metadata-only)
# =============================================================================


@pytest.mark.parametrize(
    "table",
    ["canonical_entities", "canonical_events", "canonical_event_participants"],
)
def test_renamed_tables_remain_at_zero_rows_post_migration(
    db_pool: Any,
    table: str,
) -> None:
    """All 3 renamed tables remain at 0 rows post-Migration-0085.

    Pattern 91 V1.44 premise verification at PM build time confirmed all 3
    tables at 0 rows -- rename is structurally cheap (metadata-only, no
    row rewrite).  This test pins the post-migration row count so any
    regression that backfills test data into the canonical layer (which
    should remain empty until Cohort 5+ writers ship) is caught.
    """
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")  # noqa: S608
        row = cur.fetchone()
    assert row is not None, f"{table} count query must return a row"
    assert row["cnt"] == 0, (
        f"{table} must have 0 rows post-Migration-0085 (canonical layer is empty "
        f"until Cohort 5+ writers ship); got {row['cnt']}"
    )
