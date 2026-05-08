"""Integration tests for migration 0068 -- Cohort 1B canonical entity foundation.

Verifies the POST-MIGRATION state of the four canonical-entity tables
introduced by migration 0068 -- ``canonical_entity_kinds``,
``canonical_entities`` (renamed from ``canonical_entity`` by Migration 0085 /
cleanup epic Slot 1), ``canonical_participant_roles``, and
``canonical_event_participants`` -- the lookup-table seed rows (12 entity_kinds
+ 10 participant_roles), and the **CONSTRAINT TRIGGER** that enforces the
polymorphic typed back-ref invariant (``entity_kind='team' => ref_team_id NOT NULL``).

This test file asserts against live post-migration schema (i.e., after
Alembic upgrades to head, including Migration 0085).  Per build spec
``memory/build_spec_slot_1_naming_bundle_pm_memo.md`` § 3 treatment rules,
it is updated in-place to track post-rename column / table identifiers
since the assertions key on ``information_schema`` queries against the
live DB rather than re-creating the table via raw SQL.

Test groups:
    - TestTableShapes: each of the 4 tables exists with the expected
      columns / types / nullability / defaults.
    - TestSeedRows: the 12 entity_kinds + 10 participant_roles are seeded
      verbatim per ADR-118 V2.38 decisions #1 + #4.
    - TestConstraintTrigger: **the highest-value gap** -- the
      ``trg_canonical_entity_team_backref`` CONSTRAINT TRIGGER body is
      exercised against:
        * INSERT entity_kind='team' + ref_team_id=NULL -> raises
        * INSERT entity_kind='team' + valid team_id -> succeeds
        * INSERT entity_kind='fighter' + ref_team_id=NULL -> succeeds (skip path)
        * UPDATE entity_kind_id -> 'team' on ref_team_id=NULL row -> raises
    - TestIndexes: 6 FK-column indexes (4 full + 2 partial WHERE).

Issue: #1012
Epic: #972 (Canonical Layer Foundation -- Phase B.5)

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor

pytestmark = [pytest.mark.integration]


# =============================================================================
# Per-table column spec (mirrors migration 0068 DDL verbatim).
#
# Each tuple: (column_name, data_type, is_nullable, default_substring_or_None).
# =============================================================================

_ENTITY_KINDS_COLS: list[tuple[str, str, str, str | None]] = [
    ("id", "integer", "NO", "nextval"),
    ("entity_kind", "text", "NO", None),
    ("description", "text", "YES", None),
    ("created_at", "timestamp with time zone", "NO", "now()"),
]

_ENTITY_COLS: list[tuple[str, str, str, str | None]] = [
    ("id", "bigint", "NO", "nextval"),
    ("entity_kind_id", "integer", "NO", None),
    ("entity_key", "text", "NO", None),
    ("display_name", "text", "NO", None),
    # Migration 0086 (cleanup epic Slot 2 / session 96) DROPPED ref_team_id
    # and flipped the FK direction (teams.canonical_entity_id replaces
    # canonical_entities.ref_team_id).  The polymorphic enforcement trigger
    # trg_canonical_entity_team_backref + its function are also dropped at
    # Slot 2 (Pattern 82 V2 scope-narrowing per V2.47 / Slot 5 / session 99).
    ("metadata", "jsonb", "YES", None),
    ("created_at", "timestamp with time zone", "NO", "now()"),
]

_PARTICIPANT_ROLES_COLS: list[tuple[str, str, str, str | None]] = [
    ("id", "integer", "NO", "nextval"),
    ("domain_id", "integer", "YES", None),
    ("role", "text", "NO", None),
    ("description", "text", "YES", None),
    ("created_at", "timestamp with time zone", "NO", "now()"),
]

_EVENT_PARTICIPANTS_COLS: list[tuple[str, str, str, str | None]] = [
    ("id", "bigint", "NO", "nextval"),
    ("canonical_event_id", "bigint", "NO", None),
    # Migration 0085 renamed canonical_event_participants.entity_id ->
    # canonical_entity_id (FK column naming rule application).
    ("canonical_entity_id", "bigint", "NO", None),
    ("role_id", "integer", "NO", None),
    ("sequence_number", "integer", "NO", None),
    ("created_at", "timestamp with time zone", "NO", "now()"),
]

# Migration 0085 renamed canonical_entity TABLE -> canonical_entities.
_TABLE_SPEC: list[tuple[str, list[tuple[str, str, str, str | None]]]] = [
    ("canonical_entity_kinds", _ENTITY_KINDS_COLS),
    ("canonical_entities", _ENTITY_COLS),
    ("canonical_participant_roles", _PARTICIPANT_ROLES_COLS),
    ("canonical_event_participants", _EVENT_PARTICIPANTS_COLS),
]


# Expected seed rows verbatim from migration ``_ENTITY_KIND_SEED`` /
# ``_PARTICIPANT_ROLE_SEED``.
_EXPECTED_ENTITY_KINDS: list[str] = [
    "team",
    "fighter",
    "candidate",
    "storm",
    "company",
    "location",
    "person",
    "product",
    "country",
    "organization",
    "commodity",
    "media",
]

_EXPECTED_PARTICIPANT_ROLES: list[tuple[str, str]] = [
    ("sports", "home"),
    ("sports", "away"),
    ("fighting", "fighter_a"),
    ("fighting", "fighter_b"),
    ("politics", "candidate"),
    ("politics", "moderator"),
    ("weather", "affected_location"),
    ("entertainment", "nominee"),
    ("entertainment", "winner"),
    ("entertainment", "host"),
]


# Expected indexes per migration upgrade() body (PK / UNIQUE indexes excluded).
# (table, indexname, must_be_unique, partial_predicate_or_None).
#
# Migration 0085 renamed canonical_entity -> canonical_entities; index NAMES
# are unchanged in this slot (index-name cleanup is deferred to a future
# cosmetic-cleanup slot per build spec § 11), so the index names below
# still carry their original "canonical_entity" / "entity_id" suffixes
# even though their parent tables / columns have been renamed.
_EXPECTED_INDEXES: list[tuple[str, str, bool, str | None]] = [
    ("canonical_entities", "idx_canonical_entity_entity_kind_id", False, None),
    # Migration 0086 (cleanup epic Slot 2) auto-dropped
    # idx_canonical_entity_ref_team_id when DROP COLUMN ref_team_id ran;
    # partial index on a dropped column drops with the column per PG
    # semantic.
    (
        "canonical_participant_roles",
        "idx_canonical_participant_roles_domain_id",
        False,
        "domain_id IS NOT NULL",
    ),
    (
        "canonical_event_participants",
        "idx_canonical_event_participants_canonical_event_id",
        False,
        None,
    ),
    (
        "canonical_event_participants",
        "idx_canonical_event_participants_entity_id",
        False,
        None,
    ),
    (
        "canonical_event_participants",
        "idx_canonical_event_participants_role_id",
        False,
        None,
    ),
]


# =============================================================================
# Group 1: Table shapes
# =============================================================================


@pytest.mark.parametrize(("table", "col_spec"), _TABLE_SPEC)
def test_table_column_shape(
    db_pool: Any,
    table: str,
    col_spec: list[tuple[str, str, str, str | None]],
) -> None:
    """Each column on each table has the migration-prescribed type / nullability / default."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = 'public'
            ORDER BY ordinal_position
            """,
            (table,),
        )
        rows = cur.fetchall()
    actual = {r["column_name"]: r for r in rows}
    expected_names = {c[0] for c in col_spec}
    assert expected_names.issubset(set(actual.keys())), (
        f"{table}: missing columns. expected={expected_names!r}, got={set(actual.keys())!r}"
    )

    for col_name, data_type, is_nullable, default_substr in col_spec:
        row = actual[col_name]
        assert row["data_type"] == data_type, (
            f"{table}.{col_name} type mismatch: expected {data_type!r}, got {row['data_type']!r}"
        )
        assert row["is_nullable"] == is_nullable, (
            f"{table}.{col_name} nullability mismatch: "
            f"expected {is_nullable!r}, got {row['is_nullable']!r}"
        )
        if default_substr is not None:
            actual_default = row["column_default"] or ""
            assert default_substr.lower() in actual_default.lower(), (
                f"{table}.{col_name} default missing {default_substr!r}; got {actual_default!r}"
            )


def test_canonical_event_participants_sequence_number_has_no_default(
    db_pool: Any,
) -> None:
    """``sequence_number`` has NO default per ADR-118 V2.38 decision #6 / Glokta carry-forward #5.

    Forces callers to reason about sequence explicitly (the 10-candidate
    election case is the motivating example).
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_name = 'canonical_event_participants'
              AND column_name = 'sequence_number'
              AND table_schema = 'public'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_event_participants.sequence_number must exist"
    assert row["column_default"] is None, (
        f"sequence_number must have NO default (Glokta carry-forward #5); "
        f"got {row['column_default']!r}"
    )


