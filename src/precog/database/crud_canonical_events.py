"""CRUD operations for canonical_events (+ canonical_event_domains and
canonical_event_types resolver helpers).

Cohort 1A Pattern 14 retro (issue #1021 Slice C) -- the canonical-events tier
is the foundational "Level B" canonical identity layer from ADR-118 V2.38.
Sister module to ``crud_canonical_markets.py`` (Cohort 2) and
``crud_canonical_entity.py`` (Slice B); mirrors those modules' raw-psycopg2 +
``get_cursor`` / ``fetch_one`` + RealDictCursor + heavy-docstring conventions
verbatim.

Cleanup epic Slot 1 (Migration 0085, V2.47 ADR amendment) renamed two
columns on ``canonical_events``: ``domain_id`` -> ``event_domain_id``
(FK column naming convention) and ``entities_sorted`` -> ``participants_sorted``
(participants vocabulary alignment).  Resolver helpers below carry the
new column name in their SQL bodies + parameter names.

Tables covered:
    - ``canonical_events`` (Migration 0067; ``event_domain_id`` +
      ``participants_sorted`` per Migration 0085) -- the canonical
      (platform-agnostic) event row.  Discriminated by ``event_domain_id``
      -> ``canonical_event_domains`` and ``event_type_id`` ->
      ``canonical_event_types`` (both Pattern 81 lookups).  ``natural_key_hash``
      is the UNIQUE business identity for cross-platform identity resolution
      (derivation rule is application-layer, deferred to Cohort 5).  See
      Migration 0067 docstring for the full DDL rationale and ADR-118 V2.38
      decisions.
    - ``canonical_event_domains`` (lookup, Migration 0067) -- read-only
      resolver helper ``get_canonical_event_domain_id_by_domain()`` only.
    - ``canonical_event_types`` (lookup, Migration 0067) -- read-only
      resolver helper ``get_canonical_event_type_id_by_domain_and_type()``
      only.  Natural key is the composite ``(domain_id, event_type)``
      where ``domain_id`` is the local FK column name on
      ``canonical_event_types`` (NOT renamed by Migration 0085 -- only
      the corresponding column on ``canonical_events`` was renamed).

Pattern 14 5-step bundle status:
    This module is **step 3 of 5** for Slice C of the Cohort 1A retro
    (issue #1021):
        - step 1 = Migration 0067 (already shipped session 71-72 PR #1003);
        - step 2 (SQLAlchemy ORM model) is **N/A** because Precog uses raw
          psycopg2 only (no SQLAlchemy ORM despite CLAUDE.md's Tech Stack
          line) -- mirrors the Cohort 2 ``crud_canonical_markets`` and
          Slice B ``crud_canonical_entity`` deferrals;
        - step 3 = this module;
        - step 4 = ``tests/unit/database/test_crud_canonical_events_unit.py``;
        - step 5 (integration tests) is already covered by
          ``tests/integration/database/test_migration_0067_canonical_events_foundation.py``
          (#1012 / session 76 PR #1045) -- bundled in Slice C scope per
          #1021 acceptance criteria.

UPDATE / RETIRE coverage (Slice C deliberate gap, mirrors Cohort 2 Glokta
Finding 10 and Slice B deferrals):
    This module ships ``create_canonical_event`` + ``retire_canonical_event``
    + lookup helpers.  ``canonical_events`` was migrated WITH ``updated_at``
    and ``retired_at`` columns (post-Slot-2 column list: id,
    event_domain_id, event_type_id, participants_sorted, resolution_window,
    resolution_rule_fp, natural_key_hash, title, description,
    lifecycle_phase, metadata, created_at, updated_at, retired_at), so the
    canonical-tier ``retire_X`` verb is in scope.  No general
    ``update_canonical_event_metadata()`` or ``update_canonical_event()``
    helper -- metadata-enrichment helpers land with Cohort 5+ when the
    matcher pipeline begins writing to ``canonical_events.metadata``.  Until
    then, callers needing UPDATE coverage beyond retirement must NOT write
    ad-hoc UPDATE SQL (Pattern 73 violation -- drift across consumers); file
    an issue or add the helper here first.

Note on ``updated_at`` (BEFORE UPDATE trigger status -- shipped in
Migration 0076):
    ``canonical_events.updated_at`` was migrated with ``DEFAULT now()`` in
    Migration 0067 but the BEFORE UPDATE trigger that refreshes it on
    UPDATE was not installed until Migration 0076 (generic
    ``set_updated_at()`` retrofit, ADR-118 V2.42 sub-amendment A).
    Pre-Migration-0076 the column was a static creation timestamp;
    post-Migration-0076 the trigger ``trg_canonical_events_updated_at``
    advances ``updated_at`` automatically on every UPDATE.
    ``retire_canonical_event`` writes ``retired_at = now()`` ONLY and
    relies on the trigger to refresh ``updated_at`` (Pattern 73 SSOT:
    the trigger is the canonical source for ``updated_at`` semantics;
    this module trusts it).  Post-retrofit, ``updated_at`` reflects any
    DB-side row modification, including FK-NULL cascades from upstream
    DELETEs (per ADR-118 V2.42 sub-amendment B / Migration 0077) -- it
    is NOT a "last canonical content change" timestamp.

Note on ``game_id`` / ``series_id`` (RETIRED in Migration 0086 -- cleanup
epic Slot 2 / session 96):
    Both columns were FKs into the platform tier (``games.id`` /
    ``series.id``) shipped in Migration 0067 + retrofitted to ``ON DELETE
    SET NULL`` in Migration 0077 (ADR-118 V2.42 sub-amendment B).
    Migration 0086 (cleanup epic Slot 2) DROPped both columns as part of
    the CL-2 denorm collapse: canonical_events is the platform-agnostic
    identity layer, so carrying platform-side dim FKs on the canonical row
    was a denormalization shortcut from Cohort 1A.  Post-Slot-2 the
    platform->canonical direction is the only direction (games references
    canonical_events via ``games.canonical_event_id`` per Migration 0080;
    canonical_events does not reference games / series at all).
    ``create_canonical_event`` no longer accepts ``game_id`` / ``series_id``
    parameters; row-projection helpers no longer return those columns.
    Cohort 5+ matcher code that needs the platform-row -> canonical-event
    direction reads ``games.canonical_event_id`` / ``series.canonical_event_id``
    directly (slot 0080 + future slots).

Note on ``superseded_by`` + retirement cascade (NET-NEW in Migration 0087 --
cleanup epic Slot 3 / session 97):
    Migration 0087 ADDs ``canonical_events.superseded_by BIGINT NULL`` with
    a self-referencing FK -> ``canonical_events(id)`` ON DELETE SET NULL,
    plus a CHECK constraint blocking id->id self-cycles.  Coordinated with
    the existing ``retired_at`` column to encode three states:

        Active row:                  superseded_by IS NULL AND retired_at IS NULL
        Retired without replacement: superseded_by IS NULL AND retired_at IS NOT NULL
                                     (terminal tombstone)
        Superseded (replaced):       superseded_by = <new_id> AND retired_at IS NOT NULL

    The SSOT helper ``get_active_canonical_event(id)`` (introduced this
    slot) walks the chain forward via recursive CTE and returns the
    terminal-active row OR None if the chain terminates at a tombstone.
    This is the canonical entry point for active-row resolution post-
    Slot-3; forward callers (Cohort 5+ matcher slot, future strategy /
    model code) use the helper, not inline WHERE clauses against
    ``superseded_by`` or ``retired_at``.

    ``retire_canonical_event`` extends this slot to optionally accept a
    ``superseded_by_id`` kwarg.  When set, the function (a) validates the
    chain from ``superseded_by_id`` does not lead back to the canonical
    event being retired (Pattern 73 SSOT cycle prevention -- one write
    surface = one validation point); (b) sets BOTH ``retired_at`` AND
    ``superseded_by`` in a single atomic UPDATE.  When unset, the existing
    terminal-tombstone semantics are preserved (backward compat).

Slice C scope (this module) -- exactly these tables:
    - ``canonical_events`` (CRUD: create + 2 lookups + retire);
    - ``canonical_event_domains`` (read-only resolver helper);
    - ``canonical_event_types`` (read-only resolver helper);
    - NOT covered (separate Slice C module, ``crud_canonical_event_participants.py``):
        * ``canonical_event_participants`` (typed relation CRUD)
        * ``canonical_participant_roles`` (read-only resolver helper)
    - NOT covered (already shipped Slice B):
        * ``canonical_entity`` -- ``crud_canonical_entity.py``
        * ``canonical_entity_kinds`` -- resolver in ``crud_canonical_entity.py``
    - NOT covered (already shipped Cohort 2):
        * ``canonical_markets`` -- ``crud_canonical_markets.py``

Reference:
    - ``docs/foundation/ARCHITECTURE_DECISIONS.md`` ADR-118 V2.38+ (Cohort 1
      ratification + V2.40 carry-forward + V2.41 Cohort 3 amendment)
    - ``src/precog/database/alembic/versions/0067_canonical_events_foundation.py``
    - ``src/precog/database/crud_canonical_markets.py`` (style reference --
      Cohort 2 sibling template, mirrored line-for-line)
    - ``src/precog/database/crud_canonical_entity.py`` (Slice B sibling --
      lookup resolver co-location precedent)
    - ``tests/integration/database/test_migration_0067_canonical_events_foundation.py``
      (#1012 trigger DDL/body coverage)
"""

