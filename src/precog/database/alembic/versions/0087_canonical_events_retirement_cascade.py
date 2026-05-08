"""Cleanup epic Slot 3 -- canonical_events retirement cascade (slot 0087).

Realizes CL-7 path b (the SSOT helper + ``superseded_by`` self-FK approach
chosen at session 94 council; full origin in
``memory/design_review_canonical_cleanup_synthesis.md`` § 2 D1).
ADR-118 V2.47 amendment will codify the design rationale this slot enacts;
V2.47 lands at Slot 5 / session 99 per the 5-slot cleanup epic plan (this
slot ships the operations, Slot 5 ships the rule narrative).

Source: ``memory/build_spec_slot_3_retirement_cascade_pm_memo.md`` § 2
(binding DDL) + ``memory/design_review_canonical_cleanup_synthesis.md``
§ 4 Slot 3 row + § 8 one-line spec.  PM Picard adjudications Q1-Q7
(session 97).

Two schema-mutation surfaces in upgrade(), in a single transaction:

    1. ADD COLUMN ``canonical_events.superseded_by BIGINT NULL`` with
       inline self-referencing FK ``REFERENCES canonical_events(id) ON
       DELETE SET NULL``.  Empty target table (canonical_events at 0 rows
       MCP-verified pre-build); FK validation is trivial.
    2. ADD CONSTRAINT ``canonical_events_no_self_supersession CHECK
       (superseded_by IS NULL OR superseded_by <> id)``.  Single-row self-
       cycle prevention; cheap forward-looking insurance at all row counts.
       Multi-row cycles are prevented by 2 application-layer defenses --
       see "Cycle prevention 3-layer defense" below.

Hard ordering invariant inside upgrade():
    1. ADD COLUMN canonical_events.superseded_by (FK inline)
    2. ADD CONSTRAINT canonical_events_no_self_supersession (CHECK)

downgrade() reverses in opposite order:
    2r. DROP CONSTRAINT canonical_events_no_self_supersession
    1r. DROP COLUMN canonical_events.superseded_by (CASCADE drops the
        inline FK constraint with the column; PG semantic).

Pattern 87 (Append-only migration files) -- REAFFIRMED CLEAN:
    DEVELOPMENT_PATTERNS V1.40+.  This file is immutable post-merge.  Slot
    0087 is a NEW migration file; Pattern 87 fires when editing
    PREVIOUSLY-MERGED migrations.  This PR makes ZERO edits to migrations
    0001-0086.  The pre-Slot-3 schema referenced in shipped migration
    bodies + docstrings is correct, not stale.

Pattern 84 V1.42 (NOT VALID + VALIDATE for FK by-analogy):
    NOT applied here.  canonical_events is empty AND the FK is self-
    referencing on the same empty table -- there is no scenario where
    FK validation could block (the referencing column starts as all-NULL
    on an empty table; there are no rows to scan).  Straight-add via
    inline ``REFERENCES`` is correct.  Pattern 84 ledger stays at 3 uses
    (slots 0080 + 0082 + 0086) post-Slot-3.

Coordination with retired_at:

    Active row:                  superseded_by IS NULL AND retired_at IS NULL
    Retired without replacement: superseded_by IS NULL AND retired_at IS NOT NULL
                                 (terminal tombstone)
    Superseded (replaced):       superseded_by = <new_id> AND retired_at IS NOT NULL

    crud_canonical_events.retire_canonical_event() extends to optionally
    accept ``superseded_by_id``; the SSOT helper get_active_canonical_event()
    walks the chain forward and returns the terminal-active row OR None
    if the chain terminates at a tombstone (per Q1 adjudication).

Cycle prevention 3-layer defense (per Q2 user adjudication):

    (a) DB-level single-row self-cycle CHECK (this migration) blocks
        id->id cycles at all row counts.
    (b) Helper write-side cycle check inside
        retire_canonical_event(superseded_by_id=...) walks the chain
        from superseded_by_id via recursive CTE and raises ValueError if
        canonical_event_id appears in the chain (Pattern 73 SSOT: one
        write surface = one validation point; this is the meaningful
        production-cycle barrier when the table is populated).
    (c) Helper read-side 100-hop iteration guard in
        get_active_canonical_event() -- runtime safety net for any chain
        that escaped (a)+(b) via direct SQL writes.

    DB-level recursive-trigger multi-row validation rejected as over-
    engineering at any row count: the SSOT entry point is the natural
    validation site, and the read-side guard converts escape paths to
    non-fatal failure modes.

Pattern 91 V1.44 MCP-first premise verification (PM + Builder, build time):
    Pre-build MCP postgres-dev verification (alembic_head=0086):
        - alembic head = 0086 (post-Slot-2).
        - canonical_events row count = 0 (FK target empty; ADD COLUMN
          trivial, FK self-validation no-op).
        - canonical_events.superseded_by does NOT exist (clean ADD COLUMN
          target; column inventory: id / event_domain_id / event_type_id /
          participants_sorted / resolution_window / resolution_rule_fp /
          natural_key_hash / title / description / lifecycle_phase /
          metadata / created_at / updated_at / retired_at = 14 columns).
        - canonical_events.retired_at exists, timestamptz NULL (existing
          soft-retirement column; coordinated with new superseded_by per
          ``Coordination with retired_at`` matrix above).
        - Round-trip CI gate exists at
          ``tests/integration/migrations/test_round_trip.py`` and auto-
          discovers slot 0087 via its discovery loop.

Pattern 73 SSOT discipline:
    No new constants this slot.  The recursive-CTE chain-walk SQL idiom
    is encapsulated in get_active_canonical_event() (and a sibling
    helper inside retire_canonical_event for write-side cycle check) so
    the helper is the SSOT for active-row resolution + cycle validation.

Round-trip discipline:
    Slot 0087's downgrade() is a pure inverse of upgrade(): every CREATE/
    ADD has a matching DROP (in opposite order).  DROP COLUMN with inline
    FK is sufficient -- PG drops the FK constraint with the column
    automatically (no explicit DROP CONSTRAINT needed before DROP COLUMN).
    The round-trip CI gate (tests/integration/migrations/test_round_trip.py)
    auto-discovers slot 0087 via its discovery loop; no manual hookup
    needed.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-07

Issues: #1155 (canonical-layer simplification epic),
    Slot 3 of 5-slot cleanup epic per design_review_canonical_cleanup_synthesis.md
ADR: ADR-118 V2.47 amendment lands at Slot 5 / session 99 (codifies the
    retirement-cascade rule this slot enacts)
Memo: build_spec_slot_3_retirement_cascade_pm_memo.md (binding build spec)
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0087"
down_revision: str = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply 2 schema-mutation surfaces (1 ADD COLUMN with inline self-FK +
    1 ADD CHECK).

    Hard ordering invariant inside upgrade():
        1. ADD COLUMN canonical_events.superseded_by BIGINT NULL
           REFERENCES canonical_events(id) ON DELETE SET NULL (FK inline)
        2. ADD CONSTRAINT canonical_events_no_self_supersession CHECK
           (superseded_by IS NULL OR superseded_by <> id)

    Reason for ordering: CHECK references the column added in step 1.
    Both statements run in a single transaction (Alembic default).
    """
    # ------------------------------------------------------------------
    # Step 1: ADD COLUMN canonical_events.superseded_by (BIGINT NULL,
    # self-referencing FK inline).  Empty target table (canonical_events
    # at 0 rows MCP-verified) means FK self-validation is trivial; no
    # rows to scan, no blocking-write window.  Pattern 84 NOT applied
    # (build spec § 0 P91 catch #1) -- straight-add via inline REFERENCES
    # is correct on an empty self-referencing table.
    #
    # ON DELETE SET NULL polarity matches ADR-118 V2.42-B precedent
    # (canonical-outlives-platform): when a superseding canonical_events
    # row is hard-deleted, the older row's superseded_by silently drops
    # to NULL rather than cascading the delete.  Net effect: the older
    # row's terminal-tombstone state is restored (retired_at IS NOT NULL,
    # superseded_by IS NULL); the chain re-terminates at the deleted row's
    # predecessor.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD COLUMN superseded_by BIGINT NULL
        REFERENCES canonical_events(id) ON DELETE SET NULL
        """
    )

    # ------------------------------------------------------------------
    # Step 2: ADD CONSTRAINT canonical_events_no_self_supersession CHECK.
    # Single-row self-cycle prevention -- blocks id->id cycles at all
    # row counts.  Cheap forward-looking insurance: NULL admitted (most
    # rows; active head OR retired-without-replacement tombstone), <>
    # admitted (chain construction); = id rejected (the trivial cycle).
    #
    # Multi-row cycles are not blocked at the DB layer by design (a
    # recursive trigger would be over-engineering); see migration
    # docstring ``Cycle prevention 3-layer defense`` for the
    # application-layer defenses (helper write-side check + read-side
    # 100-hop guard).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD CONSTRAINT canonical_events_no_self_supersession
        CHECK (superseded_by IS NULL OR superseded_by <> id)
        """
    )