# =============================================================================
# Group 2: Seed rows (12 entity_kinds + 10 participant_roles)
# =============================================================================


def test_canonical_entity_kinds_has_all_seed_rows(db_pool: Any) -> None:
    """All 12 base entity_kinds seeded per ADR-118 V2.38 decision #1."""
    with get_cursor() as cur:
        cur.execute("SELECT entity_kind FROM canonical_entity_kinds ORDER BY id")
        rows = cur.fetchall()
    actual = [r["entity_kind"] for r in rows]
    for expected_kind in _EXPECTED_ENTITY_KINDS:
        assert expected_kind in actual, f"entity_kind {expected_kind!r} missing; got {actual!r}"
    assert len(actual) == len(_EXPECTED_ENTITY_KINDS), (
        f"canonical_entity_kinds row count drifted; expected {len(_EXPECTED_ENTITY_KINDS)}, "
        f"got {len(actual)}: {actual!r}"
    )


def test_canonical_participant_roles_has_all_seed_rows(db_pool: Any) -> None:
    """All 10 base participant_roles seeded per ADR-118 V2.38 decision #4."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT d.domain, r.role
            FROM canonical_participant_roles r
            LEFT JOIN canonical_event_domains d ON d.id = r.domain_id
            ORDER BY r.id
            """
        )
        rows = cur.fetchall()
    actual = [(r["domain"], r["role"]) for r in rows]
    for expected_pair in _EXPECTED_PARTICIPANT_ROLES:
        assert expected_pair in actual, (
            f"participant_role pair {expected_pair!r} missing; got {actual!r}"
        )
    assert len(actual) == len(_EXPECTED_PARTICIPANT_ROLES), (
        f"canonical_participant_roles row count drifted; expected {len(_EXPECTED_PARTICIPANT_ROLES)}, "
        f"got {len(actual)}: {actual!r}"
    )


