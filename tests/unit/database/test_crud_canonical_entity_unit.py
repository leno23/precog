"""Unit tests for crud_canonical_entity module -- Pattern 14 step 4 of the Cohort 1B Slice B retro.

Covers (function-by-function):
    - create_canonical_entity: happy path, JSONB metadata serialization,
      None metadata -> NULL, Decimal preservation through metadata,
      RETURNING projection fidelity (all 6 post-Slot-2 columns).
    - get_canonical_entity_by_id: happy path (row found), None (not found),
      SELECT projection fidelity.
    - get_canonical_entity_by_kind_and_key: happy path (row found), None
      (not found), composite-key params shape, SELECT projection fidelity.
    - get_canonical_entity_kind_id_by_kind: happy path (kind found),
      None (kind not seeded), case-sensitivity passthrough.

Pattern 43 (mock fidelity) discipline: mocks return the EXACT shape that the
real query returns -- full row dicts with all canonical_entities columns, no
extra keys, no missing keys.  Mocks of ``get_cursor`` use the
``__enter__`` / ``__exit__`` protocol consistent with
``test_crud_canonical_markets_unit.py``.

Cleanup epic Slot 2 (Migration 0086) flipped the FK direction between
``teams`` and ``canonical_entities``.  Pre-Slot-2 ``ref_team_id`` was the
6th column (typed back-ref); post-Slot-2 ``canonical_entities`` carries 6
columns total: id + entity_kind_id + entity_key + display_name + metadata
+ created_at.  ``create_canonical_entity()`` no longer accepts a
``ref_team_id`` parameter; the trigger-raise propagation test that pinned
Pattern 82 V2 forward-only behavior is retired (the polymorphic enforcement
trigger ``trg_canonical_entity_team_backref`` is dropped at Slot 2).  The
Pattern 82 V2 source-grep compliance test is also retired (the rule
retires for canonical_entities; Pattern 82 V2 SCOPE NARROWING in V2.47
applies the rule to canonical_markets only -- formal retirement codified
at V2.47 ADR amendment / Slot 5 / session 99).

Reference:
    - ``src/precog/database/crud_canonical_entity.py``
    - ``src/precog/database/alembic/versions/0068_canonical_entity_foundation.py``
    - ``src/precog/database/alembic/versions/0086_canonical_fk_direction_flip.py``
    - ``tests/unit/database/test_crud_canonical_markets_unit.py`` (style reference)
    - ADR-118 V2.40 Item 4 (Pattern 82 V2 ratification with inline forward-
      pointer to V2.47 retirement at Slot 5 / session 99)
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from precog.database.crud_canonical_entity import (
    create_canonical_entity,
    get_canonical_entity_by_id,
    get_canonical_entity_by_kind_and_key,
    get_canonical_entity_kind_id_by_kind,
)


def _full_row_dict(
    *,
    id: int = 7,
    entity_kind_id: int = 1,
    entity_key: str = "BUF-NFL-001",
    display_name: str = "Buffalo Bills",
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Build a full canonical_entities row dict matching the real query shape.

    Pattern 43 fidelity: every key the real RETURNING / SELECT projection
    emits is present, with no extras.  This is the SSOT for "what does a
    canonical_entities row dict look like in tests".  Mirrors the
    ``_full_row_dict`` helper in ``test_crud_canonical_markets_unit.py``.

    Cleanup epic Slot 2 (Migration 0086) shape: 6 columns total.  The
    pre-Slot-2 ``ref_team_id`` column is dropped.
    """
    if created_at is None:
        created_at = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    return {
        "id": id,
        "entity_kind_id": entity_kind_id,
        "entity_key": entity_key,
        "display_name": display_name,
        "metadata": metadata,
        "created_at": created_at,
    }


# =============================================================================
# create_canonical_entity
# =============================================================================


