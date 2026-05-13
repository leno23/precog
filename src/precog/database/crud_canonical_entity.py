"""CRUD operations for canonical_entities (+ canonical_entity_kinds resolver).

Cohort 1B Pattern 14 retro (issue #1021 Slice B) -- the canonical-entity tier
is the second concrete implementation of the "Level B" canonical identity
layer from ADR-118 V2.40.  Sister module to ``crud_canonical_markets.py``;
mirrors that module's raw-psycopg2 + ``get_cursor`` / ``fetch_one`` +
RealDictCursor + heavy-docstring conventions verbatim.

Cleanup epic Slot 1 (Migration 0085, V2.47 ADR amendment) renamed
``canonical_entity`` to ``canonical_entities`` for naming-convention parity
with sibling collection tables (``canonical_events``,
``canonical_event_participants``, ``canonical_markets``).  Module file name
``crud_canonical_entity.py`` is unchanged in this slot per PM Picard
adjudication; file rename is a separate cosmetic-cleanup concern.

Cleanup epic Slot 2 (Migration 0086) reverses the FK direction between
``teams`` and ``canonical_entities``.  Pre-Slot-2 the canonical-entity tier
carried a ``ref_team_id`` typed back-ref into ``teams`` (the Pattern 82
canonical instance).  Post-Slot-2 the FK lives on ``teams.canonical_entity_id``
pointing at ``canonical_entities(id)`` ON DELETE SET NULL -- the normalized
direction (each platform team optionally references its canonical identity).
The pre-Slot-2 ``trg_canonical_entity_team_backref`` polymorphic enforcement
trigger + its underlying function are dropped at Slot 2; the Pattern 82 V2
"forward-only direction policy" rule retires for canonical_entities (see
forward-pointer note below).  Pattern 82 V2 SCOPE NARROWING in V2.47:
Pattern 82 V2 applies to ``canonical_markets`` only post-cleanup-epic
Slot 2; the canonical_entities-team variant retires via Migration 0086.
Test deletion at Slot 2 (``tests/database/test_canonical_entity_polymorphic
_invariants.py`` removed); formal V2.40-pin retirement + Pattern 82 V2
scope-narrowing codified at V2.47 ADR amendment (Slot 5 / session 99).

Tables covered:
    - ``canonical_entities`` (Migration 0068, renamed Migration 0085, FK
      direction flipped Migration 0086) -- the canonical (platform-agnostic)
      polymorphic entity row.  Discriminated by ``entity_kind_id`` ->
      ``canonical_entity_kinds`` (Pattern 81 lookup).  Post-Slot-2 column
      shape: id + entity_kind_id + entity_key + display_name + metadata
      + created_at (no ref_team_id; the typed back-ref retired with
      Migration 0086).  Platform teams reference their canonical identity
      via ``teams.canonical_entity_id`` (added Migration 0086).
    - ``canonical_entity_kinds`` (lookup, Migration 0068) -- read-only
      resolver helper ``get_canonical_entity_kind_id_by_kind()`` only.

Pattern 14 5-step bundle status:
    This module is **step 3 of 5** for Slice B of the Cohort 1B retro
    (issue #1021):
        - step 1 = Migration 0068 (already shipped session 71-72 PR #1005);
        - step 2 (SQLAlchemy ORM model) is **N/A** because Precog uses raw
          psycopg2 only (no SQLAlchemy ORM despite CLAUDE.md's Tech Stack
          line) -- mirrors the Cohort 2 ``crud_canonical_markets`` deferral;
        - step 3 = this module;
        - step 4 = ``tests/unit/database/test_crud_canonical_entity_unit.py``;
        - step 5 (integration tests) is partially covered by
          ``tests/integration/database/test_migration_0068_canonical_entity_foundation.py``
          (#1012, session 75) PLUS the load-bearing
          ``tests/database/test_canonical_entity_polymorphic_invariants.py``
          (this slice).  Cleanup epic Slot 2 / Migration 0086 retires
          this load-bearing test (deletion at Slot 2; formal pin retirement
          codified at V2.47 / Slot 5 / session 99).

UPDATE / RETIRE coverage (Slice B deliberate gap, mirrors Cohort 2 Glokta
Finding 10 deferral):
    This module ships ``create_canonical_entity`` + lookup helpers ONLY.
    No ``update_canonical_entity()`` and no ``retire_canonical_entity()``
    are exposed because ``canonical_entity`` was migrated WITHOUT
    ``updated_at`` and WITHOUT ``retired_at`` columns (verified via MCP
    against migration 0068 -- see column list in this module's
    ``create_canonical_entity`` docstring).  Any future write surface
    beyond INSERT lands together with:
        - the BEFORE UPDATE trigger retrofit tracked in #1007 (which adds
          a generic ``set_updated_at()`` BEFORE UPDATE trigger, per the
          #1018 claude-review hint), AND
        - whatever lifecycle policy is decided for the canonical-entity
          tier (Cohort 5 / matcher slot ~session 101 seed context).
    Until then, callers that need UPDATE coverage must NOT write ad-hoc
    UPDATE SQL (Pattern 73 violation -- drift across consumers); file an
    issue or add the helper here first.

Slice B scope (this module) -- exactly these tables:
    - ``canonical_entities`` (CRUD: create + 2 lookups);
    - ``canonical_entity_kinds`` (read-only resolver helper);
    - NOT covered (deferred to a separate PR under #1021):
        * ``canonical_events`` -- crud_canonical_events.py
        * ``canonical_event_participants`` -- crud_canonical_event_participants.py
        * ``canonical_participant_roles`` + ``canonical_event_domains`` +
          ``canonical_event_types`` -- read-only helpers in crud_lookups.py

Reference:
    - ``docs/foundation/ARCHITECTURE_DECISIONS.md`` ADR-118 V2.40 Item 4
      (Pattern 82 V2 forward-only ratification with inline forward-pointer
      to V2.47 retirement / Slot 5 / session 99)
    - ``docs/guides/DEVELOPMENT_PATTERNS.md`` Pattern 82 V2 (with inline
      forward-pointer to V2.47 scope-narrowing -- post-Slot-2 the rule
      applies to canonical_markets only)
    - ``src/precog/database/alembic/versions/0068_canonical_entity_foundation.py``
      (original DDL; pre-Slot-2 carried ref_team_id + trigger -- Pattern 87
      immutable, the historical text accurately describes the schema state
      at slot 0068's ship time)
    - ``src/precog/database/alembic/versions/0086_canonical_fk_direction_flip.py``
      (Slot 2 -- adds teams.canonical_entity_id, drops ref_team_id +
      trigger + function)
    - ``src/precog/database/crud_canonical_markets.py`` (style reference --
      Cohort 2 sibling template, mirrored line-for-line)
"""