def test_participant_role_fk_resolves_to_correct_domain(db_pool: Any) -> None:
    """Every seeded participant_role row has a non-NULL domain_id (initial seed has no cross-domain rows)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.role, r.domain_id, d.domain
            FROM canonical_participant_roles r
            LEFT JOIN canonical_event_domains d ON d.id = r.domain_id
            """
        )
        rows = cur.fetchall()
    # All initial seed rows must have a resolved domain (per migration docstring:
    # "All seed rows have concrete domain_id (no cross-domain rows in the initial seed)").
    for row in rows:
        assert row["domain"] is not None, (
            f"participant_role id={row['id']} ({row['role']!r}) seeded with NULL "
            f"domain_id -- initial seed should have no cross-domain rows"
        )


# =============================================================================
# Group 3: CONSTRAINT TRIGGER -- POST-SLOT-2 ABSENCE PIN
#
# Pre-Slot-2 (Migration 0086 / cleanup epic Slot 2 / session 96), this group
# fired the polymorphic enforcement trigger (Pattern 82 V2 forward-direction)
# with each behavioral case from ADR-118 V2.38 lines ~17376-17398.  Slot 2
# DROPped the trigger + DROPped the underlying function as part of the FK
# direction flip (CL-1: teams -> canonical_entities replaces canonical_entities
# -> teams typed back-ref).  Pattern 82 V2 SCOPE NARROWING in V2.47 applies the
# rule to canonical_markets only post-Slot-2; formal scope-narrowing codified
# at V2.47 ADR amendment (Slot 5 / session 99).
#
# Post-Slot-2 the only meaningful assertion at the Migration 0068 boundary is
# the trigger-and-function ABSENCE pin -- if a regression brings the trigger
# back via a future migration, this test fails.  Slot 0086 integration tests
# (test_migration_0086_canonical_fk_direction_flip.py) carry the orthogonal
# assertions: FK direction = teams -> canonical_entities, ref_team_id column
# absent, function not orphan in pg_proc, etc.
# =============================================================================


def test_trg_canonical_entity_team_backref_absent_post_slot_2(db_pool: Any) -> None:
    """``trg_canonical_entity_team_backref`` is gone post-Migration-0086.

    Pre-Slot-2 the trigger enforced ``entity_kind='team' => ref_team_id NOT NULL``
    via the CONSTRAINT TRIGGER pattern (Pattern 82 V2 canonical instance).
    Slot 2 (Migration 0086) DROP TRIGGER + DROP FUNCTION + DROP COLUMN
    ref_team_id -- the polymorphic enforcement apparatus retires.  This
    test pins the absence so a regression that re-adds the trigger surfaces
    at PR time.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_canonical_entity_team_backref'
              AND NOT tgisinternal
            """
        )
        row = cur.fetchone()
    assert row is None, (
        "trg_canonical_entity_team_backref must NOT exist post-Migration-0086 "
        "(Slot 2: DROP TRIGGER as part of FK direction flip + Pattern 82 V2 "
        "scope-narrowing)"
    )


def test_enforce_canonical_entity_team_backref_function_absent_post_slot_2(
    db_pool: Any,
) -> None:
    """``enforce_canonical_entity_team_backref()`` function is gone post-Migration-0086."""
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
        "enforce_canonical_entity_team_backref() function must NOT exist "
        "post-Migration-0086 (Slot 2: DROP FUNCTION explicit -- DROP TRIGGER "
        "alone leaves the function as orphan in pg_proc)"
    )


