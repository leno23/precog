"""Cleanup epic Slot 1 -- canonical naming bundle (slot 0085).

Realizes the four naming-convention adjustments codified by the
session 94 cleanup-epic council synthesis (D4 + CL-5 + CL-6 + Q10) and
pre-codified for V2.47 ADR-118 amendment landing at Slot 5 (session 99):

    Surface 1 -- ALTER TABLE canonical_entity RENAME TO canonical_entities.
                 Canonical-side naming convention: collection tables
                 carry the plural form (canonical_events,
                 canonical_event_participants, canonical_markets, ...).
                 ``canonical_entity`` was the lone singular outlier from
                 Cohort 1A.

    Surface 2 -- ALTER TABLE canonical_events RENAME COLUMN domain_id
                 TO event_domain_id.  FK column naming rule application
                 (precedent: ADR-118 V2.42-B FK column convention --
                 ``<target_role>_id`` where ``target_role`` clarifies the
                 referent).  ``domain_id`` is ambiguous out of context;
                 ``event_domain_id`` makes the canonical-events-domain
                 referent explicit.

    Surface 3 -- ALTER TABLE canonical_events RENAME COLUMN
                 entities_sorted TO participants_sorted.  Clarity
                 rename: the ARRAY value is the sorted list of
                 *participant* identifiers (per
                 canonical_event_participants), not entities at large.
                 ``participants_sorted`` matches the participants
                 vocabulary used everywhere else in the canonical layer.

    Surface 4 -- ALTER TABLE canonical_event_participants RENAME COLUMN
                 entity_id TO canonical_entity_id (4a) +
                 ALTER TABLE canonical_event_participants RENAME
                 CONSTRAINT canonical_event_participants_entity_id_fkey
                 TO canonical_event_participants_canonical_entity_id_fkey
                 (4b).  FK column naming rule application: target table
                 is ``canonical_entities`` (post-rename), so the FK
                 column name should be ``canonical_entity_id``.  The PG
                 auto-derived FK constraint name embeds the column name,
                 so the explicit RENAME CONSTRAINT keeps the constraint
                 name aligned with the column name.

Pattern 87 (Append-only migration files) -- REAFFIRMED CLEAN:

    DEVELOPMENT_PATTERNS V1.40+.  Slot 0085 is a NEW migration file;
    Pattern 87 fires when editing PREVIOUSLY-MERGED migrations.  This PR
    makes ZERO edits to migrations 0001-0084.  In particular, slot 0068
    (canonical_entity foundation) is not edited -- the pre-rename names
    in its DDL and docstring reflect the schema state at slot 0068's
    ship time and are correct, not stale.  Future readers reconstructing
    schema from migrations alone replay 0068 -> ... -> 0085 in order,
    arriving at the post-0085 shape without ever editing the historical
    text.

Pattern 91 V1.44 MCP-first premise verification (Builder, build time):

    Pre-build MCP postgres-dev verification (alembic_head=0084):
        - canonical_entity exists; canonical_entities does NOT exist.
        - canonical_events.domain_id exists, integer NOT NULL.
        - canonical_events.entities_sorted exists, ARRAY NOT NULL.
        - canonical_event_participants.entity_id exists, BIGINT NOT NULL.
        - All 3 affected tables at 0 rows -- no data backfill needed.
        - Inbound FK to canonical_entity:
              canonical_event_participants_entity_id_fkey
              (canonical_event_participants.entity_id ->
              canonical_entity(id) ON DELETE RESTRICT).
              PG auto-rewrites the FK target on RENAME TABLE; the
              constraint definition at post-upgrade reads
              ``REFERENCES canonical_entities(id)`` automatically.
              The constraint NAME survives the table rename (it does
              not embed the table name -- it embeds the FK column name);
              we explicitly rename the constraint in surface 4b to
              align with the new column name.
        - Indexes referencing renamed columns
              (idx_canonical_events_domain_id,
              idx_canonical_event_participants_entity_id) auto-follow
              the column rename per PG semantics.  Index names stay --
              not renamed in this slot (would require explicit ALTER
              INDEX RENAME, which is out of scope for naming-bundle).

Pattern 84 (NOT VALID + VALIDATE on populated tables) -- N/A this slot:

    All 3 affected tables are empty (0 rows MCP-verified).  RENAME
    operations are PG-internal metadata-only mutations; no row rewrite
    or constraint-revalidation cost.

Round-trip discipline (per session 94 close OQ-H1 verification --
existing CI gate via tests/integration/migrations/test_round_trip.py):

    Slot 0085's downgrade() is a pure inverse of upgrade(): every
    RENAME has a matching reverse RENAME, in opposite order.  The
    constraint-name reverse rename precedes the column reverse rename
    so the constraint name is undone before the column it embeds is
    undone (mirror of upgrade order).  The round-trip CI gate
    auto-discovers slot 0085 via its discovery loop; no manual hookup
    needed.

Revision ID: 0085
Revises: 0084
Create Date: 2026-05-05

Issues: #1155 (canonical-layer simplification epic),
    Slot 1 of 5-slot cleanup epic per design_review_canonical_cleanup_synthesis.md
ADR: ADR-118 V2.47 amendment (lands Slot 5 / session 99 -- this slot
    ships the operations; Slot 5 codifies the rule)
Memo: build_spec_slot_1_naming_bundle_pm_memo.md (binding build spec)
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0085"
down_revision: str = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply 4 naming surfaces (1 table rename + 3 column renames + 1 FK constraint rename).

    Step order (independent renames; order chosen for readability,
    upgrade-downgrade symmetry, and constraint-rename-after-column-rename
    discipline):

        1. Surface 1 -- canonical_entity TABLE -> canonical_entities.
           Inbound FK target rewrites automatically (PG semantic).
        2. Surface 2 -- canonical_events.domain_id COLUMN ->
           event_domain_id.  Index idx_canonical_events_domain_id
           auto-follows the rename (column reference in index def
           rewrites; index name unchanged).
        3. Surface 3 -- canonical_events.entities_sorted COLUMN ->
           participants_sorted.  No indexes reference this column.
        4. Surface 4a -- canonical_event_participants.entity_id COLUMN
           -> canonical_entity_id.  Index
           idx_canonical_event_participants_entity_id auto-follows the
           rename (column reference in index def rewrites; index name
           unchanged).  FK constraint
           canonical_event_participants_entity_id_fkey survives
           structurally (PG does not auto-rename constraints when their
           referencing column is renamed).
        5. Surface 4b -- RENAME CONSTRAINT
           canonical_event_participants_entity_id_fkey ->
           canonical_event_participants_canonical_entity_id_fkey for
           naming-convention alignment with the new column name.

    All 5 ALTER statements run in a single transaction (Alembic default).
    No data backfill (all 3 affected tables verified at 0 rows MCP).
    """
    # =========================================================================
    # Surface 1: canonical_entity TABLE -> canonical_entities
    #
    # PG auto-rewrites inbound FK target references on RENAME TABLE.
    # Verified inbound FK: canonical_event_participants.entity_id ->
    # canonical_entity(id).  Post-rename, the FK definition reads
    # REFERENCES canonical_entities(id) automatically.
    #
    # The 4 indexes on canonical_entity (canonical_entity_pkey,
    # idx_canonical_entity_entity_kind_id, idx_canonical_entity_ref_team_id,
    # uq_canonical_entity_kind_key) move with the table.  Index names
    # carry the old "canonical_entity" prefix; renaming them is out of
    # scope for this naming-bundle (would require 4 explicit ALTER
    # INDEX RENAME statements; deferred to a future cosmetic slot if
    # pursued).
    # =========================================================================
    op.rename_table("canonical_entity", "canonical_entities")

    # =========================================================================
    # Surface 2: canonical_events.domain_id -> event_domain_id
    #
    # Index idx_canonical_events_domain_id auto-follows the rename
    # (column reference in index def rewrites; index name unchanged --
    # carries old "domain_id" suffix).  Renaming the index is deferred
    # to a future cosmetic slot if pursued.
    # =========================================================================
    op.alter_column(
        "canonical_events",
        "domain_id",
        new_column_name="event_domain_id",
    )

    # =========================================================================
    # Surface 3: canonical_events.entities_sorted -> participants_sorted
    #
    # No indexes reference this column (MCP-verified).  No FK
    # constraints reference this column.  Pure metadata-only rename.
    # =========================================================================
    op.alter_column(
        "canonical_events",
        "entities_sorted",
        new_column_name="participants_sorted",
    )

    # =========================================================================
    # Surface 4a: canonical_event_participants.entity_id -> canonical_entity_id
    #
    # Index idx_canonical_event_participants_entity_id auto-follows the
    # rename (column reference in index def rewrites; index name
    # unchanged -- carries old "entity_id" suffix).  Renaming the index
    # is deferred to a future cosmetic slot if pursued.
    #
    # The FK constraint canonical_event_participants_entity_id_fkey
    # survives structurally: PG does NOT auto-rename constraints when
    # the column they reference is renamed.  Surface 4b explicitly
    # renames the constraint to align with the new column name.
    # =========================================================================
    op.alter_column(
        "canonical_event_participants",
        "entity_id",
        new_column_name="canonical_entity_id",
    )

    # =========================================================================
    # Surface 4b: FK constraint name update (clarity; old name no longer
    # matches column it references)
    #
    # PG-side metadata-only constraint rename.  Constraint definition is
    # untouched (still REFERENCES canonical_entities(id) ON DELETE
    # RESTRICT post-Surface-1 auto-rewrite).
    # =========================================================================
    op.execute(
        "ALTER TABLE canonical_event_participants "
        "RENAME CONSTRAINT canonical_event_participants_entity_id_fkey "
        "TO canonical_event_participants_canonical_entity_id_fkey"
    )