import json
from typing import Any

from .connection import fetch_one, get_cursor

# =============================================================================
# CANONICAL ENTITY OPERATIONS
# =============================================================================


def create_canonical_entity(
    entity_kind_id: int,
    entity_key: str,
    display_name: str,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """
    Create a new canonical_entities row.

    Canonical entities are the platform-agnostic identity tier for individual
    real-world participants in canonical events (teams, fighters, candidates,
    storms, ...).  ``entity_kind_id`` is the Pattern 81 discriminator FK into
    ``canonical_entity_kinds`` (12 seeded kinds; new kinds extend by INSERT,
    not ALTER TABLE).

    Cleanup epic Slot 2 (Migration 0086) flipped the FK direction between
    ``teams`` and ``canonical_entities``.  Pre-Slot-2 this function accepted
    a ``ref_team_id`` typed back-ref parameter; post-Slot-2 the platform
    team references its canonical identity via ``teams.canonical_entity_id``
    (added Migration 0086) and this function no longer carries the
    ref_team_id parameter.  The pre-Slot-2 polymorphic enforcement trigger
    ``trg_canonical_entity_team_backref`` is dropped at Slot 2; Pattern 82
    V2 forward-only direction policy retires for canonical_entities (formal
    scope-narrowing codified at V2.47 ADR amendment / Slot 5 / session 99).

    Args:
        entity_kind_id: Integer FK into ``canonical_entity_kinds.id``.
            Use ``get_canonical_entity_kind_id_by_kind()`` to resolve from
            the human-readable kind string ('team', 'fighter', ...).  ON
            DELETE RESTRICT on the FK -- entity_kinds outlive any single
            entity row.
        entity_key: Stable business identifier for the entity within its
            kind (e.g., the team external_id, the fighter slug).
            Composite UNIQUE with ``entity_kind_id`` via
            ``uq_canonical_entity_kind_key``; duplicate (kind_id, key) pairs
            raise ``psycopg2.IntegrityError``.
        display_name: Human-readable display label (e.g., "Buffalo Bills",
            "Conor McGregor").  NOT NULL.
        metadata: Optional JSONB dict.  Serialized via ``json.dumps``
            (mirrors the ``crud_canonical_markets.create_canonical_market``
            and ``crud_platform_events.create_event`` metadata convention).

    Returns:
        Full row dict of the created canonical entity.  Keys:
            id, entity_kind_id, entity_key, display_name, metadata, created_at

    Raises:
        psycopg2.IntegrityError: If ``(entity_kind_id, entity_key)`` already
            exists (UNIQUE violation), or ``entity_kind_id`` does not
            reference a real ``canonical_entity_kinds`` row.

    Example:
        >>> # Resolve the entity_kind_id once and reuse:
        >>> team_kind_id = get_canonical_entity_kind_id_by_kind("team")
        >>> # Create a team entity:
        >>> row = create_canonical_entity(
        ...     entity_kind_id=team_kind_id,
        ...     entity_key="BUF-NFL-001",
        ...     display_name="Buffalo Bills",
        ... )
        >>> row["id"]  # BIGSERIAL surrogate PK
        7
        >>> # Platform team references its canonical identity via
        >>> # teams.canonical_entity_id = row["id"] (Cohort 5+ matcher
        >>> # slot ~session 101 wires this).

    Educational Note:
        Why no ``updated_at`` / ``retired_at`` column?  Per ADR-118 V2.38
        the canonical-entity tier was scoped to identity-creation only in
        Cohort 1B; lifecycle surfaces (UPDATE / RETIRE) defer to a future
        cohort with a dedicated trigger retrofit (#1007 -- generic
        ``set_updated_at()`` BEFORE UPDATE trigger).  Until then, this
        CRUD module exposes INSERT only.

    Reference:
        - Migration 0068 (table DDL; pre-Slot-2 shape with ref_team_id)
        - Migration 0085 (cleanup epic Slot 1: canonical_entity ->
          canonical_entities table rename)
        - Migration 0086 (cleanup epic Slot 2: FK direction flip + DROP
          ref_team_id column + DROP polymorphic enforcement trigger)
        - ADR-118 V2.38 decisions #1, #5; V2.40 amendment Item 4 (with
          inline forward-pointer to V2.47 retirement at Slot 5 / session 99)
    """
    query = """
        INSERT INTO canonical_entities (
            entity_kind_id, entity_key, display_name, metadata
        )
        VALUES (%s, %s, %s, %s)
        RETURNING id, entity_kind_id, entity_key, display_name,
                  metadata, created_at
    """

    params = (
        entity_kind_id,
        entity_key,
        display_name,
        json.dumps(metadata) if metadata is not None else None,
    )

    with get_cursor(commit=True) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row)