import json
from typing import Any, cast

from .connection import fetch_one, get_cursor

# =============================================================================
# CANONICAL EVENTS OPERATIONS
# =============================================================================


def create_canonical_event(
    event_domain_id: int,
    event_type_id: int,
    participants_sorted: list[int],
    resolution_window: str,
    natural_key_hash: bytes,
    title: str,
    description: str | None = None,
    lifecycle_phase: str = "proposed",
    resolution_rule_fp: bytes | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """
    Create a new canonical_events row.

    Canonical events are the platform-agnostic identity tier for individual
    real-world events (sports games, political elections, weather events,
    earnings releases, ...).  Per ADR-118 V2.38, this row is the canonical
    anchor that cross-platform replicas point to via
    ``canonical_event_links`` (Migration 0072+, Cohort 3).

    Migration 0085 (cleanup epic Slot 1) renamed two columns on
    ``canonical_events``: ``domain_id`` -> ``event_domain_id`` and
    ``entities_sorted`` -> ``participants_sorted``.  Keyword argument
    names below mirror the new column names for naming-convention parity.

    Args:
        event_domain_id: Integer FK into ``canonical_event_domains.id``
            (Pattern 81 lookup; 7 seeded domains in Migration 0067:
            sports, politics, weather, econ, news, entertainment, fighting).
            Use ``get_canonical_event_domain_id_by_domain()`` to resolve
            from the human-readable domain string.  ON DELETE RESTRICT --
            domains outlive any single event.
        event_type_id: Integer FK into ``canonical_event_types.id`` (Pattern
            81 lookup; ~13 per-domain event types seeded in Migration 0067).
            Use ``get_canonical_event_type_id_by_domain_and_type()`` to
            resolve from the (domain, event_type) text composite.  ON DELETE
            RESTRICT.
        participants_sorted: ``INTEGER[]`` array of canonical_entities ids
            that participate in this event, sorted ascending.  WITHOUT FK
            constraint at the column level (Migration 0067 ships
            ``participants_sorted`` agnostic to ``canonical_entities`` to
            avoid a cross-cohort dependency cycle); callers must ensure
            the ids reference real ``canonical_entities.id`` rows.
        resolution_window: ``TSTZRANGE`` string (e.g.,
            ``"[2026-04-26 12:00+00, 2026-04-26 16:00+00]"``).  Required
            (NOT NULL).  Defines the time interval within which the event
            outcome is determined.
        natural_key_hash: ``BYTEA`` (Python ``bytes``).  Derivation rule is
            APPLICATION-LAYER (deferred to Cohort 5 / Migration 0085 seed
            context); this CRUD function is agnostic to the rule and simply
            persists what the caller provides.  ``UNIQUE`` constraint
            (``uq_canonical_events_nk``) -- duplicate hashes raise
            ``psycopg2.IntegrityError``.
        title: Human-readable event title (e.g., "Buffalo Bills @ Miami
            Dolphins, Week 1").  ``VARCHAR`` -- NOT NULL.
        description: Optional human-readable description.  ``TEXT``.
        lifecycle_phase: Closed-enum-like string.  Defaults to ``'proposed'``
            per ADR-118 V2.38 Phase B.5 state machine.  Migration 0067
            ships this as ``VARCHAR(32) NOT NULL DEFAULT 'proposed'`` with
            no inline CHECK; Pattern 84 CHECK retrofit lands separately
            (#1037 / Migration 0070 -- Slice B carry-forward, already
            shipped).
        resolution_rule_fp: Optional ``BYTEA`` fingerprint of the resolution
            rule.  NULLABLE -- most events do not encode a rule fingerprint
            until the matcher pipeline (Cohort 5+) populates it.
        metadata: Optional JSONB dict.  Serialized via ``json.dumps`` (mirrors
            the ``crud_canonical_markets.create_canonical_market`` and
            ``crud_canonical_entity.create_canonical_entity`` metadata
            convention).

    Returns:
        Full row dict of the created canonical event.  Keys (post-
        Migration-0087, 15 keys; ``superseded_by`` is NULL for newly-
        created rows -- it is set by ``retire_canonical_event(id,
        superseded_by_id=...)`` when an existing row is superseded):
            id, event_domain_id, event_type_id, participants_sorted,
            resolution_window, resolution_rule_fp, natural_key_hash, title,
            description, lifecycle_phase, metadata, created_at, updated_at,
            retired_at, superseded_by

    Raises:
        psycopg2.IntegrityError: If ``natural_key_hash`` already exists,
            ``event_domain_id`` / ``event_type_id`` do not reference real
            rows in their target tables, or ``resolution_window`` is
            malformed (PG range parser rejects it).

    Example:
        >>> import hashlib
        >>> from precog.database.crud_canonical_events import (
        ...     get_canonical_event_domain_id_by_domain,
        ...     get_canonical_event_type_id_by_domain_and_type,
        ...     create_canonical_event,
        ... )
        >>> event_domain_id = get_canonical_event_domain_id_by_domain("sports")
        >>> event_type_id = get_canonical_event_type_id_by_domain_and_type(
        ...     event_domain_id, "game"
        ... )
        >>> nk = hashlib.sha256(b"NFL|2026-09-04|BUF|MIA").digest()
        >>> row = create_canonical_event(
        ...     event_domain_id=event_domain_id,
        ...     event_type_id=event_type_id,
        ...     participants_sorted=[1, 2],  # canonical_entities ids, ascending
        ...     resolution_window="[2026-09-04 17:00+00, 2026-09-04 21:00+00]",
        ...     natural_key_hash=nk,
        ...     title="Buffalo Bills @ Miami Dolphins, Week 1",
        ... )
        >>> row["id"]  # BIGSERIAL surrogate PK
        7
        >>> row["lifecycle_phase"]
        'proposed'

    Educational Note:
        ``canonical_events`` is the FIRST tier in the canonical identity
        hierarchy: ``canonical_events -> canonical_markets -> (Cohort 3:
        canonical_market_links -> platform markets)``.  This row exists
        once per real-world event regardless of how many platforms list a
        replica market for it.

        ``lifecycle_phase`` defaults to ``'proposed'`` to encode the Phase
        B.5 state-machine seed state -- the matcher pipeline (Cohort 5+)
        transitions rows to ``'matched'`` / ``'resolved'`` / ``'voided'``
        as the event lifecycle progresses.

        ``updated_at`` is maintained automatically by the
        ``trg_canonical_events_updated_at`` BEFORE UPDATE trigger (shipped
        in Migration 0076 -- generic ``set_updated_at()`` retrofit per
        ADR-118 V2.42 sub-amendment A).  Callers MUST NOT write
        ``updated_at`` themselves; the trigger is the canonical source
        for the column (Pattern 73 SSOT compliance).

    Reference:
        - ``docs/foundation/ARCHITECTURE_DECISIONS.md`` ADR-118 V2.38+
        - Migration 0067 (table DDL + lookup seeds)
        - ADR-118 V2.38 decisions #2 (Pattern 81 domain), #3 (Pattern 81
          event_type), #4 (entities_sorted shape), #6 (lifecycle_phase
          default)
    """
    query = """
        INSERT INTO canonical_events (
            event_domain_id, event_type_id, participants_sorted, resolution_window,
            resolution_rule_fp, natural_key_hash, title, description,
            lifecycle_phase, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, event_domain_id, event_type_id, participants_sorted,
                  resolution_window, resolution_rule_fp, natural_key_hash,
                  title, description, lifecycle_phase, metadata,
                  created_at, updated_at, retired_at, superseded_by
    """

    params = (
        event_domain_id,
        event_type_id,
        participants_sorted,
        resolution_window,
        resolution_rule_fp,
        natural_key_hash,
        title,
        description,
        lifecycle_phase,
        json.dumps(metadata) if metadata is not None else None,
    )

    with get_cursor(commit=True) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row)


