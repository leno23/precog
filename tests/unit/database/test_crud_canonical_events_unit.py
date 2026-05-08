"""Unit tests for crud_canonical_events module -- Pattern 14 step 4 of the
Cohort 1A Slice C retro.

Covers (function-by-function):
    - create_canonical_event: happy path, JSONB metadata serialization,
      None metadata -> NULL, Decimal preservation through metadata, BYTEA
      natural_key_hash passthrough, lifecycle_phase default.
    - get_canonical_event_by_id: happy path (row found), None (not found),
      column projection fidelity.
    - get_canonical_event_by_natural_key_hash: happy path (row found),
      None (not found), BYTEA params shape, column projection fidelity.
    - get_active_canonical_event (Migration 0087, cleanup epic Slot 3):
      walks superseded_by chain forward; returns terminal-active row OR
      None (per Q1 adjudication: tombstones return None, not the
      tombstone row); 100-hop guard raises RuntimeError on cycle.
    - retire_canonical_event:
      backward-compat path (no superseded_by_id): True (row updated),
      False (no row), retired_at-only SET clause discipline.
      extension path (superseded_by_id provided -- Migration 0087):
      writes BOTH columns atomically in a single UPDATE; rejects cycle
      introduction with ValueError; allows chain extension forward.
    - get_canonical_event_domain_id_by_domain: happy path, None (not seeded),
      case-sensitivity passthrough.
    - get_canonical_event_type_id_by_domain_and_type: happy path, None (not
      seeded), composite-key params shape.

Pattern 43 (mock fidelity) discipline: mocks return the EXACT shape that the
real query returns -- full row dicts with all canonical_events columns, no
extra keys, no missing keys.  Mocks of ``get_cursor`` use the
``__enter__`` / ``__exit__`` protocol consistent with
``test_crud_canonical_markets_unit.py`` and
``test_crud_canonical_entity_unit.py``.

Reference:
    - ``src/precog/database/crud_canonical_events.py``
    - ``src/precog/database/alembic/versions/0067_canonical_events_foundation.py``
    - ``tests/unit/database/test_crud_canonical_markets_unit.py`` (style reference)
    - ``tests/unit/database/test_crud_canonical_entity_unit.py`` (Slice B sibling)
    - ADR-118 V2.38+ (Cohort 1A Pattern 14 retro)
"""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from precog.database.crud_canonical_events import (
    create_canonical_event,
    get_active_canonical_event,
    get_canonical_event_by_id,
    get_canonical_event_by_natural_key_hash,
    get_canonical_event_domain_id_by_domain,
    get_canonical_event_type_id_by_domain_and_type,
    retire_canonical_event,
)


def _sample_natural_key_hash(suffix: bytes = b"sample") -> bytes:
    """Return a 32-byte sha256 digest for use as a natural_key_hash test value."""
    return hashlib.sha256(b"canonical|event|nk|" + suffix).digest()


def _full_row_dict(
    *,
    id: int = 7,
    event_domain_id: int = 1,
    event_type_id: int = 1,
    participants_sorted: list[int] | None = None,
    resolution_window: str = "[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
    resolution_rule_fp: bytes | None = None,
    natural_key_hash: bytes | None = None,
    title: str = "Buffalo Bills @ Miami Dolphins, Week 1",
    description: str | None = None,
    lifecycle_phase: str = "proposed",
    metadata: dict | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    retired_at: datetime | None = None,
    superseded_by: int | None = None,
) -> dict:
    """Build a full canonical_events row dict matching the real query shape.

    Pattern 43 fidelity: every key the real RETURNING / SELECT projection
    emits is present, with no extras.  This is the SSOT for "what does a
    canonical_events row dict look like in tests".  Mirrors the
    ``_full_row_dict`` helper in ``test_crud_canonical_markets_unit.py``.

    Migration 0086 (cleanup epic Slot 2 / session 96) DROPped game_id +
    series_id from the column inventory; Migration 0087 (cleanup epic
    Slot 3 / session 97) ADDed superseded_by.  Post-Slot-3 the row dict
    carries 15 keys (was 14 post-Slot-2, was 16 pre-Slot-2).
    """
    if participants_sorted is None:
        participants_sorted = [1, 2]
    if natural_key_hash is None:
        natural_key_hash = _sample_natural_key_hash()
    if created_at is None:
        created_at = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
    if updated_at is None:
        updated_at = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
    return {
        "id": id,
        "event_domain_id": event_domain_id,
        "event_type_id": event_type_id,
        "participants_sorted": participants_sorted,
        "resolution_window": resolution_window,
        "resolution_rule_fp": resolution_rule_fp,
        "natural_key_hash": natural_key_hash,
        "title": title,
        "description": description,
        "lifecycle_phase": lifecycle_phase,
        "metadata": metadata,
        "created_at": created_at,
        "updated_at": updated_at,
        "retired_at": retired_at,
        "superseded_by": superseded_by,
    }