def get_canonical_entity_by_id(canonical_entity_id: int) -> dict[str, Any] | None:
    """
    Get a canonical_entities row by its surrogate integer PK.

    Args:
        canonical_entity_id: BIGSERIAL surrogate PK from ``canonical_entities.id``.

    Returns:
        Full row dict if found, ``None`` otherwise.  Keys:
            id, entity_kind_id, entity_key, display_name, metadata, created_at

    Example:
        >>> row = get_canonical_entity_by_id(7)
        >>> if row:
        ...     print(row["display_name"])  # 'Buffalo Bills'

    Educational Note:
        Lookup by surrogate PK is the cheapest path (single B-tree probe on
        the primary key).  For lookup by canonical (kind, key) natural
        identity, use ``get_canonical_entity_by_kind_and_key()`` which hits
        the ``uq_canonical_entity_kind_key`` UNIQUE composite index.

    Reference:
        - Migration 0068 (table DDL)
        - Migration 0086 (FK direction flip + denorm collapse; ref_team_id
          dropped from column inventory)
        - ``crud_canonical_markets.get_canonical_market_by_id`` (sibling
          lookup-by-PK pattern)
    """
    query = """
        SELECT id, entity_kind_id, entity_key, display_name,
               metadata, created_at
        FROM canonical_entities
        WHERE id = %s
    """
    return fetch_one(query, (canonical_entity_id,))