def downgrade() -> None:
    """Reverse 0085: restore pre-rename canonical-layer naming.

    Step order (reverse-dependency-respecting; mirrors upgrade in
    opposite order; constraint reverse-rename precedes column
    reverse-rename so the constraint name is undone before the column
    it embeds is undone):

        1. Surface 4b reverse -- RENAME CONSTRAINT
           canonical_event_participants_canonical_entity_id_fkey ->
           canonical_event_participants_entity_id_fkey.
        2. Surface 4a reverse -- canonical_event_participants.canonical_entity_id
           -> entity_id.
        3. Surface 3 reverse -- canonical_events.participants_sorted
           -> entities_sorted.
        4. Surface 2 reverse -- canonical_events.event_domain_id
           -> domain_id.
        5. Surface 1 reverse -- canonical_entities TABLE
           -> canonical_entity.

    Round-trip parity: pre-0085 upgrade schema state == post-0085
    downgrade schema state (verified by
    tests/integration/migrations/test_round_trip.py).
    """
    # =========================================================================
    # Surface 4b reverse: constraint rename undo
    # =========================================================================
    op.execute(
        "ALTER TABLE canonical_event_participants "
        "RENAME CONSTRAINT canonical_event_participants_canonical_entity_id_fkey "
        "TO canonical_event_participants_entity_id_fkey"
    )

    # =========================================================================
    # Surface 4a reverse: canonical_entity_id -> entity_id
    # =========================================================================
    op.alter_column(
        "canonical_event_participants",
        "canonical_entity_id",
        new_column_name="entity_id",
    )

    # =========================================================================
    # Surface 3 reverse: participants_sorted -> entities_sorted
    # =========================================================================
    op.alter_column(
        "canonical_events",
        "participants_sorted",
        new_column_name="entities_sorted",
    )

    # =========================================================================
    # Surface 2 reverse: event_domain_id -> domain_id
    # =========================================================================
    op.alter_column(
        "canonical_events",
        "event_domain_id",
        new_column_name="domain_id",
    )

    # =========================================================================
    # Surface 1 reverse: canonical_entities -> canonical_entity
    #
    # Inbound FK target reference auto-rewrites back to
    # canonical_entity(id) per PG semantic.
    # =========================================================================
    op.rename_table("canonical_entities", "canonical_entity")
