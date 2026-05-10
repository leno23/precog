"""Cleanup epic Slot 4 -- canonical-tier lifecycle_phase redistribution + canonical_market_phase_log audit ledger.

Cleanup epic Slot 4 (CL-3 redesign per session 98 council R6 verdict).
Source: ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md``
(amended in-place at § 0d D-1..D-4 + § 0d-bis B-1 + B-2 + M-1).
Council synthesis: ``memory/design_review_lifecycle_phase_synthesis.md``
§ 2 Lock 1+2 + § 4 D-1 + D-2 (R6 unanimous 4-of-4).

Pattern 87: this file is immutable post-merge.

R3 (the redistribution): redistribute resolution-tier states from
``canonical_events.lifecycle_phase`` to a new
``canonical_markets.lifecycle_phase`` column.  Closes ADR-118 V2.39
revisit-trigger fired (per-canonical-market resolution divergence is
canonical-tier by construction; distinct from the per-platform
divergence V2.39 correctly rejected).

R8 bundle (Holden Lock 2):
    canonical_events.lifecycle_phase enum reduces 8->5 (drop suspended,
    settling, resolved, voided; add 'completed' for event-completion
    semantics).  canonical_event_phase_log.new_phase + previous_phase
    CHECK constraints mirror the reduction (V2.40 Item 3 binding rule:
    log-table CHECKs must mirror dim-table CHECK).

New audit log canonical_market_phase_log SPLITS from existing
canonical_event_phase_log per Pattern 18 SCD2-cousin discipline (Holden
Lock 2 rationale).  Mirrors slot 0079 shape exactly (8 columns + 3
functional indexes + auto-trigger function + AFTER INSERT OR UPDATE OF
trigger semantics + ``IS DISTINCT FROM`` NULL-safe predicate +
``changed_by='system:trigger'`` from ``DECIDED_BY_PREFIXES`` system:
family).

Pattern 84 V1.42 (NOT VALID + VALIDATE for FK by-analogy) -- N/A:
    canonical_markets is empty (0 rows MCP-verified) AND
    canonical_market_phase_log is fresh.  Straight-add via inline FK is
    correct.  Pattern 84 ledger stays at 3 uses.

Pattern 73 (SSOT) -- vocabulary lives at constants.py:
    Two vocabularies are anchored at
    ``src/precog/database/constants.py``:

        CANONICAL_EVENT_LIFECYCLE_PHASES  -- REDUCED 8->5 in lockstep
                                             with this migration
                                             (R8 bundle).
                                             ('proposed', 'listed',
                                              'pre_event', 'live',
                                              'completed')
        CANONICAL_MARKET_LIFECYCLE_PHASES -- NEW 5-tuple introduced in
                                             lockstep with this
                                             migration (R3 + R6).
                                             ('open', 'suspended',
                                              'settling', 'resolved',
                                              'voided')

    DDL CHECK constraints carry the literal values inline (PG CHECK
    cannot reference application-layer constants).  Five-way parity
    enforced at test time via
    ``tests/integration/database/test_lifecycle_phase_vocabulary_ssot.py``
    extension (3-way -> 5-way).  CRUD-layer write validation in
    ``crud_canonical_event_phase_log.py`` +
    ``crud_canonical_market_phase_log.py`` imports each constant.

V2.47 ADR amendment ships at Slot 5 (session 99) -- codify the
redistribution rule and Galadriel's per-platform-vs-per-canonical-market
reconciliation prose.  Pattern 91 V1.45+ promotion ships then.

Hard ordering invariant inside upgrade():
    1. ADD COLUMN canonical_markets.lifecycle_phase + inline CHECK
    2. ALTER canonical_events.lifecycle_phase CHECK (drop+add)
    3. ALTER canonical_event_phase_log.new_phase CHECK (drop+add)
    4. ALTER canonical_event_phase_log.previous_phase CHECK (drop+add)
    5. CREATE TABLE canonical_market_phase_log + inline CHECKs
    6. CREATE INDEX x 3 (mirror slot 0079)
    7. CREATE FUNCTION log_canonical_market_phase_transition() + COMMENT ON
    8. CREATE TRIGGER trg_canonical_markets_log_phase_transition

downgrade() reverses in opposite order.

Round-trip discipline (PR #1081 round-trip CI gate):
    Migration 0088 round-trips clean.  Every CREATE has a matching
    DROP IF EXISTS in downgrade.  Drop order respects object
    dependencies (trigger -> trigger function -> indexes -> table).
    The round-trip gate auto-discovers slot 0088 on push and runs
    ``downgrade -> upgrade head`` against it.

Revision ID: 0088
Revises: 0087
Create Date: 2026-05-09

Issues: #1155 (canonical-layer simplification epic),
    Cohort 5+ matcher slot inheritance,
    ADR-118 V2.39 revisit-trigger
ADR: ADR-118 V2.46 head; V2.47 ships at Slot 5 (session 99)
Build spec: ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md``
Council synthesis: ``memory/design_review_lifecycle_phase_synthesis.md``
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0088"
down_revision: str = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Redistribute lifecycle_phase + create canonical_market_phase_log + auto-trigger.

    Step order (binding per build spec § 1):

        1. ADD COLUMN canonical_markets.lifecycle_phase + inline CHECK
           (5-value: 'open', 'suspended', 'settling', 'resolved', 'voided')
        2. REDUCE canonical_events.lifecycle_phase CHECK 8->5 (R8 bundle)
        3. REDUCE canonical_event_phase_log.new_phase CHECK 8->5 (R8 mirror)
        4. REDUCE canonical_event_phase_log.previous_phase CHECK 8->5 (R8 mirror)
        5. CREATE TABLE canonical_market_phase_log (mirror slot 0079 shape)
        6. CREATE INDEX x 3 (mirror slot 0079 indexing exactly)
        7. CREATE FUNCTION log_canonical_market_phase_transition() (mirror
           slot 0079 verbatim per build spec § 0d D-2)
        8. CREATE TRIGGER trg_canonical_markets_log_phase_transition AFTER
           INSERT OR UPDATE OF lifecycle_phase ON canonical_markets
           (mirror slot 0079 verbatim per build spec § 0d D-2)
    """
    # =========================================================================
    # Step 1: ADD COLUMN canonical_markets.lifecycle_phase + inline 5-value CHECK
    #
    # canonical_markets has 0 rows MCP-verified; trivial DEFAULT 'open' fills
    # any future inserts.  CHECK enumerates the resolution-tier vocabulary
    # redistributed from canonical_events per R3.
    # =========================================================================
    op.execute(
        """
        ALTER TABLE canonical_markets
        ADD COLUMN lifecycle_phase VARCHAR(32) NOT NULL DEFAULT 'open'
        CHECK (lifecycle_phase IN ('open', 'suspended', 'settling', 'resolved', 'voided'))
        """
    )

    # =========================================================================
    # Step 2: REDUCE canonical_events.lifecycle_phase 8->5 (R8 bundle)
    #
    # Drop the 8-value CHECK shipped in Migration 0070 + replace with the
    # reduced 5-value vocabulary.  canonical_events has 0 rows so structural
    # CHECK swap is trivial.  Adds 'completed' for event-completion semantics
    # (replaces 'resolved' which moved to canonical_markets).
    # =========================================================================
    op.execute(
        """
        ALTER TABLE canonical_events
        DROP CONSTRAINT canonical_events_lifecycle_phase_check
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD CONSTRAINT canonical_events_lifecycle_phase_check
        CHECK (lifecycle_phase IN ('proposed', 'listed', 'pre_event', 'live', 'completed'))
        """
    )

    # =========================================================================
    # Step 3: REDUCE canonical_event_phase_log.new_phase 8->5 (R8 mirror)
    #
    # Mirror reduction on the audit-log new_phase CHECK.  canonical_event_phase_log
    # has 0 rows MCP-verified; structural swap trivial.  Pattern 73 SSOT:
    # log-table CHECKs MUST mirror dim-table CHECK exactly (V2.40 Item 3).
    # =========================================================================
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        DROP CONSTRAINT ck_canonical_event_phase_log_new_phase
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        ADD CONSTRAINT ck_canonical_event_phase_log_new_phase
        CHECK (new_phase IN ('proposed', 'listed', 'pre_event', 'live', 'completed'))
        """
    )

    # =========================================================================
    # Step 4: REDUCE canonical_event_phase_log.previous_phase 8->5 (R8 mirror)
    #
    # NULL-tolerant CHECK: previous_phase is NULL on first-transition rows.
    # =========================================================================
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        DROP CONSTRAINT ck_canonical_event_phase_log_previous_phase
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        ADD CONSTRAINT ck_canonical_event_phase_log_previous_phase
        CHECK (previous_phase IS NULL OR previous_phase IN
            ('proposed', 'listed', 'pre_event', 'live', 'completed'))
        """
    )

    # =========================================================================
    # Step 5: CREATE TABLE canonical_market_phase_log (mirror slot 0079 shape)
    #
    # Column-level rationale (mirror slot 0079 exactly, market-substituted):
    #   - id BIGSERIAL PK: surrogate addressability for operator runbooks.
    #   - canonical_market_id BIGINT NOT NULL ON DELETE CASCADE: audit log
    #     dies with the parent market (no parallel attribution tuple).
    #   - previous_phase VARCHAR(32) NULL: first transition has no
    #     predecessor (INSERT path emits NULL -> 'open').
    #   - new_phase VARCHAR(32) NOT NULL: every transition has a destination.
    #   - transition_at TIMESTAMPTZ NOT NULL DEFAULT now(): drives audit-hot-path ORDER BY.
    #   - changed_by VARCHAR(64) NOT NULL: actor attribution (DECIDED_BY_PREFIXES).
    #     Trigger emits 'system:trigger'; manual paths via
    #     append_market_phase_transition() use 'human:<username>' or other.
    #   - note TEXT NULL: free-form operator-readable explanation.
    #   - created_at TIMESTAMPTZ NOT NULL DEFAULT now(): ADR-118 V2.42
    #     sub-amendment A canonical convention.
    #
    # Two CHECK constraints carry the 5-value canonical_markets vocabulary
    # inline; previous_phase CHECK is NULL-tolerant.
    # =========================================================================
    op.execute(
        """
        CREATE TABLE canonical_market_phase_log (
            id                    BIGSERIAL    PRIMARY KEY,
            -- canonical_market_id ON DELETE CASCADE: audit log dies with the
            -- parent market.  Mirrors slot 0079's canonical_event_id polarity.
            canonical_market_id   BIGINT       NOT NULL REFERENCES canonical_markets(id) ON DELETE CASCADE,
            -- previous_phase nullable: first transition has no predecessor
            -- (INSERT path emits NULL -> phase).
            previous_phase        VARCHAR(32)  NULL,
            -- new_phase always populated.
            new_phase             VARCHAR(32)  NOT NULL,
            -- transition_at drives the audit-hot-path ORDER BY.
            transition_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
            -- changed_by Pattern 73 SSOT pointer to constants.py
            -- DECIDED_BY_PREFIXES; CRUD-layer validation enforces format.
            changed_by            VARCHAR(64)  NOT NULL,
            note                  TEXT         NULL,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

            -- 5-value canonical_markets lifecycle_phase vocabulary canonical
            -- home: src/precog/database/constants.py
            -- CANONICAL_MARKET_LIFECYCLE_PHASES.  Pattern 73 SSOT: same 5
            -- values appear in canonical_markets.lifecycle_phase CHECK
            -- (Step 1).  Adding a value requires lockstep update across (a)
            -- the constant, (b) Step 1's CHECK, and (c) BOTH of these
            -- CHECKs.  Five-way parity verified by
            -- tests/integration/database/test_lifecycle_phase_vocabulary_ssot.py.
            CONSTRAINT ck_canonical_market_phase_log_new_phase CHECK (
                new_phase IN ('open', 'suspended', 'settling', 'resolved', 'voided')
            ),
            CONSTRAINT ck_canonical_market_phase_log_previous_phase CHECK (
                previous_phase IS NULL OR previous_phase IN
                    ('open', 'suspended', 'settling', 'resolved', 'voided')
            )
        )
        """
    )

    # =========================================================================
    # Step 6: CREATE INDEX x 3 (mirror slot 0079 indexing exactly)
    # =========================================================================
    # Audit hot-path index: ORDER BY transition_at DESC dominates operator
    # runbook queries.  DESC index avoids server-side reverse-scan.
    op.execute(
        "CREATE INDEX idx_canonical_market_phase_log_transition_at "
        "ON canonical_market_phase_log (transition_at DESC)"
    )

    # FK-target index on canonical_market_id: supports the "phase history
    # for market X" lookup + speeds CASCADE delete fan-out.
    op.execute(
        "CREATE INDEX idx_canonical_market_phase_log_canonical_market_id "
        "ON canonical_market_phase_log (canonical_market_id)"
    )

    # Composite index for the canonical operator runbook query:
    # "show me phase history for market X, newest first."
    op.execute(
        "CREATE INDEX idx_canonical_market_phase_log_market_transition "
        "ON canonical_market_phase_log (canonical_market_id, transition_at DESC)"
    )

    # =========================================================================
    # Step 7: CREATE FUNCTION log_canonical_market_phase_transition()
    #
    # Mirror slot 0079 verbatim per build spec § 0d D-2 + Q14:
    #   - INSERT path: emit NULL -> NEW.lifecycle_phase row.  Always fires.
    #   - UPDATE path: use IS DISTINCT FROM (NULL-safe) to handle defensive
    #     cases where one side might be NULL.  Even though
    #     canonical_markets.lifecycle_phase is NOT NULL, IS DISTINCT FROM
    #     is the correct idiom for phase-comparison in trigger functions.
    #   - changed_by='system:trigger' uses the system: prefix from
    #     DECIDED_BY_PREFIXES; satisfies the prefix discipline.
    #   - The trigger (Step 8) is AFTER (not BEFORE) so the row update is
    #     guaranteed-committed-locally before the audit row is inserted.
    #
    # WARNING: function body whitespace is load-bearing for the round-trip CI
    # gate which snapshots pg_get_functiondef() output.  Reformatting the
    # heredoc indentation will break the snapshot oracle even though the
    # function behaves identically (slot 0076 P2 finding precedent).
    # =========================================================================
    op.execute(
        """
        CREATE FUNCTION log_canonical_market_phase_transition()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO canonical_market_phase_log (
                    canonical_market_id, previous_phase, new_phase, changed_by, note
                ) VALUES (
                    NEW.id, NULL, NEW.lifecycle_phase, 'system:trigger',
                    'auto-populated from canonical_markets INSERT'
                );
            ELSIF TG_OP = 'UPDATE' AND OLD.lifecycle_phase IS DISTINCT FROM NEW.lifecycle_phase THEN
                INSERT INTO canonical_market_phase_log (
                    canonical_market_id, previous_phase, new_phase, changed_by, note
                ) VALUES (
                    NEW.id, OLD.lifecycle_phase, NEW.lifecycle_phase, 'system:trigger',
                    'auto-populated from canonical_markets UPDATE'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # COMMENT ON FUNCTION documents the trigger's contract for operators
    # inspecting via \df+ log_canonical_market_phase_transition.
    op.execute(
        """
        COMMENT ON FUNCTION log_canonical_market_phase_transition() IS
        'Auto-populates canonical_market_phase_log from canonical_markets '
        'INSERT (NULL->phase) and UPDATE OF lifecycle_phase (old->new) '
        'using IS DISTINCT FROM (NULL-safe).  Emits changed_by=''system:trigger''.'
        """
    )

    # =========================================================================
    # Step 8: CREATE TRIGGER trg_canonical_markets_log_phase_transition
    #
    # Mirror slot 0079 verbatim per build spec § 0d D-2 + Q14:
    #   - AFTER INSERT: every canonical_markets INSERT generates an initial
    #     audit row (NULL -> NEW.lifecycle_phase).
    #   - AFTER UPDATE OF lifecycle_phase: only column-targeted UPDATEs that
    #     touch lifecycle_phase invoke the function; UPDATEs that touch
    #     other columns (e.g., outcome_label, updated_at) do NOT fire this
    #     trigger.
    # =========================================================================
    op.execute(
        """
        CREATE TRIGGER trg_canonical_markets_log_phase_transition
            AFTER INSERT OR UPDATE OF lifecycle_phase ON canonical_markets
            FOR EACH ROW
            EXECUTE FUNCTION log_canonical_market_phase_transition()
        """
    )


def downgrade() -> None:
    """Reverse 0088 in opposite order.

    Drop order:
        1. trg_canonical_markets_log_phase_transition  (frees the function)
        2. log_canonical_market_phase_transition()
        3. Indexes (3 explicit drops)
        4. canonical_market_phase_log table
        5. Restore canonical_event_phase_log.previous_phase 8-value CHECK
        6. Restore canonical_event_phase_log.new_phase 8-value CHECK
        7. Restore canonical_events.lifecycle_phase 8-value CHECK
        8. DROP COLUMN canonical_markets.lifecycle_phase (CASCADE drops CHECK)

    ``IF EXISTS`` used throughout for idempotent rollback per session 59
    ``feedback_idempotent_migration_drops.md``.

    The downgrade is intentionally lossy: any audit ledger contents written
    to canonical_market_phase_log are discarded, and any non-default
    canonical_markets.lifecycle_phase values are lost when the column
    drops.  Upgrade-then-downgrade-then-upgrade is the supported cycle
    (round-trip CI gate per PR #1081).
    """
    # Step 1: drop trigger (frees the function for safe DROP).
    op.execute(
        "DROP TRIGGER IF EXISTS trg_canonical_markets_log_phase_transition ON canonical_markets"
    )

    # Step 2: drop trigger function (NOT CASCADE per slot-0079 precedent).
    op.execute("DROP FUNCTION IF EXISTS log_canonical_market_phase_transition()")

    # Step 3: indexes (explicit drops; DROP TABLE would cascade, but explicit
    # drop matches slot 0079 convention).
    op.execute("DROP INDEX IF EXISTS idx_canonical_market_phase_log_market_transition")
    op.execute("DROP INDEX IF EXISTS idx_canonical_market_phase_log_canonical_market_id")
    op.execute("DROP INDEX IF EXISTS idx_canonical_market_phase_log_transition_at")

    # Step 4: drop the canonical_market_phase_log table (leaf; no children FK INTO it).
    op.execute("DROP TABLE IF EXISTS canonical_market_phase_log")

    # Step 5: restore canonical_event_phase_log.previous_phase 8-value CHECK
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        DROP CONSTRAINT ck_canonical_event_phase_log_previous_phase
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        ADD CONSTRAINT ck_canonical_event_phase_log_previous_phase
        CHECK (previous_phase IS NULL OR previous_phase IN
            ('proposed', 'listed', 'pre_event', 'live',
             'suspended', 'settling', 'resolved', 'voided'))
        """
    )

    # Step 6: restore canonical_event_phase_log.new_phase 8-value CHECK
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        DROP CONSTRAINT ck_canonical_event_phase_log_new_phase
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event_phase_log
        ADD CONSTRAINT ck_canonical_event_phase_log_new_phase
        CHECK (new_phase IN ('proposed', 'listed', 'pre_event', 'live',
            'suspended', 'settling', 'resolved', 'voided'))
        """
    )

    # Step 7: restore canonical_events.lifecycle_phase 8-value CHECK
    op.execute(
        """
        ALTER TABLE canonical_events
        DROP CONSTRAINT canonical_events_lifecycle_phase_check
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_events
        ADD CONSTRAINT canonical_events_lifecycle_phase_check
        CHECK (lifecycle_phase IN ('proposed', 'listed', 'pre_event', 'live',
            'suspended', 'settling', 'resolved', 'voided'))
        """
    )

    # Step 8: drop the canonical_markets.lifecycle_phase column.  CASCADE drops
    # the inline CHECK automatically (column-attached constraints follow the
    # column out of existence).
    op.execute("ALTER TABLE canonical_markets DROP COLUMN lifecycle_phase")