def get_canonical_entity_by_kind_and_key(
    entity_kind_id: int,
    entity_key: str,
) -> dict[str, Any] | None:
    """
    Get a canonical_entities row by its (entity_kind_id, entity_key) natural key.

    This is the canonical lookup for "do we already have a canonical entity
    for this (kind, key) tuple?"  ``(entity_kind_id, entity_key)`` is the
    UNIQUE natural composite key on ``canonical_entities`` (constraint
    ``uq_canonical_entity_kind_key`` -- Migration 0068, table renamed
    Migration 0085).  A hit means
    "canonical identity already exists, reuse it"; a miss means "new
    canonical identity, the caller should create it".

    Args:
        entity_kind_id: Integer FK from ``canonical_entity_kinds.id``.
            Use ``get_canonical_entity_kind_id_by_kind()`` to resolve from
            the human-readable kind string.
        entity_key: Stable business identifier within the kind.

    Returns:
        Full row dict if found, ``None`` otherwise.  Same keys as
        ``get_canonical_entity_by_id``.

    Example:
        >>> team_kind_id = get_canonical_entity_kind_id_by_kind("team")
        >>> row = get_canonical_entity_by_kind_and_key(team_kind_id, "BUF-NFL-001")
        >>> if row is None:
        ...     print("New canonical identity -- caller should create it")
        ... else:
        ...     print(f"Existing canonical id={row['id']} found")

    Educational Note:
        ``(entity_kind_id, entity_key)`` is the UNIQUE composite natural
        identity (constraint ``uq_canonical_entity_kind_key``), so this
        query returns at most one row.  The composite UNIQUE index makes
        the lookup O(log n) regardless of table size.

        Note that the canonical-entity tier deliberately does NOT use
        ``natural_key_hash`` (the BYTEA hash convention from
        ``canonical_markets``) -- the ``(kind, key)`` text composite is the
        natural identity per ADR-118 V2.38.  Future cross-platform
        identity matching (Cohort 5+) may layer a hash on top via a
        derivation rule, but the canonical lookup remains on the composite.

    Reference:
        - Migration 0068 (table DDL -- ``uq_canonical_entity_kind_key``)
        - Migration 0086 (FK direction flip + denorm collapse)
        - ADR-118 V2.38 decision #1 (composite natural identity)
    """
    query = """
        SELECT id, entity_kind_id, entity_key, display_name,
               metadata, created_at
        FROM canonical_entities
        WHERE entity_kind_id = %s AND entity_key = %s
    """
    return fetch_one(query, (entity_kind_id, entity_key))


# =============================================================================
# CANONICAL ENTITY KINDS RESOLVER (read-only helper)
# =============================================================================


def get_canonical_entity_kind_id_by_kind(entity_kind: str) -> int | None:
    """
    Resolve a ``canonical_entity_kinds.entity_kind`` text -> id (read-only).

    The 12 seeded entity_kinds (team, fighter, candidate, storm, company,
    location, person, product, country, organization, commodity, media) are
    Pattern 81 instances (open canonical enum -> lookup table).  Callers
    constructing canonical_entities rows MUST resolve the human-readable kind
    string to its integer FK before INSERT; this helper centralizes that
    resolution to avoid hardcoded integer literals across consumers
    (Pattern 73 SSOT).

    Args:
        entity_kind: Human-readable kind string ('team', 'fighter', ...).
            Case-sensitive (matches the seed text exactly).

    Returns:
        Integer ``canonical_entity_kinds.id`` if the kind is seeded,
        ``None`` if no row matches the given kind text.

    Example:
        >>> team_kind_id = get_canonical_entity_kind_id_by_kind("team")
        >>> if team_kind_id is None:
        ...     raise RuntimeError("canonical_entity_kinds seed missing 'team'")
        >>> row = create_canonical_entity(
        ...     entity_kind_id=team_kind_id,
        ...     entity_key="BUF-NFL-001",
        ...     display_name="Buffalo Bills",
        ... )

    Educational Note:
        This helper is a thin wrapper around a single-row SELECT, returning
        ``None`` (not raising) for unknown kinds.  Pattern 81 lookup tables
        are intended to be extended by INSERT, so callers may legitimately
        encounter a kind that hasn't been seeded yet (in which case the
        right path is to fail loudly with a domain-specific error message,
        not blow up on an unhandled exception inside this helper).

        Future: when canonical_entity_kinds rows are FK-referenced en masse
        (e.g., bulk seeding flows), consider a batch helper that returns
        a {kind: id} dict in a single query.  Out of scope for Slice B.

    Reference:
        - Migration 0068 (canonical_entity_kinds DDL + 12-row seed)
        - ADR-118 V2.38 decision #1 (Pattern 81 lookup table for entity_kinds)
        - DEVELOPMENT_PATTERNS Pattern 81
    """
    query = """
        SELECT id
        FROM canonical_entity_kinds
        WHERE entity_kind = %s
    """
    row = fetch_one(query, (entity_kind,))
    return row["id"] if row is not None else None