@pytest.mark.unit
class TestCreateCanonicalEntity:
    """Unit tests for create_canonical_entity -- INSERT + RETURNING happy path."""

    @patch("precog.database.crud_canonical_entity.get_cursor")
    def test_returns_full_row_dict_on_success(self, mock_get_cursor):
        """Returns the full row dict from RETURNING projection."""
        expected_row = _full_row_dict(
            id=7,
            entity_kind_id=1,
            entity_key="BUF-NFL-001",
            display_name="Buffalo Bills",
            metadata=None,
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_canonical_entity(
            entity_kind_id=1,
            entity_key="BUF-NFL-001",
            display_name="Buffalo Bills",
        )

        assert result == expected_row
        # commit=True must be used because this is a write
        mock_get_cursor.assert_called_once_with(commit=True)
        mock_cursor.execute.assert_called_once()
        # Verify INSERT + RETURNING query shape.  Migration 0085 renamed
        # canonical_entity -> canonical_entities; Migration 0086 dropped
        # ref_team_id from the column inventory.  Trailing space + open-
        # paren guards against accidental match on canonical_entity_kinds.
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO canonical_entities (" in sql
        assert "RETURNING" in sql
        assert "entity_kind_id" in sql
        assert "entity_key" in sql
        # Post-Slot-2: ref_team_id MUST NOT appear in the INSERT statement
        assert "ref_team_id" not in sql, (
            "ref_team_id retired by Migration 0086 (cleanup epic Slot 2 / "
            "FK direction flip); SQL must not reference the dropped column"
        )
        # Params order matches column order in INSERT statement
        assert params[0] == 1  # entity_kind_id
        assert params[1] == "BUF-NFL-001"  # entity_key
        assert params[2] == "Buffalo Bills"  # display_name
        assert params[3] is None  # metadata None -> NULL

    @patch("precog.database.crud_canonical_entity.get_cursor")
    def test_metadata_dict_serialized_as_json(self, mock_get_cursor):
        """metadata dict is JSON-serialized (matching crud_canonical_markets convention)."""
        meta = {"source": "espn", "import_run_id": 99}
        expected_row = _full_row_dict(metadata=meta)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_entity(
            entity_kind_id=2,
            entity_key="MCGREGOR-CONOR",
            display_name="Conor McGregor",
            metadata=meta,
        )

        params = mock_cursor.execute.call_args[0][1]
        # metadata is the 4th param (index 3) post-Slot-2 (was index 4 with ref_team_id)
        json_param = params[3]
        assert isinstance(json_param, str)
        assert json.loads(json_param) == meta

    @patch("precog.database.crud_canonical_entity.get_cursor")
    def test_metadata_none_passed_as_null(self, mock_get_cursor):
        """metadata=None is passed as NULL (not the string 'null')."""
        expected_row = _full_row_dict(metadata=None)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_entity(
            entity_kind_id=1,
            entity_key="MIA-NFL-002",
            display_name="Miami Dolphins",
            metadata=None,
        )

        params = mock_cursor.execute.call_args[0][1]
        assert params[3] is None  # NULL, not "null"

    @patch("precog.database.crud_canonical_entity.get_cursor")
    def test_decimal_in_metadata_preserved_via_json(self, mock_get_cursor):
        """Decimal values in metadata round-trip via JSON without precision loss.

        Educational Note:
            ``json.dumps`` cannot serialize Decimal directly, so callers must
            stringify Decimals before passing them in metadata.  This test
            pins the contract: stringified Decimals are preserved verbatim
            through the JSON layer.  Mirrors the
            ``test_crud_canonical_markets_unit.test_decimal_in_metadata_preserved_via_json``
            pattern.
        """
        meta = {"settle_threshold": str(Decimal("0.5000"))}
        expected_row = _full_row_dict(metadata=meta)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_entity(
            entity_kind_id=1,
            entity_key="BUF-NFL-001",
            display_name="Buffalo Bills",
            metadata=meta,
        )

        params = mock_cursor.execute.call_args[0][1]
        deserialized = json.loads(params[3])
        assert deserialized["settle_threshold"] == "0.5000"

    @patch("precog.database.crud_canonical_entity.get_cursor")
    def test_returning_projects_all_canonical_entities_columns(self, mock_get_cursor):
        """Pattern 43 fidelity: the INSERT...RETURNING projection must include all
        6 post-Slot-2 canonical_entities columns. Mirrors Glokta Finding 7 + Ripley
        Finding 5 from Cohort 2 (test_crud_canonical_markets_unit.py) -- without
        this test, a future refactor that drops a column from the RETURNING clause
        would silently pass because the mock dict (built by _full_row_dict()) has
        all keys regardless.

        Cleanup epic Slot 2 (Migration 0086) dropped ``ref_team_id`` from the
        column inventory; the RETURNING projection now lists 6 columns instead
        of the pre-Slot-2 7.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = _full_row_dict()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_entity(
            entity_kind_id=1,
            entity_key="BUF-NFL-001",
            display_name="Buffalo Bills",
        )

        sql = mock_cursor.execute.call_args[0][0]
        # Every post-Slot-2 canonical_entities column must appear in the
        # RETURNING projection (6 columns).
        for col in (
            "id",
            "entity_kind_id",
            "entity_key",
            "display_name",
            "metadata",
            "created_at",
        ):
            assert col in sql, f"Column {col!r} missing from INSERT...RETURNING projection"
        # Post-Slot-2: ref_team_id MUST NOT appear in the projection
        assert "ref_team_id" not in sql, (
            "ref_team_id retired by Migration 0086 (cleanup epic Slot 2); "
            "RETURNING projection must not reference the dropped column"
        )


# =============================================================================
# get_canonical_entity_by_id
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEntityById:
    """Unit tests for get_canonical_entity_by_id -- SELECT by surrogate PK."""

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_row_dict_when_found(self, mock_fetch_one):
        """Returns the full row dict when a row matches the id."""
        expected_row = _full_row_dict(id=7)
        mock_fetch_one.return_value = expected_row

        result = get_canonical_entity_by_id(7)

        assert result == expected_row
        mock_fetch_one.assert_called_once()
        sql, params = mock_fetch_one.call_args[0]
        # Migration 0085 renamed canonical_entity -> canonical_entities;
        # SELECT projects from the new name.  Trailing space guards
        # against accidental match on canonical_entity_kinds.
        assert "FROM canonical_entities\n" in sql or "FROM canonical_entities " in sql
        assert "WHERE id = %s" in sql
        assert params == (7,)

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_none_when_not_found(self, mock_fetch_one):
        """Returns None when no row matches the id."""
        mock_fetch_one.return_value = None

        result = get_canonical_entity_by_id(99999)

        assert result is None
        # Verify the call happened with the expected id parameter
        # (SQL substring check is in test_query_selects_all_canonical_entities_columns)
        assert mock_fetch_one.call_count == 1
        assert mock_fetch_one.call_args[0][1] == (99999,)

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_query_selects_all_canonical_entities_columns(self, mock_fetch_one):
        """Verify the SELECT projection includes all post-Slot-2 canonical_entities columns.

        Pattern 43 fidelity: the projection must match the columns that
        callers downstream expect to see in the returned dict.  Cleanup epic
        Slot 2 (Migration 0086) dropped ref_team_id from the column inventory.
        """
        mock_fetch_one.return_value = None

        get_canonical_entity_by_id(7)

        sql = mock_fetch_one.call_args[0][0]
        for col in (
            "id",
            "entity_kind_id",
            "entity_key",
            "display_name",
            "metadata",
            "created_at",
        ):
            assert col in sql, f"Column {col!r} missing from SELECT projection"
        # Post-Slot-2: ref_team_id MUST NOT appear in the projection
        assert "ref_team_id" not in sql, (
            "ref_team_id retired by Migration 0086; SELECT projection must "
            "not reference the dropped column"
        )


# =============================================================================
# get_canonical_entity_by_kind_and_key
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEntityByKindAndKey:
    """Unit tests for get_canonical_entity_by_kind_and_key -- SELECT by composite NK."""

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_row_dict_when_found(self, mock_fetch_one):
        """Returns the full row dict when a (kind_id, key) pair matches."""
        expected_row = _full_row_dict(id=7, entity_kind_id=1, entity_key="BUF-NFL-001")
        mock_fetch_one.return_value = expected_row

        result = get_canonical_entity_by_kind_and_key(1, "BUF-NFL-001")

        assert result == expected_row
        sql, params = mock_fetch_one.call_args[0]
        # Migration 0085 renamed canonical_entity -> canonical_entities.
        # Trailing space / newline guards against accidental match on
        # canonical_entity_kinds.
        assert "FROM canonical_entities\n" in sql or "FROM canonical_entities " in sql
        assert "WHERE entity_kind_id = %s AND entity_key = %s" in sql
        assert params == (1, "BUF-NFL-001")

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_none_when_not_found(self, mock_fetch_one):
        """Returns None when no row matches (caller should create new).

        This is the "new canonical identity" signal -- a None return means
        the caller should create a new canonical_entities row.
        """
        mock_fetch_one.return_value = None

        result = get_canonical_entity_by_kind_and_key(1, "NEVER-SEEN-BEFORE")

        assert result is None

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_query_selects_all_canonical_entities_columns(self, mock_fetch_one):
        """Pattern 43 fidelity: the (kind, key) lookup's SELECT projection must
        include all 6 post-Slot-2 canonical_entities columns. Mirrors the
        natural-key-hash projection-fidelity test in
        test_crud_canonical_markets_unit.py -- without this test, a future
        refactor that drops a column from the composite-key SELECT would
        silently pass because the mock dict (built by _full_row_dict()) has
        all keys regardless.
        """
        mock_fetch_one.return_value = None

        get_canonical_entity_by_kind_and_key(1, "BUF-NFL-001")

        sql = mock_fetch_one.call_args[0][0]
        for col in (
            "id",
            "entity_kind_id",
            "entity_key",
            "display_name",
            "metadata",
            "created_at",
        ):
            assert col in sql, f"Column {col!r} missing from kind-and-key SELECT projection"
        # Post-Slot-2: ref_team_id MUST NOT appear in the projection
        assert "ref_team_id" not in sql, (
            "ref_team_id retired by Migration 0086; kind-and-key SELECT "
            "projection must not reference the dropped column"
        )

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_empty_string_key_passes_through(self, mock_fetch_one):
        """Empty entity_key string is passed through unchanged.

        Edge case: in production callers should never pass empty entity_key
        (the column is NOT NULL but empty TEXT is technically valid storage),
        but this CRUD function does not validate input -- it forwards the
        text to the lookup.  Pinning the contract here.
        """
        mock_fetch_one.return_value = None

        result = get_canonical_entity_by_kind_and_key(1, "")

        assert result is None
        params = mock_fetch_one.call_args[0][1]
        assert params == (1, "")


# =============================================================================
# get_canonical_entity_kind_id_by_kind
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEntityKindIdByKind:
    """Unit tests for get_canonical_entity_kind_id_by_kind -- text -> id resolver."""

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_id_when_kind_seeded(self, mock_fetch_one):
        """Returns the integer id for a seeded entity_kind."""
        mock_fetch_one.return_value = {"id": 1}

        result = get_canonical_entity_kind_id_by_kind("team")

        assert result == 1
        sql, params = mock_fetch_one.call_args[0]
        assert "FROM canonical_entity_kinds" in sql
        assert "WHERE entity_kind = %s" in sql
        assert params == ("team",)

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_returns_none_when_kind_not_seeded(self, mock_fetch_one):
        """Returns None when no canonical_entity_kinds row matches the given text."""
        mock_fetch_one.return_value = None

        result = get_canonical_entity_kind_id_by_kind("unicorn")

        assert result is None

    @patch("precog.database.crud_canonical_entity.fetch_one")
    def test_case_sensitive_passthrough(self, mock_fetch_one):
        """Kind text is passed through unchanged (case-sensitive at the DB layer).

        The seed values are lowercase ('team', 'fighter', ...).  Callers
        passing 'Team' or 'TEAM' will hit a None result; this CRUD function
        does NOT lowercase or normalize input.  Pinning the contract here.
        """
        mock_fetch_one.return_value = None

        result = get_canonical_entity_kind_id_by_kind("TEAM")

        assert result is None
        params = mock_fetch_one.call_args[0][1]
        assert params == ("TEAM",)  # passed through verbatim
