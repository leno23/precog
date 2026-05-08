"""Cleanup epic Slot 2 -- canonical FK direction flip + denorm collapse (slot 0086).

Realizes CL-1 (FK direction flip) + CL-2 (denorm collapse) from the session 94
cleanup-epic council synthesis.  ADR-118 V2.47 amendment will codify the design
rationale this slot enacts; V2.47 lands at Slot 5 / session 99 per the 5-slot
cleanup epic plan (this slot ships the operations, Slot 5 ships the rule).

Source: ``memory/build_spec_slot_2_fk_direction_pm_memo.md`` § 2 (binding DDL)
+ ``memory/design_review_canonical_cleanup_synthesis.md`` § 4 Slot 2 row + § 8
one-line spec.  PM Picard adjudications Q1-Q4 (session 96).

Nine schema-mutation surfaces in upgrade(), in a single transaction:

    1. ADD COLUMN ``teams.canonical_entity_id BIGINT NULL`` (1,034 existing
       rows start NULL; populated by Cohort 5+ matcher slot ~session 101).
    2. ADD CONSTRAINT FK NOT VALID ``teams_canonical_entity_id_fkey`` ->
       ``canonical_entities(id)`` ON DELETE SET NULL.
    3. VALIDATE CONSTRAINT (no-op against empty target; precedent-consistency
       not safety -- see Pattern 84 framing below).
    4. DROP TRIGGER ``trg_canonical_entity_team_backref`` ON
       ``canonical_entities`` (hard ordering invariant: trigger references
       ref_team_id column body; DROP TRIGGER MUST precede DROP COLUMN).
    5. DROP FUNCTION ``enforce_canonical_entity_team_backref()`` (sentinel
       cleanup -- DROP TRIGGER does not auto-DROP the underlying function;
       leaves orphan in pg_proc otherwise).
    6. DROP CONSTRAINT ``canonical_entity_ref_team_id_fkey`` ON
       ``canonical_entities`` (precedes DROP COLUMN).
    7. DROP COLUMN ``canonical_entities.ref_team_id`` (after trigger + FK).
    8. DROP COLUMN ``canonical_events.game_id`` (empty table; trivial; FK to
       games and any associated index drop with the column).
    9. DROP COLUMN ``canonical_events.series_id`` (empty table; trivial;
       same shape as game_id).

  (Steps 4+5 are conceptually one "drop the polymorphic enforcement
   apparatus" carve-out; numbered separately because they are two distinct
   PG operations in sequence.)

Hard ordering invariant rationale:
    The trigger function ``enforce_canonical_entity_team_backref()`` body
    references ``NEW.ref_team_id`` directly.  PG cannot drop the column
    while the trigger function still references it (the trigger body is
    materialized at CREATE FUNCTION time and cached -- DROP COLUMN with a
    referencing trigger errors with "column is referenced in trigger
    function").  Thus DROP TRIGGER + DROP FUNCTION MUST precede DROP COLUMN.
    All 9 statements run in a single transaction (Alembic default); within
    the transaction PG enforces this ordering; out of order fails fast.

Pattern 87 (Append-only migration files) -- REAFFIRMED CLEAN:
    DEVELOPMENT_PATTERNS V1.40+.  This file is immutable post-merge.  Slot
    0086 is a NEW migration file; Pattern 87 fires when editing
    PREVIOUSLY-MERGED migrations.  This PR makes ZERO edits to migrations
    0001-0085.  In particular, slot 0068 (canonical_entity foundation) is
    not edited -- the pre-Slot-2 names + DDL in its body reflect the
    schema state at slot 0068's ship time and are correct, not stale.
    Slot 0086's downgrade() reproduces the trigger function body verbatim
    from Migration 0068 lines 271-291 (Pattern 87: 0068 is canonical for
    the trigger DDL).  Future readers reconstructing schema from migrations
    alone replay 0068 -> ... -> 0085 -> 0086 in order, arriving at the
    post-0086 shape without ever editing the historical text.

Pattern 84 V1.42 (NOT VALID + VALIDATE for FK by-analogy):
    Applied for **precedent-consistency (style choice, not safety)**.
    This is the **3rd by-analogy use** after slots 0080 (Cohort 4
    game_states + games -> canonical_events FK on populated tables, where
    Pattern 84 was load-bearing) and 0082 (Cohort 4 temporal_alignment ->
    canonical_events FK, by-analogy 2nd use that triggered Pattern 84
    promotion to V1.42).  At Slot 2 the target table ``canonical_entities``
    is at 0 rows (MCP-verified PM-side at session 96 start), so
    VALIDATE CONSTRAINT is a no-op against the empty target -- there are no
    rows to scan, no blocking-write window, no operational rationale.  The
    Pattern 84 application here is documenting the convention: every
    Cohort-4+ FK-add on a polymorphic-or-cross-tier target uses NOT VALID +
    VALIDATE for shape-consistency, even when the target is empty.  N=3
    evidence point for any future Pattern 84 hardening.

Pattern 91 V1.44 MCP-first premise verification (PM + Builder, build time):
    Pre-build MCP postgres-dev verification (alembic_head=0085):
        - alembic head = 0085 (post-Slot-1).
        - teams row count = 1,034 (all start NULL on canonical_entity_id).
        - canonical_entities row count = 0 (FK target empty; drops trivial).
        - canonical_events row count = 0 (drops of game_id + series_id
          trivial).
        - teams.canonical_entity_id does NOT exist (clear ADD COLUMN target).
        - canonical_entities.ref_team_id exists, INTEGER NULLABLE (DROP
          target).
        - canonical_events.{game_id, series_id} both exist, INTEGER NULLABLE
          (DROP targets).
        - Trigger trg_canonical_entity_team_backref present on
          canonical_entities, DEFERRABLE constraint trigger (DROP target).
        - FK canonical_entity_ref_team_id_fkey present, ON DELETE RESTRICT
          (confdeltype='r'; DROP target).
        - Round-trip CI gate exists at
          tests/integration/migrations/test_round_trip.py and auto-discovers
          slot 0086 via its discovery loop.

Pattern 73 SSOT discipline:
    No new constants this slot (the polymorphic-invariant rule that the
    trigger encoded retires alongside the column it guards).  The Pattern
    82 V2 invariant test ``tests/database/test_canonical_entity_polymorphic
    _invariants.py`` is deleted at this slot per Path A (PM Q4 adjudication);
    the formal V2.40-pin retirement + Pattern 82 V2 scope-narrowing land in
    V2.47 ADR amendment / Pattern note (Slot 5 / session 99).  Inline
    forward-pointer notes on ADR-118 V2.40 Item 4 + DEVELOPMENT_PATTERNS
    Pattern 82 V2 codify the scheduled retirement.

Round-trip discipline:
    Slot 0086's downgrade() is a pure inverse of upgrade(): every CREATE/ADD
    has a matching DROP (in opposite order); the trigger function body is
    reproduced verbatim from Migration 0068 to satisfy round-trip parity.
    The round-trip CI gate (tests/integration/migrations/test_round_trip.py)
    auto-discovers slot 0086 via its discovery loop; no manual hookup
    needed.

Revision ID: 0086
Revises: 0085
Create Date: 2026-05-07

Issues: #1155 (canonical-layer simplification epic),
    Slot 2 of 5-slot cleanup epic per design_review_canonical_cleanup_synthesis.md
ADR: ADR-118 V2.47 amendment lands at Slot 5 / session 99 (codifies the
    FK direction + denorm collapse rules this slot applies)
Memo: build_spec_slot_2_fk_direction_pm_memo.md (binding build spec)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0086"
down_revision: str = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply 9 schema-mutation surfaces (1 ADD COLUMN + 1 ADD FK + VALIDATE +
    DROP TRIGGER + DROP FUNCTION + DROP FK + 3 DROP COLUMN).

    Hard ordering invariant inside upgrade():
        1. ADD teams.canonical_entity_id BIGINT NULL
        2. ADD CONSTRAINT FK NOT VALID
        3. VALIDATE CONSTRAINT (no-op against empty target)
        4. DROP TRIGGER trg_canonical_entity_team_backref
        5. DROP FUNCTION enforce_canonical_entity_team_backref()
        6. DROP FK canonical_entity_ref_team_id_fkey
        7. DROP COLUMN canonical_entities.ref_team_id
        8. DROP COLUMN canonical_events.game_id
        9. DROP COLUMN canonical_events.series_id

    Reason for ordering: trigger function body references ref_team_id; DROP
    TRIGGER + DROP FUNCTION MUST precede DROP COLUMN.  FK MUST drop before
    column.  ADD on teams is independent of canonical_entities mutations
    and runs first for clarity.  All 9 statements run in a single
    transaction (Alembic default); PG enforces ordering within the
    transaction.
    """
    # ------------------------------------------------------------------
    # Step 1: ADD COLUMN teams.canonical_entity_id (nullable; 1,034 rows
    # start NULL).  Cohort 5+ matcher slot (~session 101) populates this
    # column when canonical_entities rows are first seeded.  Until then,
    # all teams rows have NULL canonical_entity_id -- this is the
    # "empty canonical-tier" steady state.
    # ------------------------------------------------------------------
    op.add_column(
        "teams",
        sa.Column("canonical_entity_id", sa.BigInteger(), nullable=True),
    )

    # ------------------------------------------------------------------
    # Step 2: ADD FK NOT VALID (Pattern 84 by-analogy 3rd use; precedent-
    # consistency style not safety).  Empty target table
    # canonical_entities (0 rows MCP-verified) means VALIDATE in step 3
    # is a no-op; we apply the NOT VALID + VALIDATE shape for shape-
    # consistency with slots 0080 + 0082 -- every Cohort 4+ FK-add on a
    # canonical-tier target uses this two-phase pattern.
    #
    # ON DELETE SET NULL polarity matches ADR-118 V2.42-B precedent
    # (canonical-outlives-platform: when a canonical_entity is hard-
    # deleted, the teams row preserves its identity via SET NULL rather
    # than cascading the delete).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE teams
        ADD CONSTRAINT teams_canonical_entity_id_fkey
        FOREIGN KEY (canonical_entity_id) REFERENCES canonical_entities(id)
        ON DELETE SET NULL
        NOT VALID
        """
    )

    # ------------------------------------------------------------------
    # Step 3: VALIDATE CONSTRAINT (no-op against empty target; required
    # for precedent-consistency with Pattern 84 by-analogy uses in slots
    # 0080 + 0082).  PG accepts VALIDATE on a NOT VALID constraint with
    # zero rows in the referencing table immediately -- no scan, no row-
    # by-row check.  The marker matters: post-VALIDATE the constraint is
    # convalidated=true in pg_constraint, which is the queryable
    # post-condition the integration test pins.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE teams VALIDATE CONSTRAINT teams_canonical_entity_id_fkey")

    # ------------------------------------------------------------------
    # Step 4: DROP TRIGGER trg_canonical_entity_team_backref
    #
    # Hard ordering invariant: trigger function body references
    # NEW.ref_team_id (Migration 0068 lines 271-291; quoted verbatim in
    # downgrade() below).  PG materializes the trigger function body at
    # CREATE FUNCTION time and caches column references; DROP COLUMN
    # before DROP TRIGGER would error "column is referenced in trigger
    # function".
    #
    # CONSTRAINT TRIGGER has no OR REPLACE form -- DROP IF EXISTS is the
    # idempotent-rollback shape (Glokta carry-forward #1 from 0067 review,
    # propagated to 0068 downgrade and now to slot 0086 upgrade).
    # ------------------------------------------------------------------
    op.execute("DROP TRIGGER IF EXISTS trg_canonical_entity_team_backref ON canonical_entities")

    # ------------------------------------------------------------------
    # Step 5: DROP FUNCTION enforce_canonical_entity_team_backref()
    #
    # PG semantic: DROP TRIGGER does not auto-DROP the underlying function.
    # Without this step the function survives as orphan in pg_proc with
    # zero callers.  Ripley risk surface § 7 specifically pins this:
    # post-migration ``SELECT proname FROM pg_proc WHERE proname =
    # 'enforce_canonical_entity_team_backref'`` MUST return zero rows.
    # ------------------------------------------------------------------
    op.execute("DROP FUNCTION IF EXISTS enforce_canonical_entity_team_backref()")

    # ------------------------------------------------------------------
    # Step 6: DROP CONSTRAINT canonical_entity_ref_team_id_fkey
    #
    # Precedes DROP COLUMN (PG forbids dropping a column that is the
    # subject of a non-cascaded FK constraint).  ON DELETE RESTRICT
    # polarity (confdeltype='r' MCP-verified) is irrelevant at DROP
    # time -- DROP CONSTRAINT removes the constraint regardless of
    # polarity.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE canonical_entities DROP CONSTRAINT canonical_entity_ref_team_id_fkey")

    # ------------------------------------------------------------------
    # Step 7: DROP COLUMN canonical_entities.ref_team_id
    #
    # The FK index idx_canonical_entity_ref_team_id (partial WHERE
    # ref_team_id IS NOT NULL; Migration 0068 line 308) is auto-dropped
    # by PG when the column is dropped (PG semantic: indexes that
    # reference only the dropped column drop along with it).
    # ------------------------------------------------------------------
    op.drop_column("canonical_entities", "ref_team_id")

    # ------------------------------------------------------------------
    # Step 8: DROP COLUMN canonical_events.game_id
    #
    # Empty table (canonical_events at 0 rows MCP-verified).  The FK to
    # games(id) was Migration 0067-shipped with ON DELETE SET NULL
    # polarity per V2.42-B (Migration 0077 retrofit).  Both the FK
    # constraint and any associated index drop with the column.
    # ------------------------------------------------------------------
    op.drop_column("canonical_events", "game_id")

    # ------------------------------------------------------------------
    # Step 9: DROP COLUMN canonical_events.series_id
    #
    # Same shape as step 8 -- empty table, FK to series(id) ON DELETE
    # SET NULL (Migration 0077 retrofit), drops with the column.
    # ------------------------------------------------------------------
    op.drop_column("canonical_events", "series_id")


def downgrade() -> None:
    """Reverse 0086: restore pre-Slot-2 canonical-layer schema.

    Step order (reverse-dependency-respecting; mirrors upgrade in opposite
    order; trigger function body reproduced verbatim from Migration 0068
    lines 271-301 per Pattern 87 -- 0068 is canonical for the trigger
    DDL):

        9r. RECREATE canonical_events.series_id INTEGER NULL FK -> series(id)
        8r. RECREATE canonical_events.game_id INTEGER NULL FK -> games(id)
        7r. RECREATE canonical_entities.ref_team_id INTEGER NULL
        6r. RECREATE FK canonical_entity_ref_team_id_fkey
        5r. RECREATE FUNCTION enforce_canonical_entity_team_backref()
        4r. RECREATE CONSTRAINT TRIGGER trg_canonical_entity_team_backref
        3r./2r./1r. DROP FK + DROP COLUMN teams.canonical_entity_id

    Round-trip parity: pre-0086 upgrade schema state == post-0086 downgrade
    schema state (verified by tests/integration/migrations/test_round_trip.py).
    The trigger function body in step 5r matches Migration 0068 lines
    273-290 verbatim (whitespace + formatting may differ; logical body
    identical).
    """
    # ------------------------------------------------------------------
    # Step 9r: RECREATE canonical_events.series_id INTEGER NULL FK ->
    # series(id) ON DELETE SET NULL.  Polarity per Migration 0077
    # retrofit (V2.42-B sub-amendment).
    # ------------------------------------------------------------------
    op.add_column(
        "canonical_events",
        sa.Column("series_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD CONSTRAINT canonical_events_series_id_fkey
        FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE SET NULL
        """
    )

    # Recreate the FK-column partial index (Migration 0067 lines 272-275).
    # Round-trip CI gate snapshots schema at HEAD only, so an absent partial
    # index in the downgrade path is invisible to the gate -- yet
    # downgrade() must restore byte-equivalent schema state for any future
    # forward-replay through 0086.  Pattern 87 makes 0086 immutable
    # post-merge; this is the last edit window for parity coverage.
    op.execute(
        "CREATE INDEX idx_canonical_events_series_id "
        "ON canonical_events(series_id) WHERE series_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # Step 8r: RECREATE canonical_events.game_id INTEGER NULL FK ->
    # games(id) ON DELETE SET NULL.  Polarity per Migration 0077 retrofit.
    # ------------------------------------------------------------------
    op.add_column(
        "canonical_events",
        sa.Column("game_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD CONSTRAINT canonical_events_game_id_fkey
        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE SET NULL
        """
    )

    # Recreate the FK-column partial index (Migration 0067 lines 268-271).
    # Mirror of step 9r index recreation above.
    op.execute(
        "CREATE INDEX idx_canonical_events_game_id "
        "ON canonical_events(game_id) WHERE game_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # Step 7r: RECREATE canonical_entities.ref_team_id INTEGER NULL
    # ------------------------------------------------------------------
    op.add_column(
        "canonical_entities",
        sa.Column("ref_team_id", sa.Integer(), nullable=True),
    )

    # ------------------------------------------------------------------
    # Step 6r: RECREATE FK canonical_entity_ref_team_id_fkey ->
    # teams(team_id) ON DELETE RESTRICT.  Original polarity per
    # Migration 0068 line 245.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE canonical_entities
        ADD CONSTRAINT canonical_entity_ref_team_id_fkey
        FOREIGN KEY (ref_team_id) REFERENCES teams(team_id) ON DELETE RESTRICT
        """
    )

    # Recreate the FK-column partial index (Migration 0068 line 308).
    op.execute(
        "CREATE INDEX idx_canonical_entity_ref_team_id "
        "ON canonical_entities(ref_team_id) WHERE ref_team_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # Step 5r: RECREATE FUNCTION enforce_canonical_entity_team_backref()
    #
    # Body verbatim from Migration 0068 lines 273-290.  Pattern 87
    # discipline: 0068 is canonical for this trigger DDL; the downgrade
    # reproduces the body identical to 0068 (whitespace + formatting may
    # differ; logical body identical).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_canonical_entity_team_backref()
        RETURNS TRIGGER AS $$
        DECLARE
            v_entity_kind TEXT;
        BEGIN
            SELECT entity_kind INTO v_entity_kind
              FROM canonical_entity_kinds
             WHERE id = NEW.entity_kind_id;

            IF v_entity_kind = 'team' AND NEW.ref_team_id IS NULL THEN
                RAISE EXCEPTION
                  'canonical_entity: entity_kind=team requires ref_team_id NOT NULL (canonical_entity.id=%)',
                  NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # ------------------------------------------------------------------
    # Step 4r: RECREATE CONSTRAINT TRIGGER
    # trg_canonical_entity_team_backref.
    #
    # CONSTRAINT TRIGGER has no OR REPLACE form -- direct CREATE.  Body
    # matches Migration 0068 lines 295-301 verbatim.  Note the table name
    # is now ``canonical_entities`` (post-Migration-0085 rename); this is
    # correct for the downgrade target state -- 0086 downgrade restores
    # the pre-0086 / post-0085 shape.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_canonical_entity_team_backref
            AFTER INSERT OR UPDATE OF entity_kind_id, ref_team_id ON canonical_entities
            DEFERRABLE INITIALLY IMMEDIATE
            FOR EACH ROW
            EXECUTE FUNCTION enforce_canonical_entity_team_backref()
        """
    )

    # ------------------------------------------------------------------
    # Steps 3r/2r/1r: DROP FK teams_canonical_entity_id_fkey + DROP
    # COLUMN teams.canonical_entity_id.  Reverses the upgrade ADD path.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE teams DROP CONSTRAINT teams_canonical_entity_id_fkey")
    op.drop_column("teams", "canonical_entity_id")