def get_canonical_event_by_id(canonical_event_id: int) -> dict[str, Any] | None:
    """
    Get a canonical_events row by its surrogate integer PK.

    Args:
        canonical_event_id: BIGSERIAL surrogate PK from ``canonical_events.id``.

    Returns:
        Full row dict if found, ``None`` otherwise.  Keys (post-
        Migration-0087, 15 keys):
            id, event_domain_id, event_type_id, participants_sorted,
            resolution_window, resolution_rule_fp, natural_key_hash, title,
            description, lifecycle_phase, metadata,
            created_at, updated_at, retired_at, superseded_by

        Migration 0086 (cleanup epic Slot 2 / session 96) DROPped
        ``game_id`` / ``series_id`` from the column inventory; callers
        needing the platform-row -> canonical-event direction read
        ``games.canonical_event_id`` directly (slot 0080).
        Migration 0087 (cleanup epic Slot 3 / session 97) ADDed
        ``superseded_by`` to encode the retirement-supersession chain;
        for active-row resolution use ``get_active_canonical_event(id)``
        rather than this low-level lookup.

    Example:
        >>> row = get_canonical_event_by_id(7)
        >>> if row:
        ...     print(row["title"])             # 'Buffalo Bills @ Miami Dolphins, Week 1'
        ...     print(row["lifecycle_phase"])   # 'proposed' / 'matched' / etc
        ...     print(row["retired_at"])        # None (active) or timestamp

    Educational Note:
        Lookup by surrogate PK is the cheapest path (single B-tree probe on
        the primary key).  For lookup by natural identity (cross-platform
        matching), use ``get_canonical_event_by_natural_key_hash()`` which
        hits the ``uq_canonical_events_nk`` UNIQUE index.

        For ACTIVE-row resolution (post-Migration-0087, cleanup epic Slot 3),
        use ``get_active_canonical_event(id)`` instead -- it walks the
        ``superseded_by`` retirement chain forward and returns the terminal-
        active row (or None for tombstones).  This low-level helper returns
        the row at the given id verbatim; if you wanted the active head of
        a retired row's chain, you want ``get_active_canonical_event``.

    Reference:
        - Migration 0067 (table DDL)
        - Migration 0087 (cleanup epic Slot 3 -- adds superseded_by + the
          ``get_active_canonical_event`` SSOT helper that supersedes this
          function for active-row semantics)
        - ``crud_canonical_markets.get_canonical_market_by_id`` (sibling
          lookup-by-PK pattern)
    """
    query = """
        SELECT id, event_domain_id, event_type_id, participants_sorted,
               resolution_window, resolution_rule_fp, natural_key_hash,
               title, description, lifecycle_phase,
               metadata, created_at, updated_at, retired_at, superseded_by
        FROM canonical_events
        WHERE id = %s
    """
    return fetch_one(query, (canonical_event_id,))


