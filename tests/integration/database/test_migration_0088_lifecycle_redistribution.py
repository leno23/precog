"""Integration tests for Migration 0088 -- canonical-tier lifecycle_phase redistribution.

Cleanup epic Slot 4 (R3 + R6 + R8 bundle).  Verifies the POST-MIGRATION
state of:
    1. ``canonical_markets.lifecycle_phase`` column (NEW; 5-value CHECK)
    2. ``canonical_events.lifecycle_phase`` CHECK reduced 8->5 (R8 bundle)
    3. ``canonical_event_phase_log.new_phase`` + ``previous_phase`` CHECK
       reduced 8->5 (R8 mirror)
    4. ``canonical_market_phase_log`` table (NEW; mirror slot 0079 shape)
    5. 3 functional indexes on canonical_market_phase_log
    6. ``log_canonical_market_phase_transition()`` trigger function
    7. ``trg_canonical_markets_log_phase_transition`` AFTER trigger

Test groups:
    - I1-I2: canonical_markets.lifecycle_phase column shape + CHECK fires
    - I3: canonical_events.lifecycle_phase CHECK reduced 8->5 (R8)
    - I4: canonical_event_phase_log mirror reduction
    - I5: canonical_market_phase_log table shape
    - I6: 3 indexes on canonical_market_phase_log
    - I7: trigger function + trigger exist with correct shape
    - I8a: behavioral -- INSERT path emits NULL->phase audit row
    - I8b: behavioral -- UPDATE OF lifecycle_phase emits old->new audit row
    - I9: behavioral -- UPDATE same value -> NO log row (IS DISTINCT FROM)
    - I9b: behavioral -- UPDATE non-lifecycle column -> NO log row
    - I10: downgrade reverses cleanly (round-trip parity at schema level)

Markers:
    @pytest.mark.integration: real DB required.

Reference:
    - ``src/precog/database/alembic/versions/0088_canonical_lifecycle_phase_redistribution.py``
    - ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md`` § 6
    - Slot 0079 mirror template (test_migration_0079_canonical_event_phase_log.py)
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import psycopg2.errors
import pytest

from precog.database.connection import get_cursor
from precog.database.constants import (
    CANONICAL_EVENT_LIFECYCLE_PHASES,
    CANONICAL_MARKET_LIFECYCLE_PHASES,
)
from tests.integration.database._canonical_event_helpers import (
    _cleanup_canonical_event,
    _seed_canonical_event,
)
from tests.integration.database._canonical_market_helpers import (
    _cleanup_canonical_market,
    _seed_canonical_market,
)

pytestmark = [pytest.mark.integration]


# =============================================================================
# Per-column shape spec for canonical_market_phase_log (mirrors slot 0079).
#
# Each tuple: (column_name, data_type, is_nullable, default_substring_or_None,
#              max_char_length_or_None).
# =============================================================================

_MARKET_PHASE_LOG_COLS: list[tuple[str, str, str, str | None, int | None]] = [
    ("id", "bigint", "NO", "nextval", None),
    ("canonical_market_id", "bigint", "NO", None, None),
    ("previous_phase", "character varying", "YES", None, 32),
    ("new_phase", "character varying", "NO", None, 32),
    ("transition_at", "timestamp with time zone", "NO", "now()", None),
    ("changed_by", "character varying", "NO", None, 64),
    ("note", "text", "YES", None, None),
    ("created_at", "timestamp with time zone", "NO", "now()", None),
]


# =============================================================================
# I1: canonical_markets.lifecycle_phase column added with default + 5-value CHECK
# =============================================================================


def test_migration_0088_adds_canonical_markets_lifecycle_phase(db_pool: Any) -> None:
    """canonical_markets.lifecycle_phase exists, VARCHAR(32) NOT NULL DEFAULT 'open'."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'canonical_markets'
              AND column_name = 'lifecycle_phase'
              AND table_schema = 'public'
            """
        )
        row = cur.fetchone()
    assert row is not None, "canonical_markets.lifecycle_phase must exist post-Migration-0088"
    assert row["data_type"] == "character varying", (
        f"lifecycle_phase data_type should be character varying; got {row['data_type']!r}"
    )
    assert row["is_nullable"] == "NO", "lifecycle_phase must be NOT NULL"
    assert row["character_maximum_length"] == 32, (
        f"lifecycle_phase max length should be 32; got {row['character_maximum_length']}"
    )
    assert row["column_default"] is not None, "lifecycle_phase must have a DEFAULT clause"
    assert "open" in row["column_default"], (
        f"lifecycle_phase DEFAULT should reference 'open'; got {row['column_default']!r}"
    )


# =============================================================================
# I2: canonical_markets.lifecycle_phase CHECK fires on invalid values
# =============================================================================


def test_migration_0088_canonical_markets_lifecycle_phase_check(db_pool: Any) -> None:
    """canonical_markets.lifecycle_phase CHECK rejects values outside 5-value vocab."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)

    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO canonical_markets (
                        canonical_event_id, market_type_general, natural_key_hash,
                        lifecycle_phase
                    ) VALUES (%s, 'binary', %s, 'not_a_real_phase')
                    """,
                    (seeded_event_id, f"TEST-cm-{suffix}".encode()),
                )
    finally:
        _cleanup_canonical_event(seeded_event_id)


def test_migration_0088_canonical_markets_lifecycle_phase_accepts_each_vocab_value(
    db_pool: Any,
) -> None:
    """Each value in CANONICAL_MARKET_LIFECYCLE_PHASES is accepted by the CHECK."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    seeded_market_ids: list[int] = []

    try:
        for i, phase in enumerate(CANONICAL_MARKET_LIFECYCLE_PHASES):
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO canonical_markets (
                        canonical_event_id, market_type_general, natural_key_hash,
                        lifecycle_phase
                    ) VALUES (%s, 'binary', %s, %s)
                    RETURNING id
                    """,
                    (seeded_event_id, f"TEST-cm-{suffix}-{i}".encode(), phase),
                )
                seeded_market_ids.append(int(cur.fetchone()["id"]))
    finally:
        for cm_id in seeded_market_ids:
            _cleanup_canonical_market(cm_id)
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I3: canonical_events.lifecycle_phase CHECK reduced 8->5 (R8 bundle)
# =============================================================================


def test_migration_0088_canonical_events_lifecycle_phase_reduced_to_5(db_pool: Any) -> None:
    """Post-0088: canonical_events.lifecycle_phase CHECK rejects the 4 dropped values."""
    # The 4 dropped values from R8: suspended, settling, resolved, voided.
    # canonical_events.lifecycle_phase is NOT NULL with no default; we need to
    # build the INSERT manually with the seed helper's full shape minus an
    # invalid lifecycle_phase override.
    for dropped_phase in ("suspended", "settling", "resolved", "voided"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO canonical_events (
                        event_domain_id, event_type_id, participants_sorted,
                        resolution_window, natural_key_hash, title, lifecycle_phase
                    ) VALUES (
                        (SELECT id FROM canonical_event_domains WHERE domain = 'sports'),
                        (SELECT et.id FROM canonical_event_types et
                         JOIN canonical_event_domains d ON d.id = et.domain_id
                         WHERE d.domain = 'sports' AND et.event_type = 'game'),
                        ARRAY[]::INTEGER[],
                        tstzrange(now(), now() + interval '1 day', '[)'),
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        f"TEST-evt-{uuid.uuid4().hex[:8]}".encode(),
                        f"R8 dropped {dropped_phase}",
                        dropped_phase,
                    ),
                )


def test_migration_0088_canonical_events_accepts_5_value_vocab(db_pool: Any) -> None:
    """Post-0088: canonical_events.lifecycle_phase CHECK accepts the new 5-value vocab."""
    seeded_ids: list[int] = []
    try:
        for phase in CANONICAL_EVENT_LIFECYCLE_PHASES:
            with get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO canonical_events (
                        event_domain_id, event_type_id, participants_sorted,
                        resolution_window, natural_key_hash, title, lifecycle_phase
                    ) VALUES (
                        (SELECT id FROM canonical_event_domains WHERE domain = 'sports'),
                        (SELECT et.id FROM canonical_event_types et
                         JOIN canonical_event_domains d ON d.id = et.domain_id
                         WHERE d.domain = 'sports' AND et.event_type = 'game'),
                        ARRAY[]::INTEGER[],
                        tstzrange(now(), now() + interval '1 day', '[)'),
                        %s,
                        %s,
                        %s
                    )
                    RETURNING id
                    """,
                    (
                        f"TEST-evt-{uuid.uuid4().hex[:8]}".encode(),
                        f"R8 5-vocab {phase}",
                        phase,
                    ),
                )
                seeded_ids.append(int(cur.fetchone()["id"]))
    finally:
        for ev_id in seeded_ids:
            _cleanup_canonical_event(ev_id)