def downgrade() -> None:
    """Reverse 0087: restore pre-Slot-3 canonical_events column inventory.

    Step order (reverse-dependency-respecting; mirrors upgrade in opposite
    order):

        2r. DROP CONSTRAINT canonical_events_no_self_supersession
        1r. DROP COLUMN canonical_events.superseded_by

    DROP COLUMN with inline FK is sufficient -- PG drops the FK
    constraint (canonical_events_superseded_by_fkey, auto-named by PG
    from the inline REFERENCES clause) with the column automatically.
    No explicit DROP CONSTRAINT needed before DROP COLUMN.

    Round-trip parity: pre-0087 upgrade schema state == post-0087 downgrade
    schema state (verified by tests/integration/migrations/test_round_trip.py).
    """
    # ------------------------------------------------------------------
    # Step 2r: DROP CHECK constraint (must precede DROP COLUMN; PG
    # forbids dropping a column referenced by a CHECK constraint).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE canonical_events
        DROP CONSTRAINT canonical_events_no_self_supersession
        """
    )

    # ------------------------------------------------------------------
    # Step 1r: DROP COLUMN canonical_events.superseded_by (CASCADE
    # drops the inline self-FK with the column; PG semantic).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE canonical_events
        DROP COLUMN superseded_by
        """
    )