def get_canonical_event_by_natural_key_hash(
    natural_key_hash: bytes,
) -> dict[str, Any] | None:
    """
    Get a canonical_events row by its natural_key_hash.

    This is the canonical lookup for cross-platform identity resolution: when
    a new platform event is observed, the matching layer (Cohort 5+) computes
    the natural key hash from the event's normalized identity inputs and
    looks up the canonical row via this function.  A hit means "this is a
    cross-platform replica of an existing canonical event"; a miss means
    "this is a new canonical identity, create it".

    Args:
        natural_key_hash: BYTEA hash bytes (typically 32 bytes from sha256).
            Derivation rule is application-layer; this function is agnostic
            to how the hash was computed.

    Returns:
        Full row dict if found, ``None`` otherwise.  Same keys as
        ``get_canonical_event_by_id``.  Post-Migration-0086 (cleanup epic
        Slot 2) the column inventory excludes ``game_id`` / ``series_id``;
        post-Migration-0087 (cleanup epic Slot 3) adds ``superseded_by``.

    Example:
        >>> import hashlib
        >>> nk = hashlib.sha256(b"NFL|2026-09-04|BUF|MIA").digest()
        >>> row = get_canonical_event_by_natural_key_hash(nk)
        >>> if row is None:
        ...     print("New canonical identity -- caller should create it")
        ... else:
        ...     print(f"Existing canonical id={row['id']} found")

    Educational Note:
        ``natural_key_hash`` is ``UNIQUE`` (constraint
        ``uq_canonical_events_nk``), so this query returns at most one row.
        The ``UNIQUE`` index makes the lookup O(log n) regardless of table
        size.

        The derivation rule for ``natural_key_hash`` is intentionally
        APPLICATION-LAYER and is being specified separately as part of
        Cohort 5 (Migration 0085 seed context).  This CRUD function is
        a thin lookup that does not validate the hash shape or
        derivation -- that is the matching layer's responsibility.

        For ACTIVE-row resolution after a hit (post-Migration-0087, cleanup
        epic Slot 3), feed the returned row's ``id`` into
        ``get_active_canonical_event(id)`` to walk the ``superseded_by``
        retirement chain forward.  This function returns the verbatim row
        at the natural-key-hash match, which may itself be a retired or
        superseded row -- the matcher pipeline (Cohort 5+) is the natural
        consumer for the active-row resolution.

    Reference:
        - Migration 0067 (table DDL -- ``uq_canonical_events_nk``)
        - Migration 0087 (cleanup epic Slot 3 -- adds superseded_by + the
          ``get_active_canonical_event`` SSOT helper)
        - ADR-118 V2.38 "natural_key_hash derivation rule (deferral note)"
        - Future: ``src/precog/matching/`` (Cohort 5)
    """
    query = """
        SELECT id, event_domain_id, event_type_id, participants_sorted,
               resolution_window, resolution_rule_fp, natural_key_hash,
               title, description, lifecycle_phase,
               metadata, created_at, updated_at, retired_at, superseded_by
        FROM canonical_events
        WHERE natural_key_hash = %s
    """
    return fetch_one(query, (natural_key_hash,))