# =============================================================================
# I4: canonical_event_phase_log mirror reduction (8->5)
# =============================================================================


def test_migration_0088_canonical_event_phase_log_mirror_reduced(db_pool: Any) -> None:
    """canonical_event_phase_log new_phase + previous_phase CHECKs reject 4 dropped values."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)

    try:
        for dropped_phase in ("suspended", "settling", "resolved", "voided"):
            # new_phase rejection
            with pytest.raises(psycopg2.errors.CheckViolation):
                with get_cursor(commit=True) as cur:
                    cur.execute(
                        """
                        INSERT INTO canonical_event_phase_log (
                            canonical_event_id, previous_phase, new_phase, changed_by
                        ) VALUES (%s, NULL, %s, 'system:test')
                        """,
                        (seeded_event_id, dropped_phase),
                    )
            # previous_phase rejection (when non-NULL)
            with pytest.raises(psycopg2.errors.CheckViolation):
                with get_cursor(commit=True) as cur:
                    cur.execute(
                        """
                        INSERT INTO canonical_event_phase_log (
                            canonical_event_id, previous_phase, new_phase, changed_by
                        ) VALUES (%s, %s, 'live', 'system:test')
                        """,
                        (seeded_event_id, dropped_phase),
                    )
    finally:
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I5: canonical_market_phase_log table shape (column-by-column parametrized)
# =============================================================================


@pytest.mark.parametrize(
    ("col_name", "data_type", "is_nullable", "default_substr", "max_char_len"),
    _MARKET_PHASE_LOG_COLS,
)
def test_migration_0088_canonical_market_phase_log_column_shape(
    db_pool: Any,
    col_name: str,
    data_type: str,
    is_nullable: str,
    default_substr: str | None,
    max_char_len: int | None,
) -> None:
    """Each column on canonical_market_phase_log has the migration-prescribed shape."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'canonical_market_phase_log'
              AND column_name = %s
              AND table_schema = 'public'
            """,
            (col_name,),
        )
        row = cur.fetchone()
    assert row is not None, (
        f"canonical_market_phase_log.{col_name} missing post-0088 -- expected per migration DDL"
    )
    assert row["data_type"] == data_type, (
        f"canonical_market_phase_log.{col_name} type mismatch: "
        f"expected {data_type!r}, got {row['data_type']!r}"
    )
    assert row["is_nullable"] == is_nullable, (
        f"canonical_market_phase_log.{col_name} nullability mismatch: "
        f"expected {is_nullable!r}, got {row['is_nullable']!r}"
    )
    if default_substr is not None:
        actual_default = row["column_default"] or ""
        assert default_substr.lower() in actual_default.lower(), (
            f"canonical_market_phase_log.{col_name} default missing {default_substr!r}; "
            f"got {actual_default!r}"
        )
    if max_char_len is not None:
        assert row["character_maximum_length"] == max_char_len, (
            f"canonical_market_phase_log.{col_name} max_length mismatch: "
            f"expected {max_char_len}, got {row['character_maximum_length']}"
        )


def test_migration_0088_canonical_market_phase_log_fk_polarity(db_pool: Any) -> None:
    """canonical_market_phase_log.canonical_market_id FK is ON DELETE CASCADE."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'canonical_market_phase_log'::regclass
              AND contype = 'f'
            """
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"canonical_market_phase_log must have exactly 1 FK; got {len(rows)}"
    constraint_def = rows[0]["def"]
    assert "REFERENCES canonical_markets" in constraint_def, (
        f"FK must reference canonical_markets; got {constraint_def!r}"
    )
    assert "ON DELETE CASCADE" in constraint_def, (
        f"FK must be ON DELETE CASCADE; got {constraint_def!r}"
    )


# =============================================================================
# I6: 3 indexes on canonical_market_phase_log (mirror slot 0079 indexing)
# =============================================================================


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_canonical_market_phase_log_transition_at",
        "idx_canonical_market_phase_log_canonical_market_id",
        "idx_canonical_market_phase_log_market_transition",
    ],
)
def test_migration_0088_canonical_market_phase_log_index_present(
    db_pool: Any, index_name: str
) -> None:
    """Each declared index exists post-0088."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'canonical_market_phase_log'
              AND indexname = %s
            """,
            (index_name,),
        )
        row = cur.fetchone()
    assert row is not None, f"Index {index_name!r} must exist post-Migration-0088"
    assert "canonical_market_phase_log" in row["indexdef"], (
        f"Index {index_name!r} indexdef must reference canonical_market_phase_log; "
        f"got: {row['indexdef']!r}"
    )


def test_migration_0088_market_composite_index_orders_transition_at_desc(db_pool: Any) -> None:
    """Composite index has DESC ordering on transition_at (mirror slot 0079)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE indexname = 'idx_canonical_market_phase_log_market_transition'
              AND schemaname = 'public'
            """
        )
        row = cur.fetchone()
    assert row is not None
    assert "transition_at DESC" in row["indexdef"], (
        f"Composite index must declare transition_at DESC; got: {row['indexdef']!r}"
    )


# =============================================================================
# I7: trigger function + trigger exist with correct shape
# =============================================================================


def test_migration_0088_log_market_phase_transition_function_exists(db_pool: Any) -> None:
    """``log_canonical_market_phase_transition()`` exists with mirror-of-0079 shape."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_functiondef(p.oid) AS def
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'log_canonical_market_phase_transition'
              AND n.nspname = 'public'
            """
        )
        row = cur.fetchone()
    assert row is not None, "log_canonical_market_phase_transition() must exist post-Migration-0088"
    function_def = row["def"]
    # IS DISTINCT FROM for NULL-safety (mirror slot 0079).
    assert "IS DISTINCT FROM" in function_def, (
        f"Function body must use IS DISTINCT FROM (NULL-safe); got: {function_def!r}"
    )
    # 'system:trigger' as changed_by (DECIDED_BY_PREFIXES system: family).
    assert "system:trigger" in function_def, (
        f"Function body must emit changed_by='system:trigger'; got: {function_def!r}"
    )


def test_migration_0088_market_phase_function_has_comment(db_pool: Any) -> None:
    """``COMMENT ON FUNCTION log_canonical_market_phase_transition()`` exists + correct.

    Glokta P2-3 fix-pass (session 98): Migration 0088 ships a COMMENT ON
    FUNCTION at lines 336-343 documenting the trigger contract for
    operators inspecting via ``\\df+ log_canonical_market_phase_transition``.
    Without this assertion, future drift (someone removing or rewriting
    the COMMENT) would not be caught.  Mirror discipline parallel to slot
    0079's COMMENT ON FUNCTION on log_canonical_event_phase_transition.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT obj_description(p.oid, 'pg_proc') AS comment
            FROM pg_proc p
            WHERE p.proname = 'log_canonical_market_phase_transition'
            """
        )
        row = cur.fetchone()
    assert row is not None, "log_canonical_market_phase_transition function must exist"
    comment = row["comment"]
    assert comment is not None, (
        "COMMENT ON FUNCTION log_canonical_market_phase_transition() must be set "
        "(Migration 0088 lines 336-343 ship it; mirror slot 0079 discipline)"
    )
    # The COMMENT documents auto-population semantics for INSERT + UPDATE paths.
    assert "auto-populates" in comment.lower() or "auto-populated" in comment.lower(), (
        f"COMMENT must document auto-population semantics; got: {comment!r}"
    )
    assert "canonical_market_phase_log" in comment, (
        f"COMMENT must reference canonical_market_phase_log target table; got: {comment!r}"
    )
    # 'system:trigger' attribution per DECIDED_BY_PREFIXES discipline.
    assert "system:trigger" in comment, (
        f"COMMENT must document changed_by='system:trigger' attribution; got: {comment!r}"
    )


def test_migration_0088_market_phase_trigger_exists(db_pool: Any) -> None:
    """``trg_canonical_markets_log_phase_transition`` exists; AFTER INSERT OR UPDATE OF lifecycle_phase."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_triggerdef(t.oid) AS def
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'canonical_markets'
              AND t.tgname = 'trg_canonical_markets_log_phase_transition'
              AND NOT t.tgisinternal
            """
        )
        row = cur.fetchone()
    assert row is not None, (
        "trg_canonical_markets_log_phase_transition must exist on canonical_markets"
    )
    trigger_def = row["def"]
    trigger_def_upper = trigger_def.upper()
    # AFTER (not BEFORE) per build spec § 0d D-2 mirror discipline.
    assert "AFTER" in trigger_def_upper, f"Trigger must fire AFTER; got: {trigger_def}"
    # INSERT firing scope per mirror.
    assert "INSERT" in trigger_def_upper, (
        f"Trigger must include INSERT firing scope; got: {trigger_def}"
    )
    # UPDATE OF lifecycle_phase (column-targeted firing) per mirror discipline.
    # Glokta P1-1 fix-pass (session 98): substring "LIFECYCLE_PHASE" alone is
    # vacuous because the function name also contains it (e.g., a wrong-shape
    # DDL like AFTER INSERT ON canonical_markets EXECUTE FUNCTION
    # log_canonical_market_phase_transition() would pass).  Tighten to
    # "OF LIFECYCLE_PHASE" which only appears in the column-restricted
    # UPDATE clause, matching slot 0079's verbatim mirror.
    assert "OF LIFECYCLE_PHASE" in trigger_def_upper, (
        f"Trigger must restrict UPDATE firing to OF lifecycle_phase "
        f"(load-bearing column-targeted firing per mirror discipline); "
        f"got: {trigger_def}"
    )
    # OR UPDATE construction confirms BOTH INSERT and column-restricted UPDATE
    # are wired (mirror slot 0079 line 425: AFTER INSERT OR UPDATE OF lifecycle_phase).
    assert "OR UPDATE" in trigger_def_upper, (
        f"Trigger must use AFTER INSERT OR UPDATE OF lifecycle_phase composite "
        f"(NOT separate trigger declarations); got: {trigger_def}"
    )


# =============================================================================
# I8a: behavioral -- INSERT path emits NULL->phase audit row
# =============================================================================


def test_migration_0088_market_phase_trigger_insert_path(db_pool: Any) -> None:
    """INSERT canonical_markets -> phase_log row auto-created (NULL -> 'open')."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    try:
        # Seed market with default lifecycle_phase='open' (DEFAULT).
        canonical_market_id = _seed_canonical_market(seeded_event_id, suffix)
        try:
            with get_cursor() as cur:
                cur.execute(
                    """
                    SELECT canonical_market_id, previous_phase, new_phase,
                           changed_by, note
                    FROM canonical_market_phase_log
                    WHERE canonical_market_id = %s
                    ORDER BY transition_at DESC, id DESC
                    """,
                    (canonical_market_id,),
                )
                rows = cur.fetchall()
            assert len(rows) == 1, (
                f"INSERT trigger must create exactly 1 phase_log row; got {len(rows)}"
            )
            row = rows[0]
            assert row["canonical_market_id"] == canonical_market_id
            assert row["previous_phase"] is None
            assert row["new_phase"] == "open"
            assert row["changed_by"] == "system:trigger"
            assert "INSERT" in (row["note"] or "")
        finally:
            _cleanup_canonical_market(canonical_market_id)
    finally:
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I8b: behavioral -- UPDATE OF lifecycle_phase emits old->new audit row
# =============================================================================


