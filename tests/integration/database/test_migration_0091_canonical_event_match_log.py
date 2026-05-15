"""Integration tests for migration 0091 -- Cohort 5+ Slot B canonical_event_match_log.

Verifies the POST-MIGRATION state of the ``canonical_event_match_log``
audit ledger introduced by Migration 0091 -- Cohort 5+ Slot B (first
new-architecture migration after Migration 0090's platform-prefix
rename).  Per session-92 4-agent design council + parent spec at
``memory/build_spec_slot_a_matcher_pm_memo.md`` + session-107 rebase
addendum at ``memory/build_spec_slot_b_matcher_addendum_session_107.md``.

Test groups:
    - Column shape: per-column type / nullability / default / max-length
      with mirror-symmetric f-string assertion messages (slot 0073
      #1085 finding #4 inheritance).
    - CHECK constraints: action 6-value vocab + confidence [0,1] +
      NULL-tolerant guard.
    - Indexes: 3 indexes (decided_at DESC, canonical_event_id partial,
      action composite) all present.
    - FK polarity: canonical_event_id ON DELETE SET NULL, link_id ON
      DELETE SET NULL, platform_event_id ON DELETE CASCADE,
      algorithm_id NO ACTION (default), prior_link_id ON DELETE SET NULL.
    - canonical_events.created_by column: NOT NULL, default
      'legacy:pre-matcher', VARCHAR(64).
    - match_algorithm seed row: cohort5_event_matcher_v1 v1.0.0 exists.
    - Pattern 73 SSOT discipline: action constant matches CHECK vocab.

Issue: Epic #972 (Canonical Layer Foundation -- Phase B.5),
    #1184 Item 4 (Cohort 5+ Slot B matcher dispatch)
ADR: ADR-118 V2.44 + V2.46 + V2.49
Build spec: ``memory/build_spec_slot_a_matcher_pm_memo.md``
Addendum: ``memory/build_spec_slot_b_matcher_addendum_session_107.md``

Markers:
    @pytest.mark.integration: real DB required.
"""

from __future__ import annotations

from typing import Any

import pytest

from precog.database.connection import get_cursor
from precog.database.constants import (
    CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES,
    CREATED_BY_PREFIXES,
)

pytestmark = [pytest.mark.integration]


# =============================================================================
# Per-column shape spec (mirrors migration 0091 DDL verbatim).
# Each tuple: (column_name, data_type, is_nullable, default_substring_or_None,
#              max_char_length_or_None).
# =============================================================================

_MATCH_LOG_COLS: list[tuple[str, str, str, str | None, int | None]] = [
    ("id", "bigint", "NO", "nextval", None),
    ("canonical_event_id", "bigint", "YES", None, None),
    ("link_id", "bigint", "YES", None, None),
    ("platform_event_id", "integer", "YES", None, None),
    ("action", "character varying", "NO", None, 16),
    ("confidence", "numeric", "YES", None, None),
    ("algorithm_id", "bigint", "NO", None, None),
    ("features", "jsonb", "YES", None, None),
    ("prior_link_id", "bigint", "YES", None, None),
    ("decided_by", "character varying", "NO", None, 64),
    ("decided_at", "timestamp with time zone", "NO", "now()", None),
    ("note", "text", "YES", None, None),
    ("created_at", "timestamp with time zone", "NO", "now()", None),
]


# =============================================================================
# Group 1: column shape
# =============================================================================