def get_active_canonical_event(canonical_event_id: int) -> dict[str, Any] | None:
    """
    Resolve the terminal-active row of a canonical_events retirement chain.

    Walks the ``superseded_by`` chain forward starting from
    ``canonical_event_id``.  Returns the terminal-active row, where
    "active" is defined by the schema-coordinated invariant:
    ``superseded_by IS NULL AND retired_at IS NULL``.  Returns None for:

        - A row that does not exist (start id missing).
        - A retirement chain whose terminal row is a tombstone (retired
          without replacement: ``superseded_by IS NULL AND retired_at IS
          NOT NULL``) -- per Q1 user adjudication.

    This is the canonical entry point for active-row resolution post-
    Migration-0087.  Forward callers (Cohort 5+ matcher slot, future
    strategy / model code) should use this helper rather than inline
    WHERE clauses against ``superseded_by`` or ``retired_at`` (Pattern 73
    SSOT discipline -- one helper, one chain-walk semantics).

    Args:
        canonical_event_id: BIGSERIAL surrogate PK from
            ``canonical_events.id``.  May reference an active row, a
            superseded row, a tombstone, or a non-existent id; the helper
            handles all four uniformly.

    Returns:
        Full row dict for the terminal-active row of the chain starting
        at ``canonical_event_id``, OR ``None`` if:
            - the start id does not exist; OR
            - the chain terminates at a tombstone (Q1 boundary).

        Row dict keys (post-Migration-0087, 15 keys -- same projection as
        ``get_canonical_event_by_id``):
            id, event_domain_id, event_type_id, participants_sorted,
            resolution_window, resolution_rule_fp, natural_key_hash, title,
            description, lifecycle_phase, metadata,
            created_at, updated_at, retired_at, superseded_by

    Raises:
        RuntimeError: If the chain exceeds 100 hops -- defensive guard
            against multi-row cycles that escaped the application-layer
            validation in ``retire_canonical_event(superseded_by_id=...)``
            (e.g., direct SQL writes bypassing the SSOT entry point).
            Per Q2 user adjudication 3-layer defense: layer (c).

    Example:
        >>> # Chain: id1 -> id2 -> id3 (id3 active, id1+id2 superseded).
        >>> # Caller passes id1; helper walks chain and returns id3's row.
        >>> active = get_active_canonical_event(1)
        >>> if active is None:
        ...     print("Canonical id 1's chain terminates at a tombstone OR id missing")
        ... else:
        ...     print(f"Active head of id 1's chain is id {active['id']}")

    Educational Note:
        The helper uses a recursive CTE with an explicit ``hops < 100``
        clause for cycle protection.  At application-layer validation
        (``retire_canonical_event(superseded_by_id=...)``) plus the
        DB-level single-row CHECK constraint
        ``canonical_events_no_self_supersession``, the only way a cycle
        can be introduced is via direct SQL bypassing the helper -- this
        100-hop guard is the runtime safety net for that escape path.

        Returning None for terminal tombstones (rather than the tombstone
        row itself) reflects the helper's contract: it answers "what is
        the active head", not "what is the most-recent state of this
        chain".  Callers needing the tombstone row can fall through to
        ``get_canonical_event_by_id()`` after the helper returns None.

        Multi-row chains are conceptually rare in production; the typical
        retirement path is a single supersession (``id1 -> id2``, where
        id2 is active) or a tombstone (``id1 -> NULL``, retired without
        replacement).  Long chains arise only in unusual operator-driven
        re-supersession sequences.

    Reference:
        - Migration 0087 (cleanup epic Slot 3 -- adds superseded_by self-FK
          + the canonical_events_no_self_supersession single-row CHECK)
        - ``retire_canonical_event(canonical_event_id, superseded_by_id=...)``
          (write-side SSOT; layer (b) of the 3-layer cycle defense)
        - ``memory/build_spec_slot_3_retirement_cascade_pm_memo.md`` § 0
          P91 catch #3 (3-layer cycle prevention rationale)
    """
    query = """
        WITH RECURSIVE chain AS (
            SELECT id, superseded_by, retired_at, 0 AS hops
              FROM canonical_events
             WHERE id = %s
            UNION ALL
            SELECT ce.id, ce.superseded_by, ce.retired_at, c.hops + 1
              FROM canonical_events ce
              JOIN chain c ON ce.id = c.superseded_by
             WHERE c.hops < 100
        )
        SELECT MAX(hops) AS max_hops
          FROM chain
    """
    # First pass: detect cycle via depth-bound saturation.  If max_hops
    # reaches 100, the chain is either truly 100 deep or cyclic (the
    # recursive CTE re-enters the same row but the depth bound prevents
    # runaway).  Either way the helper raises -- 100 hops is a
    # data-quality flag, tunable via constant if proven too tight.
    with get_cursor() as cur:
        cur.execute(query, (canonical_event_id,))
        depth_row = cur.fetchone()
    if (
        depth_row is not None
        and depth_row.get("max_hops") is not None
        and depth_row["max_hops"] >= 100
    ):
        raise RuntimeError(
            f"get_active_canonical_event: chain exceeds 100 hops, "
            f"suspected cycle starting from id={canonical_event_id}"
        )

    # Second pass: walk to terminal-active row (superseded_by IS NULL AND
    # retired_at IS NULL).  Returns None for tombstones (terminal with
    # retired_at IS NOT NULL) per Q1 boundary, AND for missing start ids
    # (recursive CTE returns zero rows, MAX(hops) is None above and we
    # fall through to here).
    active_query = """
        WITH RECURSIVE chain AS (
            SELECT id, superseded_by, retired_at, 0 AS hops
              FROM canonical_events
             WHERE id = %s
            UNION ALL
            SELECT ce.id, ce.superseded_by, ce.retired_at, c.hops + 1
              FROM canonical_events ce
              JOIN chain c ON ce.id = c.superseded_by
             WHERE c.hops < 100
        )
        SELECT ce.id, ce.event_domain_id, ce.event_type_id,
               ce.participants_sorted, ce.resolution_window,
               ce.resolution_rule_fp, ce.natural_key_hash, ce.title,
               ce.description, ce.lifecycle_phase, ce.metadata,
               ce.created_at, ce.updated_at, ce.retired_at,
               ce.superseded_by
          FROM canonical_events ce
          JOIN chain c ON ce.id = c.id
         WHERE c.superseded_by IS NULL
           AND c.retired_at IS NULL
         LIMIT 1
    """
    return fetch_one(active_query, (canonical_event_id,))