def test_migration_0088_market_phase_trigger_update_path(db_pool: Any) -> None:
    """UPDATE canonical_markets SET lifecycle_phase -> phase_log row added (old -> new)."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    try:
        canonical_market_id = _seed_canonical_market(seeded_event_id, suffix)
        try:
            # Seed creates 1 INSERT-path row (NULL -> 'open').
            # Now UPDATE lifecycle_phase 'open' -> 'suspended' (1 more row).
            with get_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE canonical_markets SET lifecycle_phase = 'suspended' WHERE id = %s",
                    (canonical_market_id,),
                )

            with get_cursor() as cur:
                cur.execute(
                    """
                    SELECT previous_phase, new_phase, changed_by, note
                    FROM canonical_market_phase_log
                    WHERE canonical_market_id = %s
                    ORDER BY transition_at ASC, id ASC
                    """,
                    (canonical_market_id,),
                )
                rows = cur.fetchall()
            assert len(rows) == 2, f"INSERT + UPDATE must produce 2 phase_log rows; got {len(rows)}"
            # First row from INSERT.
            assert rows[0]["previous_phase"] is None
            assert rows[0]["new_phase"] == "open"
            assert rows[0]["changed_by"] == "system:trigger"
            # Second row from UPDATE.
            assert rows[1]["previous_phase"] == "open"
            assert rows[1]["new_phase"] == "suspended"
            assert rows[1]["changed_by"] == "system:trigger"
            assert "UPDATE" in (rows[1]["note"] or "")
        finally:
            _cleanup_canonical_market(canonical_market_id)
    finally:
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I9: UPDATE same value -> NO log row (IS DISTINCT FROM correctness)
# =============================================================================


def test_migration_0088_market_phase_trigger_skips_no_change_update(db_pool: Any) -> None:
    """UPDATE canonical_markets with same lifecycle_phase value -> NO new log row."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    try:
        canonical_market_id = _seed_canonical_market(seeded_event_id, suffix)
        try:
            # After INSERT: 1 row.
            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                    "WHERE canonical_market_id = %s",
                    (canonical_market_id,),
                )
                before_count = cur.fetchone()["n"]
            assert before_count == 1

            # UPDATE to same value 'open' -> 'open'.
            with get_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE canonical_markets SET lifecycle_phase = 'open' WHERE id = %s",
                    (canonical_market_id,),
                )

            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                    "WHERE canonical_market_id = %s",
                    (canonical_market_id,),
                )
                after_count = cur.fetchone()["n"]
            assert after_count == 1, (
                f"UPDATE with same lifecycle_phase value must NOT create a new "
                f"phase_log row; before={before_count}, after={after_count}"
            )
        finally:
            _cleanup_canonical_market(canonical_market_id)
    finally:
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I9b: UPDATE non-lifecycle column -> NO log row
# =============================================================================