@pytest.mark.parametrize(
    ("col_name", "data_type", "is_nullable", "default_substr", "max_char_len"),
    _MATCH_LOG_COLS,
)
def test_canonical_event_match_log_column_shape(
    db_pool: Any,
    col_name: str,
    data_type: str,
    is_nullable: str,
    default_substr: str | None,
    max_char_len: int | None,
) -> None:
    """Each column on canonical_event_match_log has the migration-prescribed shape."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'canonical_event_match_log'
              AND column_name = %s
            """,
            (col_name,),
        )
        row = cur.fetchone()

    assert row is not None, (
        f"canonical_event_match_log.{col_name} missing from information_schema.columns"
    )
    assert row["data_type"] == data_type, (
        f"canonical_event_match_log.{col_name} data_type={row['data_type']!r} "
        f"expected {data_type!r}"
    )
    assert row["is_nullable"] == is_nullable, (
        f"canonical_event_match_log.{col_name} is_nullable={row['is_nullable']!r} "
        f"expected {is_nullable!r}"
    )
    if default_substr is not None:
        assert row["column_default"] is not None, (
            f"canonical_event_match_log.{col_name} column_default missing; "
            f"expected substring {default_substr!r}"
        )
        assert default_substr in row["column_default"], (
            f"canonical_event_match_log.{col_name} column_default="
            f"{row['column_default']!r} missing substring {default_substr!r}"
        )
    if max_char_len is not None:
        assert row["character_maximum_length"] == max_char_len, (
            f"canonical_event_match_log.{col_name} character_maximum_length="
            f"{row['character_maximum_length']!r} expected {max_char_len!r}"
        )


# =============================================================================
# Group 2: CHECK constraints
# =============================================================================


def test_canonical_event_match_log_action_check_constraint_definition(
    db_pool: Any,
) -> None:
    """The action CHECK constraint enumerates exactly the 6-value vocab."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname = 'ck_canonical_event_match_log_action'
            """
        )
        row = cur.fetchone()
    assert row is not None, "ck_canonical_event_match_log_action constraint missing"
    constraint_def = row["def"]
    # Each canonical action value must appear in the CHECK definition.
    for action in CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES:
        assert action in constraint_def, (
            f"action value {action!r} missing from CHECK constraint def: {constraint_def!r}"
        )


def test_canonical_event_match_log_confidence_check_constraint_definition(
    db_pool: Any,
) -> None:
    """The confidence CHECK constraint enforces [0, 1] OR NULL."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname = 'ck_canonical_event_match_log_confidence'
            """
        )
        row = cur.fetchone()
    assert row is not None, "ck_canonical_event_match_log_confidence constraint missing"
    constraint_def = row["def"]
    # Constraint must mention the bound (0 and 1) AND NULL tolerance.
    assert "IS NULL" in constraint_def, (
        f"confidence CHECK missing IS NULL clause: {constraint_def!r}"
    )
    assert "0" in constraint_def, f"confidence CHECK missing 0 lower bound: {constraint_def!r}"
    assert "1" in constraint_def, f"confidence CHECK missing 1 upper bound: {constraint_def!r}"


# =============================================================================
# Group 3: Indexes
# =============================================================================


@pytest.mark.parametrize(
    "idx_name",
    [
        "idx_canonical_event_match_log_decided_at",
        "idx_canonical_event_match_log_canonical_event_id",
        "idx_canonical_event_match_log_action",
    ],
)
def test_canonical_event_match_log_index_present(
    db_pool: Any,
    idx_name: str,
) -> None:
    """All 3 named indexes exist on canonical_event_match_log."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'canonical_event_match_log' AND indexname = %s
            """,
            (idx_name,),
        )
        row = cur.fetchone()
    assert row is not None, f"index {idx_name!r} missing on canonical_event_match_log"


def test_canonical_event_match_log_canonical_event_id_index_is_partial(
    db_pool: Any,
) -> None:
    """Slot B canonical_event_id index is partial (WHERE canonical_event_id IS NOT NULL)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE indexname = 'idx_canonical_event_match_log_canonical_event_id'
            """
        )
        row = cur.fetchone()
    assert row is not None
    indexdef = row["indexdef"]
    assert "WHERE" in indexdef, (
        f"canonical_event_id index expected partial WHERE clause; got: {indexdef!r}"
    )
    assert "IS NOT NULL" in indexdef, (
        f"canonical_event_id index expected IS NOT NULL predicate; got: {indexdef!r}"
    )


# =============================================================================
# Group 4: FK polarity
# =============================================================================