def _retirement_chain_includes(start_id: int, target_id: int) -> bool:
    """
    Internal helper: does the superseded_by chain starting at ``start_id``
    include ``target_id`` anywhere?

    Layer (b) of the 3-layer cycle defense (per Q2 adjudication; see
    ``get_active_canonical_event`` docstring).  Used by
    ``retire_canonical_event(superseded_by_id=...)`` BEFORE writing the
    new supersession link, to verify that linking ``superseded_by_id``
    into the chain headed by ``target_id`` (the row being retired) does
    NOT introduce a cycle.

    Defensive: capped at 100 hops to mirror
    ``get_active_canonical_event``'s read-side guard; if the chain
    exceeds 100 hops the helper conservatively returns ``True`` (treats
    deep chains as suspect cycle indicators), causing the calling
    ``retire_canonical_event`` to reject the write with ValueError.
    Production chains are not 100 deep; this is a safety boundary, not a
    legitimate path.

    Pattern 73 SSOT: the recursive-CTE chain-walk SQL is encapsulated
    here (and in ``get_active_canonical_event`` -- the two helpers are
    siblings sharing the chain-walk idiom).  No external caller writes
    inline ``superseded_by`` traversal SQL.

    Args:
        start_id: id whose chain to walk forward via ``superseded_by``.
        target_id: id to search for in the walked chain.

    Returns:
        ``True`` if ``target_id`` appears anywhere in the chain starting
        at ``start_id`` (including ``start_id`` itself), OR if the chain
        exceeds 100 hops (defensive cycle-suspect treatment).
        ``False`` if the chain terminates within 100 hops without
        encountering ``target_id``.
    """
    query = """
        WITH RECURSIVE chain AS (
            SELECT id, superseded_by, 0 AS hops
              FROM canonical_events
             WHERE id = %s
            UNION ALL
            SELECT ce.id, ce.superseded_by, c.hops + 1
              FROM canonical_events ce
              JOIN chain c ON ce.id = c.superseded_by
             WHERE c.hops < 100
        )
        SELECT
            BOOL_OR(id = %s) AS includes_target,
            MAX(hops) AS max_hops
          FROM chain
    """
    with get_cursor() as cur:
        cur.execute(query, (start_id, target_id))
        row = cur.fetchone()
    if row is None:
        # Start id does not exist -- chain is empty; cannot include target.
        return False
    if row.get("max_hops") is not None and row["max_hops"] >= 100:
        # Defensive: chain depth saturated -- treat as suspect cycle.
        return True
    return bool(row.get("includes_target"))