def test_migration_0088_market_phase_trigger_skips_non_lifecycle_update(db_pool: Any) -> None:
    """UPDATE on a non-lifecycle_phase column must NOT create a phase_log row."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    try:
        canonical_market_id = _seed_canonical_market(seeded_event_id, suffix)
        try:
            # After INSERT: 1 row.
            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                    "WHERE canonical_market_id = %s",
                    (canonical_market_id,),
                )
                before_count = cur.fetchone()["n"]
            assert before_count == 1

            # UPDATE outcome_label (non-lifecycle column).
            with get_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE canonical_markets SET outcome_label = %s WHERE id = %s",
                    (f"Updated outcome ({suffix})", canonical_market_id),
                )

            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                    "WHERE canonical_market_id = %s",
                    (canonical_market_id,),
                )
                after_count = cur.fetchone()["n"]
            assert after_count == 1, (
                f"UPDATE on non-lifecycle_phase column must NOT create a new "
                f"phase_log row; before={before_count}, after={after_count}"
            )
        finally:
            _cleanup_canonical_market(canonical_market_id)
    finally:
        _cleanup_canonical_event(seeded_event_id)


# =============================================================================
# I10: cascade delete on canonical_markets removes phase_log rows
# =============================================================================


def test_migration_0088_cascade_delete_removes_phase_log_rows(db_pool: Any) -> None:
    """DELETE FROM canonical_markets cascades to canonical_market_phase_log."""
    suffix = uuid.uuid4().hex[:8]
    seeded_event_id = _seed_canonical_event(suffix)
    try:
        canonical_market_id = _seed_canonical_market(seeded_event_id, suffix)

        # Verify phase_log row exists post-INSERT (auto-trigger).
        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                "WHERE canonical_market_id = %s",
                (canonical_market_id,),
            )
            count_before = cur.fetchone()["n"]
        assert count_before == 1

        # DELETE the canonical_markets row.
        with get_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM canonical_markets WHERE id = %s",
                (canonical_market_id,),
            )

        # Verify phase_log rows were CASCADE-deleted.
        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM canonical_market_phase_log "
                "WHERE canonical_market_id = %s",
                (canonical_market_id,),
            )
            count_after = cur.fetchone()["n"]
        assert count_after == 0, (
            f"ON DELETE CASCADE must remove phase_log rows; "
            f"before delete: {count_before}, after delete: {count_after}"
        )
    finally:
        _cleanup_canonical_event(seeded_event_id)