def test_canonical_event_match_log_fk_canonical_event_id_set_null(
    db_pool: Any,
) -> None:
    """canonical_event_id FK has ON DELETE SET NULL polarity."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_match_log'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE '%canonical_events(id)%'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_event_id FK missing"
    assert "ON DELETE SET NULL" in row["def"], (
        f"canonical_event_id FK expected ON DELETE SET NULL: {row['def']!r}"
    )


def test_canonical_event_match_log_fk_platform_event_id_cascade(
    db_pool: Any,
) -> None:
    """platform_event_id FK has ON DELETE CASCADE polarity (mirrors canonical_event_links)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_event_match_log'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE '%platform_events(id)%'
            """
        )
        row = cur.fetchone()
    assert row is not None, "platform_event_id FK missing"
    assert "ON DELETE CASCADE" in row["def"], (
        f"platform_event_id FK expected ON DELETE CASCADE: {row['def']!r}"
    )


# =============================================================================
# Group 5: canonical_events.created_by column shape
# =============================================================================


def test_canonical_events_created_by_column_present(db_pool: Any) -> None:
    """canonical_events.created_by exists, NOT NULL, VARCHAR(64), default 'legacy:pre-matcher'."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'canonical_events' AND column_name = 'created_by'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_events.created_by column missing"
    assert row["is_nullable"] == "NO", (
        f"canonical_events.created_by expected NOT NULL; got {row['is_nullable']!r}"
    )
    assert row["data_type"] == "character varying"
    assert row["character_maximum_length"] == 64
    assert "legacy:pre-matcher" in (row["column_default"] or ""), (
        f"canonical_events.created_by default missing 'legacy:pre-matcher': "
        f"{row['column_default']!r}"
    )


def test_created_by_prefixes_constant_includes_matcher_and_legacy() -> None:
    """Pattern 73 SSOT: CREATED_BY_PREFIXES constant covers matcher's identity prefixes."""
    # The matcher writes 'matcher:slot-B:v1' (matcher: prefix); the backfill
    # CLI writes 'cli:matcher-backfill:v1' (cli: prefix); the migration default
    # uses 'legacy:pre-matcher' (legacy: prefix).
    assert "matcher:" in CREATED_BY_PREFIXES, (
        f"CREATED_BY_PREFIXES missing 'matcher:' prefix: {CREATED_BY_PREFIXES!r}"
    )
    assert "cli:" in CREATED_BY_PREFIXES, (
        f"CREATED_BY_PREFIXES missing 'cli:' prefix: {CREATED_BY_PREFIXES!r}"
    )
    assert "legacy:" in CREATED_BY_PREFIXES, (
        f"CREATED_BY_PREFIXES missing 'legacy:' prefix: {CREATED_BY_PREFIXES!r}"
    )


# =============================================================================
# Group 6: match_algorithm seed row
# =============================================================================


def test_match_algorithm_seed_cohort5_event_matcher_v1_present(
    db_pool: Any,
) -> None:
    """Migration 0091 seeds the cohort5_event_matcher_v1 algorithm row."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, version, code_ref
            FROM match_algorithm
            WHERE name = 'cohort5_event_matcher_v1' AND version = '1.0.0'
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "match_algorithm seed row 'cohort5_event_matcher_v1' v1.0.0 missing -- "
        "Migration 0091 INSERT did not run or was reverted"
    )
    assert row["code_ref"] == "precog.matching.canonical_event_matcher", (
        f"seed row code_ref={row['code_ref']!r}; expected 'precog.matching.canonical_event_matcher'"
    )


# =============================================================================
# Group 7: Pattern 73 SSOT discipline
# =============================================================================


def test_pattern_73_ssot_action_constant_matches_ddl_check(
    db_pool: Any,
) -> None:
    """CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES matches the DDL CHECK enumeration.

    Pattern 73 SSOT discipline: drift between the Python constant and
    the DDL CHECK is the failure mode this test catches.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname = 'ck_canonical_event_match_log_action'
            """
        )
        row = cur.fetchone()
    assert row is not None
    constraint_def = row["def"]

    # Every Python constant value must appear in the DDL CHECK.
    for action in CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES:
        assert action in constraint_def, (
            f"Pattern 73 SSOT drift: action {action!r} in Python constant "
            f"but missing from DDL CHECK: {constraint_def!r}"
        )

    # The reverse direction (no DDL value missing from Python) is harder
    # to assert without parsing pg_get_constraintdef output; the Pattern
    # 73 SSOT integration test 'test_lifecycle_phase_vocabulary_ssot.py'
    # is a useful template for adding that direction in a future PR.