def retire_canonical_event(
    canonical_event_id: int,
    superseded_by_id: int | None = None,
) -> bool:
    """
    Retire a canonical_events row.

    Without ``superseded_by_id``: sets ``retired_at = now()`` only --
    terminal tombstone (retired without replacement).  Backward-compatible
    with the pre-Migration-0087 signature.

    With ``superseded_by_id``: validates that linking ``superseded_by_id``
    into the chain does NOT introduce a cycle (layer (b) of the 3-layer
    cycle defense per Q2 adjudication), then writes BOTH ``retired_at =
    now()`` AND ``superseded_by = :superseded_by_id`` in a SINGLE atomic
    UPDATE.  This is the canonical write surface for "this canonical row
    is being replaced by that one".

    Canonical-tier retirement is for cases where the canonical identity
    itself is deprecated (e.g., this row duplicates an existing canonical
    event and should not be returned by future lookups).  It does NOT track
    per-platform tradability (that's platform ``markets.status``) nor event-
    matching state (that's ``canonical_events.lifecycle_phase``).

    Args:
        canonical_event_id: BIGSERIAL surrogate PK from
            ``canonical_events.id`` -- the row to retire.
        superseded_by_id: Optional BIGSERIAL surrogate PK from
            ``canonical_events.id`` -- the row that supersedes this one.
            When provided, the function validates that the chain from
            ``superseded_by_id`` does NOT lead back to
            ``canonical_event_id`` (cycle prevention) and writes both
            columns atomically.  When None (default), the function
            preserves backward-compatible terminal-tombstone semantics.

    Returns:
        ``True`` if a row was retired (matched and updated), ``False`` if no
        row matched the given id.

    Raises:
        ValueError: If ``superseded_by_id`` is provided AND its chain
            leads back to ``canonical_event_id`` (cycle introduction
            blocked).  No row is updated when this fires; database state
            is unchanged.  Pattern 73 SSOT cycle prevention -- one write
            surface = one validation point.
        psycopg2.errors.ForeignKeyViolation: If ``superseded_by_id`` does
            not reference an existing ``canonical_events.id`` row.  The
            inline FK constraint (``canonical_events_superseded_by_fkey``,
            shipped Migration 0087) rejects the UPDATE at the DB level.
            The cycle-prevention chain-walk does not pre-validate
            existence (an empty chain trivially does not include the
            target); the FK does the existence enforcement.

    Example:
        >>> # Pre-Migration-0087 callstyle (still supported):
        >>> if retire_canonical_event(7):
        ...     print("Canonical event 7 retired (terminal tombstone)")
        ...
        >>> # Post-Migration-0087: retire-and-supersede:
        >>> if retire_canonical_event(7, superseded_by_id=42):
        ...     print("Canonical event 7 retired, superseded by 42")
        ...
        >>> # Cycle introduction is rejected:
        >>> # Suppose 42 -> 7 already (42 was previously retired into 7).
        >>> # Then retire(7, superseded_by_id=42) would create cycle 7 -> 42 -> 7.
        >>> try:
        ...     retire_canonical_event(7, superseded_by_id=42)
        ... except ValueError as e:
        ...     print(f"Cycle rejected: {e}")

    Educational Note:
        ``retired_at`` follows the append-then-retire model used elsewhere in
        the canonical tier (canonical_events is NOT SCD-2; no
        ``row_current_ind``, no version chain).  Per ADR-118 V2.38 / Cohort 2
        amendment Holden Finding 9 (the same posture for canonical_markets):
        this is a deliberate divergence from the SCD-2 patterns used on
        platform-tier tables (Patterns 18, 80) -- the canonical tier is
        identity, not history.

        ``canonical_events`` carries the ``trg_canonical_events_updated_at``
        BEFORE UPDATE trigger (shipped in Migration 0076 -- generic
        ``set_updated_at()`` retrofit per ADR-118 V2.42 sub-amendment A).
        This function therefore writes ``retired_at = now()`` (and
        optionally ``superseded_by``) ONLY; ``updated_at`` refreshes
        automatically via the trigger.  Pattern 73 SSOT compliance: the
        trigger is the canonical source for ``updated_at`` semantics;
        this module relies on it.

        Atomicity: the ``superseded_by_id`` extension writes BOTH columns
        in a SINGLE UPDATE statement (NOT two separate UPDATEs).  This
        matters because the schema invariant "superseded rows have
        retired_at set" must hold at every moment, including the moment
        between two hypothetical separate UPDATEs.  An external observer
        reading the row mid-transaction would see either the pre-
        retirement state (both NULL) or the post-retirement state (both
        set) -- never the half-state (only one set).

        This function is idempotent in effect for the unsuperseded path:
        retiring an already-retired row simply refreshes ``retired_at``
        to the current timestamp.  For the superseded path the same
        idempotency applies, AND the cycle check is conservative on
        re-retirement (re-supersession to the same superseded_by_id is
        idempotent; re-supersession to a different id that would create
        a cycle is rejected).

    Reference:
        - Migration 0067 (table DDL)
        - Migration 0076 (generic ``set_updated_at()`` BEFORE UPDATE
          trigger retrofit per ADR-118 V2.42 sub-amendment A)
        - Migration 0087 (cleanup epic Slot 3 -- adds superseded_by self-FK
          + canonical_events_no_self_supersession single-row CHECK)
        - ``get_active_canonical_event`` (read-side SSOT; layer (c) of
          the 3-layer cycle defense)
        - ``_retirement_chain_includes`` (write-side cycle check helper;
          layer (b))
        - ``crud_canonical_markets.retire_canonical_market`` (sibling
          retire-tier pattern)
    """
    if superseded_by_id is not None:
        # Layer (b) cycle prevention: walk the chain from superseded_by_id
        # and verify it does not lead back to canonical_event_id.  If it
        # does, linking superseded_by_id as canonical_event_id's
        # successor would create a cycle (canonical_event_id ->
        # superseded_by_id -> ... -> canonical_event_id) -- reject.
        if _retirement_chain_includes(superseded_by_id, canonical_event_id):
            raise ValueError(
                f"retire_canonical_event: superseded_by_id={superseded_by_id} "
                f"chain leads back to canonical_event_id={canonical_event_id}, "
                f"cycle rejected"
            )

        # Single atomic UPDATE writing BOTH columns.  Pattern 73 SSOT:
        # one statement, one transaction-visible state-transition --
        # mid-transaction observers see either both-set or both-unset,
        # never the half-state.
        query = """
            UPDATE canonical_events
            SET retired_at = now(),
                superseded_by = %s
            WHERE id = %s
        """
        params: tuple[Any, ...] = (superseded_by_id, canonical_event_id)
    else:
        # Backward-compatible path: preserve pre-Migration-0087 terminal-
        # tombstone semantics.  superseded_by remains NULL.
        query = """
            UPDATE canonical_events
            SET retired_at = now()
            WHERE id = %s
        """
        params = (canonical_event_id,)

    with get_cursor(commit=True) as cur:
        cur.execute(query, params)
        return cast("bool", cur.rowcount > 0)