# Migration 0085 (cleanup epic Slot 1) renamed canonical_events.domain_id
# -> event_domain_id and canonical_events.entities_sorted ->
# participants_sorted.  Migration 0086 (cleanup epic Slot 2) DROPped
# game_id + series_id (CL-2 denorm collapse).  Migration 0087 (cleanup
# epic Slot 3) ADDed superseded_by (self-FK + retirement-cascade).
# Tuple values mirror the post-Slot-3 column names in the SELECT /
# RETURNING projections.
_ALL_CANONICAL_EVENTS_COLUMNS = (
    "id",
    "event_domain_id",
    "event_type_id",
    "participants_sorted",
    "resolution_window",
    "resolution_rule_fp",
    "natural_key_hash",
    "title",
    "description",
    "lifecycle_phase",
    "metadata",
    "created_at",
    "updated_at",
    "retired_at",
    "superseded_by",
)


# =============================================================================
# create_canonical_event
# =============================================================================


@pytest.mark.unit
class TestCreateCanonicalEvent:
    """Unit tests for create_canonical_event -- INSERT + RETURNING happy path."""

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_returns_full_row_dict_on_success(self, mock_get_cursor):
        """Returns the full row dict from RETURNING projection."""
        nk = _sample_natural_key_hash()
        expected_row = _full_row_dict(
            id=7,
            event_domain_id=1,
            event_type_id=1,
            natural_key_hash=nk,
            metadata=None,
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Buffalo Bills @ Miami Dolphins, Week 1",
        )

        assert result == expected_row
        # commit=True must be used because this is a write
        mock_get_cursor.assert_called_once_with(commit=True)
        mock_cursor.execute.assert_called_once()
        # Verify INSERT + RETURNING query shape.  Migration 0085 (cleanup
        # epic Slot 1) renamed canonical_events.domain_id -> event_domain_id
        # and canonical_events.entities_sorted -> participants_sorted.
        # Migration 0086 (cleanup epic Slot 2) DROPped game_id + series_id
        # from the column inventory.
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO canonical_events" in sql
        assert "RETURNING" in sql
        assert "event_domain_id" in sql
        assert "event_type_id" in sql
        assert "participants_sorted" in sql
        # Post-Slot-2: game_id + series_id MUST NOT appear in the INSERT
        assert "game_id" not in sql, (
            "game_id retired by Migration 0086 (cleanup epic Slot 2 / "
            "denorm collapse); SQL must not reference the dropped column"
        )
        assert "series_id" not in sql, (
            "series_id retired by Migration 0086; SQL must not reference the dropped column"
        )
        # Params order matches column order in INSERT statement
        assert params[0] == 1  # event_domain_id
        assert params[1] == 1  # event_type_id
        assert params[2] == [1, 2]  # participants_sorted
        assert params[3] == "[2026-09-04 17:00+00, 2026-09-04 21:00+00]"  # resolution_window
        assert params[4] is None  # resolution_rule_fp
        assert params[5] == nk  # natural_key_hash (bytes passthrough)
        assert params[6] == "Buffalo Bills @ Miami Dolphins, Week 1"  # title
        assert params[7] is None  # description
        assert params[8] == "proposed"  # lifecycle_phase default
        assert params[9] is None  # metadata None -> NULL

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_metadata_dict_serialized_as_json(self, mock_get_cursor):
        """metadata dict is JSON-serialized (matching crud_canonical_markets convention)."""
        nk = _sample_natural_key_hash()
        meta = {"source": "espn", "import_run_id": 99}
        expected_row = _full_row_dict(metadata=meta)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
            metadata=meta,
        )

        params = mock_cursor.execute.call_args[0][1]
        # metadata is the 10th param (index 9) post-Slot-2 (was index 11
        # pre-Slot-2 with game_id + series_id occupying indices 8-9).
        json_param = params[9]
        assert isinstance(json_param, str)
        assert json.loads(json_param) == meta

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_metadata_none_passed_as_null(self, mock_get_cursor):
        """metadata=None is passed as NULL (not the string 'null')."""
        nk = _sample_natural_key_hash()
        expected_row = _full_row_dict(metadata=None)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
            metadata=None,
        )

        params = mock_cursor.execute.call_args[0][1]
        # metadata is index 9 post-Slot-2 (game_id + series_id retired)
        assert params[9] is None  # NULL, not "null"

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_lifecycle_phase_default_is_proposed(self, mock_get_cursor):
        """lifecycle_phase defaults to 'proposed' when not passed."""
        nk = _sample_natural_key_hash()
        expected_row = _full_row_dict(lifecycle_phase="proposed")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
        )

        params = mock_cursor.execute.call_args[0][1]
        # lifecycle_phase is the 9th param (index 8) post-Slot-2 (was
        # index 10 pre-Slot-2 with game_id + series_id at 8-9).
        assert params[8] == "proposed"

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_lifecycle_phase_explicit_override(self, mock_get_cursor):
        """lifecycle_phase can be overridden by caller."""
        nk = _sample_natural_key_hash()
        expected_row = _full_row_dict(lifecycle_phase="matched")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
            lifecycle_phase="matched",
        )

        params = mock_cursor.execute.call_args[0][1]
        # lifecycle_phase is index 8 post-Slot-2
        assert params[8] == "matched"

    @patch("precog.database.crud_canonical_events.get_cursor")
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
        nk = _sample_natural_key_hash()
        meta = {"settle_threshold": str(Decimal("0.5000"))}
        expected_row = _full_row_dict(metadata=meta)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
            metadata=meta,
        )

        params = mock_cursor.execute.call_args[0][1]
        # metadata is index 9 post-Slot-2
        deserialized = json.loads(params[9])
        assert deserialized["settle_threshold"] == "0.5000"

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_natural_key_hash_bytes_passthrough(self, mock_get_cursor):
        """natural_key_hash is passed as raw bytes (BYTEA -- no encoding)."""
        nk = _sample_natural_key_hash(b"unique-suffix")
        assert isinstance(nk, bytes)
        assert len(nk) == 32  # sha256 digest
        expected_row = _full_row_dict(natural_key_hash=nk)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = expected_row
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=nk,
            title="Test Event",
        )

        params = mock_cursor.execute.call_args[0][1]
        assert params[5] == nk
        assert isinstance(params[5], bytes)

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_returning_projects_all_canonical_events_columns(self, mock_get_cursor):
        """Pattern 43 fidelity: the INSERT...RETURNING projection must include all
        16 canonical_events columns. Mirrors Glokta Finding 7 + Ripley Finding 5
        from Cohort 2 (test_crud_canonical_markets_unit.py) -- without this test, a
        future refactor that drops a column from the RETURNING clause would silently
        pass because the mock dict (built by _full_row_dict()) has all keys regardless.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = _full_row_dict()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_canonical_event(
            event_domain_id=1,
            event_type_id=1,
            participants_sorted=[1, 2],
            resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
            natural_key_hash=_sample_natural_key_hash(),
            title="Test Event",
        )

        sql = mock_cursor.execute.call_args[0][0]
        # Every canonical_events column must appear in the RETURNING projection
        for col in _ALL_CANONICAL_EVENTS_COLUMNS:
            assert col in sql, f"Column {col!r} missing from INSERT...RETURNING projection"


# =============================================================================
# get_canonical_event_by_id
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEventById:
    """Unit tests for get_canonical_event_by_id -- SELECT by surrogate PK."""

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_row_dict_when_found(self, mock_fetch_one):
        """Returns the full row dict when a row matches the id."""
        expected_row = _full_row_dict(id=7)
        mock_fetch_one.return_value = expected_row

        result = get_canonical_event_by_id(7)

        assert result == expected_row
        mock_fetch_one.assert_called_once()
        sql, params = mock_fetch_one.call_args[0]
        assert "FROM canonical_events" in sql
        assert "WHERE id = %s" in sql
        assert params == (7,)

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_none_when_not_found(self, mock_fetch_one):
        """Returns None when no row matches the id."""
        mock_fetch_one.return_value = None

        result = get_canonical_event_by_id(99999)

        assert result is None
        assert mock_fetch_one.call_count == 1
        assert mock_fetch_one.call_args[0][1] == (99999,)

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_query_selects_all_canonical_events_columns(self, mock_fetch_one):
        """Pattern 43 fidelity: SELECT projection must include all 16 columns."""
        mock_fetch_one.return_value = None

        get_canonical_event_by_id(7)

        sql = mock_fetch_one.call_args[0][0]
        for col in _ALL_CANONICAL_EVENTS_COLUMNS:
            assert col in sql, f"Column {col!r} missing from SELECT projection"


# =============================================================================
# get_canonical_event_by_natural_key_hash
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEventByNaturalKeyHash:
    """Unit tests for get_canonical_event_by_natural_key_hash -- SELECT by NK hash."""

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_row_dict_when_found(self, mock_fetch_one):
        """Returns the full row dict when an NK match is found."""
        nk = _sample_natural_key_hash()
        expected_row = _full_row_dict(natural_key_hash=nk)
        mock_fetch_one.return_value = expected_row

        result = get_canonical_event_by_natural_key_hash(nk)

        assert result == expected_row
        sql, params = mock_fetch_one.call_args[0]
        assert "FROM canonical_events" in sql
        assert "WHERE natural_key_hash = %s" in sql
        assert params == (nk,)
        assert isinstance(params[0], bytes)

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_none_when_not_found(self, mock_fetch_one):
        """Returns None when no canonical event has the given NK hash.

        This is the matcher's "new canonical identity" signal -- a None return
        means the caller should create a new canonical_events row.
        """
        mock_fetch_one.return_value = None
        nk = _sample_natural_key_hash(b"never-seen-before")

        result = get_canonical_event_by_natural_key_hash(nk)

        assert result is None

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_empty_bytes_passes_through(self, mock_fetch_one):
        """Empty bytes (b'') is passed through unchanged."""
        mock_fetch_one.return_value = None

        result = get_canonical_event_by_natural_key_hash(b"")

        assert result is None
        params = mock_fetch_one.call_args[0][1]
        assert params == (b"",)

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_query_selects_all_canonical_events_columns(self, mock_fetch_one):
        """Pattern 43 fidelity: NK-hash SELECT projection must include all 16 columns."""
        mock_fetch_one.return_value = None
        get_canonical_event_by_natural_key_hash(b"\xaa\xbb\xcc\xdd")

        sql = mock_fetch_one.call_args[0][0]
        for col in _ALL_CANONICAL_EVENTS_COLUMNS:
            assert col in sql, f"Column {col!r} missing from natural-key-hash SELECT projection"


# =============================================================================
# retire_canonical_event
# =============================================================================


@pytest.mark.unit
class TestRetireCanonicalEvent:
    """Unit tests for retire_canonical_event -- UPDATE retired_at = now()."""

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_returns_true_when_row_updated(self, mock_get_cursor):
        """Returns True when a row was matched and updated (rowcount=1)."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(7)

        assert result is True
        mock_get_cursor.assert_called_once_with(commit=True)
        sql, params = mock_cursor.execute.call_args[0]
        assert "UPDATE canonical_events" in sql
        assert "retired_at = now()" in sql
        assert "WHERE id = %s" in sql
        assert params == (7,)

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_returns_false_when_no_row_matched(self, mock_get_cursor):
        """Returns False when no row matched the given id (rowcount=0)."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(99999)

        assert result is False

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_does_not_write_updated_at_directly(self, mock_get_cursor):
        """retire_canonical_event does NOT explicitly write updated_at.

        The BEFORE UPDATE trigger retrofit (#1007) will refresh ``updated_at``
        automatically once installed.  Until then, ``updated_at`` remains a
        static creation timestamp; either way, this function MUST NOT write
        it directly (Pattern 73 violation -- duplicating the trigger's
        future behavior in application code).  Mirrors the same discipline
        in ``crud_canonical_markets.retire_canonical_market`` even though
        canonical_events does not yet have the trigger.
        """
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        retire_canonical_event(7)

        sql = mock_cursor.execute.call_args[0][0]
        # The SET clause should ONLY touch retired_at, not updated_at
        set_clause = sql.split("WHERE")[0]
        assert "retired_at" in set_clause
        assert "updated_at" not in set_clause

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_retire_already_retired_event_returns_true_and_refreshes_timestamp(
        self, mock_get_cursor
    ):
        """Re-retiring an already-retired row returns True and refreshes retired_at.

        Pins the docstring claim: ``retire_canonical_event`` is idempotent in
        effect -- retiring an already-retired row simply refreshes
        ``retired_at`` to the current timestamp.  The SQL must NOT carry a
        guard clause like ``WHERE id = %s AND retired_at IS NULL`` (which
        would silently change rowcount=1 -> rowcount=0 for an already-retired
        row, regressing this idempotency contract to "first retire wins").

        Without this test, a future refactor that adds a "skip if already
        retired" guard could merge silently -- the docstring would say one
        thing, the implementation would do another, and callers depending
        on the documented refresh-on-re-retire behavior would break in ways
        that surface only at runtime.

        At the unit-test level we cannot distinguish "row was already
        retired" from "row was never retired" (we mock the cursor); the pin
        is therefore on the SQL shape (no NULL-guard on retired_at) plus the
        rowcount=1 -> True return contract.
        """
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1  # PG returns rowcount=1 even for re-retire (no guard)
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(7)

        # rowcount=1 -> True (matches the documented idempotent behavior)
        assert result is True

        sql = mock_cursor.execute.call_args[0][0]
        # The WHERE clause must NOT carry a NULL-guard on retired_at;
        # case-insensitive check defends against minor SQL stylistic drift.
        where_clause = sql.split("WHERE")[1] if "WHERE" in sql else ""
        assert "retired_at is null" not in where_clause.lower()
        # Positive shape pin: the WHERE is simply ``id = %s``
        assert "id = %s" in where_clause


# =============================================================================
# get_active_canonical_event (Migration 0087, cleanup epic Slot 3)
# =============================================================================


@pytest.mark.unit
class TestGetActiveCanonicalEvent:
    """Unit tests for get_active_canonical_event -- recursive-CTE chain walk
    returning terminal-active row OR None (per Q1 adjudication: tombstones
    return None, not the tombstone row)."""

    @patch("precog.database.crud_canonical_events.fetch_one")
    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u1_returns_active_row(self, mock_get_cursor, mock_fetch_one):
        """U1: helper called with active row id returns that row.

        Setup: 1 row (superseded_by=NULL, retired_at=NULL) -- terminal-active.
        First-pass cycle-detection probe returns max_hops=0; second-pass
        returns the active row; helper returns it.
        """
        # First pass (cycle-detection probe via get_cursor):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_hops": 0}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)
        # Second pass (active-row resolver via fetch_one):
        expected_row = _full_row_dict(id=7, superseded_by=None, retired_at=None)
        mock_fetch_one.return_value = expected_row

        result = get_active_canonical_event(7)

        assert result == expected_row
        # Verify recursive CTE shape on cycle-detection probe
        sql = mock_cursor.execute.call_args[0][0]
        assert "WITH RECURSIVE chain" in sql
        assert "hops < 100" in sql
        # Verify resolver path
        active_sql = mock_fetch_one.call_args[0][0]
        assert "WITH RECURSIVE chain" in active_sql
        assert "superseded_by IS NULL" in active_sql
        assert "retired_at IS NULL" in active_sql

    @patch("precog.database.crud_canonical_events.fetch_one")
    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u2_returns_none_for_terminal_tombstone(self, mock_get_cursor, mock_fetch_one):
        """U2: helper returns None for retired-without-replacement (Q1 boundary).

        Setup: 1 row (superseded_by=NULL, retired_at=now()) -- terminal
        tombstone.  Per Q1 user adjudication: helper returns None, NOT
        the tombstone row.  Callers needing the tombstone state can fall
        through to ``get_canonical_event_by_id()``.
        """
        # First pass: cycle-detection probe shows depth=0 (single row chain).
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_hops": 0}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)
        # Second pass: no row matches (superseded_by IS NULL AND retired_at IS NULL)
        # since the only row has retired_at IS NOT NULL.
        mock_fetch_one.return_value = None

        result = get_active_canonical_event(7)

        # Q1 boundary: tombstones return None, not the tombstone row.
        assert result is None

    @patch("precog.database.crud_canonical_events.fetch_one")
    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u3_walks_one_hop_chain(self, mock_get_cursor, mock_fetch_one):
        """U3: helper walks 1-hop chain old(retired) -> new(active).

        Setup: 2 rows -- old (superseded_by=new_id, retired_at=now()) +
        new (superseded_by=NULL, retired_at=NULL).  Helper called with
        old_id walks one hop and returns the new row.
        """
        # First pass: chain depth = 1
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_hops": 1}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)
        # Second pass: returns the active head (id 8, the new row)
        expected_row = _full_row_dict(id=8, superseded_by=None, retired_at=None)
        mock_fetch_one.return_value = expected_row

        result = get_active_canonical_event(7)

        assert result == expected_row
        assert result["id"] == 8
        assert result["superseded_by"] is None
        assert result["retired_at"] is None

    @patch("precog.database.crud_canonical_events.fetch_one")
    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u4_walks_multi_hop_chain(self, mock_get_cursor, mock_fetch_one):
        """U4: helper walks 3-row chain id1 -> id2 -> id3 (id3 active).

        Setup: 3 rows in a forward retirement chain; helper called with
        id1 walks two hops and returns id3.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_hops": 2}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)
        expected_row = _full_row_dict(id=3, superseded_by=None, retired_at=None)
        mock_fetch_one.return_value = expected_row

        result = get_active_canonical_event(1)

        assert result == expected_row
        assert result["id"] == 3

    @patch("precog.database.crud_canonical_events.fetch_one")
    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u5_returns_none_for_missing_id(self, mock_get_cursor, mock_fetch_one):
        """U5: helper returns None when start id does not exist.

        Setup: id 99999 does not exist; recursive CTE returns zero rows;
        first-pass probe returns max_hops=None; second-pass resolver
        returns None.  Helper returns None without raising.
        """
        mock_cursor = MagicMock()
        # When the start id doesn't exist, the recursive CTE returns no
        # rows; MAX(hops) over zero rows is NULL (None in Python).
        mock_cursor.fetchone.return_value = {"max_hops": None}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_fetch_one.return_value = None

        result = get_active_canonical_event(99999)

        assert result is None

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u6_raises_runtimeerror_on_cycle(self, mock_get_cursor):
        """U6: helper raises RuntimeError when chain saturates 100-hop bound.

        Setup: simulate a 2-row cycle (id1 -> id2 -> id1 -> ...).  The
        recursive CTE would expand each hop until the depth bound; the
        first-pass probe returns max_hops=100, helper raises.

        Pattern 73 SSOT: the read-side 100-hop guard is layer (c) of the
        3-layer cycle defense; this test pins the failure mode for any
        cycle that escaped the application-layer write-side validation
        (e.g., direct SQL writes bypassing retire_canonical_event).
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_hops": 100}
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError) as exc_info:
            get_active_canonical_event(7)
        assert "100 hops" in str(exc_info.value)
        assert "id=7" in str(exc_info.value)


# =============================================================================
# retire_canonical_event extension (Migration 0087, cleanup epic Slot 3)
# =============================================================================


@pytest.mark.unit
class TestRetireCanonicalEventWithSupersededBy:
    """Unit tests for retire_canonical_event(id, superseded_by_id=...) --
    Migration 0087 / cleanup epic Slot 3 extension.  Pattern 73 SSOT cycle
    prevention: layer (b) of 3-layer defense lives in this write surface.
    """

    @patch("precog.database.crud_canonical_events.get_cursor")
    @patch("precog.database.crud_canonical_events._retirement_chain_includes")
    def test_u7_writes_both_columns_atomically(self, mock_chain_includes, mock_get_cursor):
        """U7: retire(old_id, superseded_by_id=new_id) writes BOTH columns
        in a SINGLE atomic UPDATE.

        Atomicity invariant: external observers reading the row mid-
        transaction see either pre-state (both NULL) or post-state (both
        set), never the half-state.  Pinned by checking that exactly ONE
        execute() call was made AND that its SQL writes both columns in
        one SET clause.
        """
        mock_chain_includes.return_value = False  # No cycle
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(7, superseded_by_id=42)

        assert result is True
        # Atomicity pin: exactly ONE execute() call, not two.
        assert mock_cursor.execute.call_count == 1, (
            "retire_canonical_event(superseded_by_id=...) must write both "
            "columns in a single atomic UPDATE; got "
            f"{mock_cursor.execute.call_count} execute() calls"
        )
        sql, params = mock_cursor.execute.call_args[0]
        # Both columns appear in a single SET clause
        assert "UPDATE canonical_events" in sql
        set_clause = sql.split("WHERE")[0]
        assert "retired_at = now()" in set_clause
        assert "superseded_by = %s" in set_clause
        assert "WHERE id = %s" in sql
        # Params order: superseded_by_id first (matches SET column order),
        # canonical_event_id second (WHERE).
        assert params == (42, 7)
        # Cycle-check helper was called with (superseded_by_id, canonical_event_id)
        mock_chain_includes.assert_called_once_with(42, 7)

    @patch("precog.database.crud_canonical_events.get_cursor")
    def test_u8_without_superseded_by_id_preserves_terminal_semantics(self, mock_get_cursor):
        """U8: retire(id) without second arg preserves backward-compat path.

        SET clause writes retired_at only; superseded_by stays NULL.
        Existing tests in TestRetireCanonicalEvent above continue to
        exercise this branch.  This test is the affirmative pin that the
        backward-compat signature is preserved (callers writing
        ``retire_canonical_event(id)`` -- no kwarg -- get exactly the
        pre-Migration-0087 behavior).
        """
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(7)  # no superseded_by_id kwarg

        assert result is True
        sql, params = mock_cursor.execute.call_args[0]
        set_clause = sql.split("WHERE")[0]
        assert "retired_at = now()" in set_clause
        # superseded_by must NOT appear in the backward-compat SET clause
        assert "superseded_by" not in set_clause, (
            "Backward-compat path (no superseded_by_id) must NOT write "
            "superseded_by; got SET clause: " + set_clause
        )
        # Params shape: single-element tuple
        assert params == (7,)

    @patch("precog.database.crud_canonical_events.get_cursor")
    @patch("precog.database.crud_canonical_events._retirement_chain_includes")
    def test_u9_rejects_cycle_introduction(self, mock_chain_includes, mock_get_cursor):
        """U9: retire(old_id, superseded_by_id=new_id) raises ValueError if
        new_id's chain leads back to old_id.

        Pattern 73 SSOT cycle prevention -- layer (b) of the 3-layer
        defense.  The cycle-check helper returns True; retire raises
        BEFORE writing; the database UPDATE is never issued.
        """
        # Simulate cycle: chain from new_id (42) leads back to old_id (7).
        mock_chain_includes.return_value = True

        with pytest.raises(ValueError) as exc_info:
            retire_canonical_event(7, superseded_by_id=42)

        msg = str(exc_info.value)
        assert "canonical_event_id=7" in msg
        assert "superseded_by_id=42" in msg
        assert "cycle rejected" in msg
        # Critical invariant: no UPDATE was issued (database state unchanged).
        mock_get_cursor.assert_not_called()

    @patch("precog.database.crud_canonical_events.get_cursor")
    @patch("precog.database.crud_canonical_events._retirement_chain_includes")
    def test_u10_allows_chain_extension_forward(self, mock_chain_includes, mock_get_cursor):
        """U10: retire(id3, superseded_by_id=id4) succeeds when id4 is fresh.

        Setup: 3-row chain id1 -> id2 -> id3 (id3 active); caller wants to
        extend the chain forward by retiring id3 with superseded_by_id=id4
        (fresh row).  The cycle check walks id4's chain (which is empty
        or terminates without including id3); returns False; retire
        proceeds normally.

        This test discriminates "valid chain extension" from "cycle
        introduction" -- both involve writing superseded_by_id to a
        retired row, but only the cycle case should raise.
        """
        # Cycle check returns False (id4's chain does NOT include id3).
        mock_chain_includes.return_value = False
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = retire_canonical_event(3, superseded_by_id=4)

        assert result is True
        # Cycle-check helper was consulted with (superseded_by_id=4,
        # canonical_event_id=3) before the UPDATE.
        mock_chain_includes.assert_called_once_with(4, 3)
        # UPDATE was issued with both-columns-in-one-statement shape.
        assert mock_cursor.execute.call_count == 1
        sql, params = mock_cursor.execute.call_args[0]
        assert "superseded_by = %s" in sql
        assert "retired_at = now()" in sql
        assert params == (4, 3)


# =============================================================================
# get_canonical_event_domain_id_by_domain
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEventDomainIdByDomain:
    """Unit tests for get_canonical_event_domain_id_by_domain -- text -> id resolver."""

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_id_when_domain_seeded(self, mock_fetch_one):
        """Returns the integer id for a seeded domain."""
        mock_fetch_one.return_value = {"id": 1}

        result = get_canonical_event_domain_id_by_domain("sports")

        assert result == 1
        sql, params = mock_fetch_one.call_args[0]
        assert "FROM canonical_event_domains" in sql
        assert "WHERE domain = %s" in sql
        assert params == ("sports",)

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_none_when_domain_not_seeded(self, mock_fetch_one):
        """Returns None when no canonical_event_domains row matches the given text."""
        mock_fetch_one.return_value = None

        result = get_canonical_event_domain_id_by_domain("unicorn_domain")

        assert result is None

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_case_sensitive_passthrough(self, mock_fetch_one):
        """Domain text is passed through unchanged (case-sensitive at the DB layer).

        The seed values are lowercase ('sports', 'politics', ...).  Callers
        passing 'Sports' or 'SPORTS' will hit a None result; this CRUD
        function does NOT lowercase or normalize input.  Pinning the
        contract here.
        """
        mock_fetch_one.return_value = None

        result = get_canonical_event_domain_id_by_domain("SPORTS")

        assert result is None
        params = mock_fetch_one.call_args[0][1]
        assert params == ("SPORTS",)  # passed through verbatim


# =============================================================================
# get_canonical_event_type_id_by_domain_and_type
# =============================================================================


@pytest.mark.unit
class TestGetCanonicalEventTypeIdByDomainAndType:
    """Unit tests for get_canonical_event_type_id_by_domain_and_type -- composite resolver."""

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_id_when_pair_seeded(self, mock_fetch_one):
        """Returns the integer id for a seeded (domain_id, event_type) pair."""
        mock_fetch_one.return_value = {"id": 1}

        result = get_canonical_event_type_id_by_domain_and_type(1, "game")

        assert result == 1
        sql, params = mock_fetch_one.call_args[0]
        assert "FROM canonical_event_types" in sql
        assert "WHERE domain_id = %s AND event_type = %s" in sql
        assert params == (1, "game")

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_returns_none_when_pair_not_seeded(self, mock_fetch_one):
        """Returns None when no canonical_event_types row matches the pair."""
        mock_fetch_one.return_value = None

        result = get_canonical_event_type_id_by_domain_and_type(1, "unicorn_type")

        assert result is None

    @patch("precog.database.crud_canonical_events.fetch_one")
    def test_case_sensitive_passthrough(self, mock_fetch_one):
        """Event type text is passed through unchanged (case-sensitive)."""
        mock_fetch_one.return_value = None

        result = get_canonical_event_type_id_by_domain_and_type(1, "GAME")

        assert result is None
        params = mock_fetch_one.call_args[0][1]
        assert params == (1, "GAME")  # passed through verbatim