# =============================================================================
# Group 4: Indexes (4 full + 2 partial WHERE)
# =============================================================================


@pytest.mark.parametrize(
    ("table", "indexname", "must_be_unique", "partial_predicate"),
    _EXPECTED_INDEXES,
)
def test_index_exists_with_expected_shape(
    db_pool: Any,
    table: str,
    indexname: str,
    must_be_unique: bool,
    partial_predicate: str | None,
) -> None:
    """Each FK-column index exists with the migration-prescribed UNIQUE-ness + WHERE clause."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexname = %s",
            (table, indexname),
        )
        row = cur.fetchone()
    assert row is not None, f"{table}.{indexname} missing post-0068"
    indexdef = row["indexdef"]

    if must_be_unique:
        assert "CREATE UNIQUE" in indexdef, f"{indexname} must be UNIQUE; got: {indexdef}"
    else:
        assert "CREATE UNIQUE" not in indexdef, f"{indexname} must NOT be UNIQUE; got: {indexdef}"

    if partial_predicate is not None:
        assert partial_predicate in indexdef, (
            f"{indexname} must have partial WHERE {partial_predicate!r}; got: {indexdef}"
        )
    else:
        assert " WHERE " not in indexdef, f"{indexname} must NOT be partial; got: {indexdef}"


def test_canonical_entity_unique_kind_key(db_pool: Any) -> None:
    """``uq_canonical_entity_kind_key`` enforces UNIQUE (entity_kind_id, entity_key)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_entities'::regclass
              AND conname = 'uq_canonical_entity_kind_key'
            """
        )
        row = cur.fetchone()
    assert row is not None, "uq_canonical_entity_kind_key must exist"
    assert "UNIQUE (entity_kind_id, entity_key)" in row["def"], (
        f"uq_canonical_entity_kind_key must enforce composite UNIQUE; got: {row['def']}"
    )


def test_canonical_event_participants_composite_unique(db_pool: Any) -> None:
    """``uq_canonical_event_participants`` enforces UNIQUE (canonical_event_id, role_id, sequence_number)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_participants'::regclass
              AND conname = 'uq_canonical_event_participants'
            """
        )
        row = cur.fetchone()
    assert row is not None, "uq_canonical_event_participants must exist"
    assert "UNIQUE (canonical_event_id, role_id, sequence_number)" in row["def"], (
        f"uq_canonical_event_participants must enforce 3-column composite UNIQUE; got: {row['def']}"
    )


# =============================================================================
# Group 5: ON DELETE clauses on canonical_event_participants FKs (#1044 item 3)
#
# Mirrors the ON DELETE RESTRICT assertion in test_0069 against
# ``canonical_markets_canonical_event_id_fkey``.  The 3 FKs differ:
#   - canonical_event_id          -> canonical_events(id)        ON DELETE CASCADE
#     (participants are denormalization; deleting the parent event must
#     cascade-clean its participant rows -- no orphans)
#   - canonical_entity_id         -> canonical_entities(id)      ON DELETE RESTRICT
#     (deleting an entity referenced by a participant row is a data-loss
#     hazard; force the caller to detach explicitly).  Migration 0085
#     renamed the column entity_id -> canonical_entity_id and renamed the
#     FK constraint to canonical_event_participants_canonical_entity_id_fkey.
#   - role_id                     -> canonical_participant_roles ON DELETE RESTRICT
#     (same rationale; lookup-table rows must not be deleted while in use)
# =============================================================================


@pytest.mark.parametrize(
    ("constraint_name", "expected_clause"),
    [
        (
            "canonical_event_participants_canonical_event_id_fkey",
            "ON DELETE CASCADE",
        ),
        (
            # Renamed from canonical_event_participants_entity_id_fkey by
            # Migration 0085 / cleanup epic Slot 1 Surface 4b.
            "canonical_event_participants_canonical_entity_id_fkey",
            "ON DELETE RESTRICT",
        ),
        (
            "canonical_event_participants_role_id_fkey",
            "ON DELETE RESTRICT",
        ),
    ],
)
def test_canonical_event_participants_fk_on_delete_clause(
    db_pool: Any,
    constraint_name: str,
    expected_clause: str,
) -> None:
    """Pin the ON DELETE clause on each canonical_event_participants FK."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_participants'::regclass
              AND conname = %s
            """,
            (constraint_name,),
        )
        row = cur.fetchone()
    assert row is not None, f"{constraint_name} must exist on canonical_event_participants"
    fk_def = row["def"]
    assert expected_clause in fk_def, (
        f"{constraint_name} must include {expected_clause!r}; got: {fk_def}"
    )