# =============================================================================
# CANONICAL EVENT DOMAINS RESOLVER (read-only helper)
# =============================================================================


def get_canonical_event_domain_id_by_domain(domain: str) -> int | None:
    """
    Resolve a ``canonical_event_domains.domain`` text -> id (read-only).

    The 7 seeded domains (sports, politics, weather, econ, news,
    entertainment, fighting) are Pattern 81 instances (open canonical enum
    -> lookup table).  Callers constructing canonical_events rows MUST
    resolve the human-readable domain string to its integer FK before
    INSERT; this helper centralizes that resolution to avoid hardcoded
    integer literals across consumers (Pattern 73 SSOT).

    Args:
        domain: Human-readable domain string ('sports', 'politics',
            'weather', 'econ', 'news', 'entertainment', 'fighting').
            Case-sensitive (matches the seed text exactly).

    Returns:
        Integer ``canonical_event_domains.id`` if the domain is seeded,
        ``None`` if no row matches the given domain text.

    Example:
        >>> domain_id = get_canonical_event_domain_id_by_domain("sports")
        >>> if domain_id is None:
        ...     raise RuntimeError("canonical_event_domains seed missing 'sports'")
        >>> # ... use domain_id when calling create_canonical_event()

    Educational Note:
        This helper is a thin wrapper around a single-row SELECT, returning
        ``None`` (not raising) for unknown domains.  Pattern 81 lookup tables
        are intended to be extended by INSERT, so callers may legitimately
        encounter a domain that hasn't been seeded yet (in which case the
        right path is to fail loudly with a domain-specific error message,
        not blow up on an unhandled exception inside this helper).

        Mirrors the ``get_canonical_entity_kind_id_by_kind`` shape from
        ``crud_canonical_entity.py`` -- read-only resolver, ``None`` on miss,
        no caching at this layer (Pattern 81 lookups are small enough that
        a per-call DB hit is fine; if hot-path consumers emerge, a cache
        layer can land separately mirroring ``crud_lookups.py``'s sport/
        league cache shape).

    Reference:
        - Migration 0067 (canonical_event_domains DDL + 7-row seed)
        - ADR-118 V2.38 decision #2 (Pattern 81 lookup table for domains)
        - DEVELOPMENT_PATTERNS V1.39 Pattern 81
    """
    query = """
        SELECT id
        FROM canonical_event_domains
        WHERE domain = %s
    """
    row = fetch_one(query, (domain,))
    return row["id"] if row is not None else None


# =============================================================================
# CANONICAL EVENT TYPES RESOLVER (read-only helper)
# =============================================================================


def get_canonical_event_type_id_by_domain_and_type(
    domain_id: int,
    event_type: str,
) -> int | None:
    """
    Resolve a ``canonical_event_types`` row by ``(domain_id, event_type)`` ->
    id (read-only).

    The ~13 per-domain seeded event_types (sports.game/match,
    politics.election/debate/referendum, weather.storm_track/temperature_range,
    econ.earnings_release/rate_decision, news.pandemic_case/conflict_outcome,
    entertainment.award_winner/box_office_result) are Pattern 81 instances
    (open canonical enum -> lookup table).  The natural identity is the
    composite ``(domain_id, event_type)`` (constraint
    ``uq_canonical_event_types_domain_type`` -- Migration 0067) because
    event_type strings can repeat across domains in principle (currently
    they do not, but the schema admits it).  Callers constructing
    canonical_events rows MUST resolve the (domain, event_type) text
    composite to its integer FK before INSERT; this helper centralizes
    that resolution to avoid hardcoded integer literals across consumers
    (Pattern 73 SSOT).

    Args:
        domain_id: Integer FK from ``canonical_event_domains.id``.  Use
            ``get_canonical_event_domain_id_by_domain()`` to resolve from
            the human-readable domain string first.
        event_type: Human-readable event_type string ('game', 'match',
            'election', 'storm_track', ...).  Case-sensitive (matches
            the seed text exactly).

    Returns:
        Integer ``canonical_event_types.id`` if the (domain_id, event_type)
        pair is seeded, ``None`` if no row matches.

    Example:
        >>> domain_id = get_canonical_event_domain_id_by_domain("sports")
        >>> event_type_id = get_canonical_event_type_id_by_domain_and_type(
        ...     domain_id, "game"
        ... )
        >>> if event_type_id is None:
        ...     raise RuntimeError(
        ...         "canonical_event_types seed missing (sports, game)"
        ...     )
        >>> # ... use event_type_id when calling create_canonical_event()

    Educational Note:
        ``(domain_id, event_type)`` is the UNIQUE composite natural identity
        (constraint ``uq_canonical_event_types_domain_type``), so this query
        returns at most one row.  The composite UNIQUE index makes the
        lookup O(log n) regardless of table size.

        Mirrors the ``get_canonical_entity_by_kind_and_key`` shape from
        ``crud_canonical_entity.py`` (composite-natural-key lookup) but
        returns just the resolved id (not the full row), matching the
        shape of ``get_canonical_entity_kind_id_by_kind`` (single-id
        resolver).  This is the canonical resolver shape for Pattern 81
        lookups whose natural key is a composite.

    Reference:
        - Migration 0067 (canonical_event_types DDL + per-domain seed)
        - ADR-118 V2.38 decision #3 (Pattern 81 lookup table for
          event_types)
        - DEVELOPMENT_PATTERNS V1.39 Pattern 81
    """
    query = """
        SELECT id
        FROM canonical_event_types
        WHERE domain_id = %s AND event_type = %s
    """
    row = fetch_one(query, (domain_id, event_type))
    return row["id"] if row is not None else None
