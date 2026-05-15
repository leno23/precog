"""Cohort 5+ Slot B -- ``canonical_event_match_log`` audit ledger +
``canonical_events.created_by`` provenance column + ``match_algorithm``
matcher seed row.

First new-architecture migration after Migration 0090's platform-prefix
rename (PR #1183) -- launches Cohort 5+ proper by landing the
infrastructure the canonical-event matcher needs to operate.  Slot A
(retroactively claimed) was Migration 0090; Slot B is THIS migration +
the matcher module that ships alongside it.

Per session-92 4-agent design council (Galadriel + Holden + Miles +
Uhura) + session-107 PM rebase addendum at
``memory/build_spec_slot_b_matcher_addendum_session_107.md`` (parent
spec ``memory/build_spec_slot_a_matcher_pm_memo.md`` + addendum
rebases for Migration 0090 platform-prefix rename, lifecycle_phase
5-value collapse, alembic head advance 0084 -> 0090).  S82 verdict:
**INHERITED + addendum** (3rd S82-INHERITED outcome project-wide).

Slot B ships THREE schema changes in one migration:

    1. CREATE TABLE ``canonical_event_match_log`` (NEW; 5th audit
       ledger; mirrors slot 0073's ``canonical_match_log`` shape but
       event-tier).  Append-only via application discipline (slot 0073
       precedent); trigger-enforced append-only deferred to a future
       slot after 30-day soak.
    2. ALTER TABLE ``canonical_events`` ADD COLUMN ``created_by``
       (Miles ❌ resolution from session-92 council; provenance for
       every canonical_events row identifying the writer).
    3. INSERT new ``match_algorithm`` row registering
       ``cohort5_event_matcher_v1`` (parent spec § File 1 step 3).

Append-only via application discipline (slot 0073 precedent):

    Same shape as ``canonical_match_log`` (slot 0073) +
    ``canonical_event_phase_log`` (slot 0079):

        * NO ``BEFORE INSERT OR UPDATE OR DELETE`` trigger on the table
          itself.  Direct UPDATE/DELETE SQL would succeed at the DB
          level.  Discipline lives at the CRUD layer:
          ``crud_canonical_event_match_log.py`` exposes EXACTLY ONE
          write function (``append_event_match_log_row()``) and zero
          ``update_*`` / ``delete_*`` / ``upsert_*`` helpers.
        * Code review enforces the discipline; future S81 grep audits
          sweep for direct ``UPDATE canonical_event_match_log`` /
          ``DELETE FROM canonical_event_match_log`` SQL outside this
          migration's downgrade.
        * Trigger-enforced version (BEFORE UPDATE/DELETE -> RAISE
          EXCEPTION) is queued for a future slot after 30-day soak
          validates the application-discipline approach (slot 0073 +
          0079 precedent inheritance).

Pattern 73 (SSOT) -- new ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES`` constant:

    The 6-value ``action`` vocabulary is slightly different from slot
    0073's 7-value ``ACTION_VALUES`` (which is market-tier).  Slot B's
    event-tier vocabulary:

        ``create``         -- new canonical_events row + canonical_event_link
                              created (matcher's primary path).
        ``retire``         -- existing canonical_event_link retired
                              (matcher detected stale binding).
        ``update_phase``   -- canonical_events.lifecycle_phase advanced
                              (RESERVED: matcher does NOT advance
                              phase in Slot B scope; reserved for
                              downstream lifecycle slot).
        ``review_approve`` -- canonical_match_reviews row resolved
                              to approved (operator-driven).
        ``review_reject``  -- canonical_match_reviews row resolved
                              to rejected (operator-driven).
        ``quarantine``     -- canonical_event_link transitioned to
                              quarantined (matcher detected uncertainty).

    Canonical home: ``src/precog/database/constants.py``
    ``CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES``.  CRUD-layer real-guard
    validation uses this constant.  ``DECIDED_BY_PREFIXES`` (slot 0073)
    is reused unchanged for the ``decided_by`` column.

Pattern 73 (SSOT) -- ``CREATED_BY_PREFIXES`` for canonical_events.created_by:

    Mirrors ``DECIDED_BY_PREFIXES`` shape.  Three prefixes cover every
    write origin:

        ``matcher:slot-B:v1``       -- steady-state matcher writes.
        ``cli:matcher-backfill:v1`` -- one-time backfill CLI writes.
        ``legacy:pre-matcher``      -- default for any pre-existing
                                       rows at migration apply time
                                       (canonical_events has 0 rows in
                                       dev at migration time per MCP
                                       probe; this default keeps the
                                       NOT NULL contract correct if a
                                       future fixture path ever inserts
                                       a row before the matcher
                                       activates).

    The DEFAULT clause on the ADD COLUMN is intentionally PERMISSIVE
    (a default exists) -- after upgrade-to-head we keep the default in
    place so test fixtures and pre-matcher INSERT paths don't have to
    specify ``created_by`` manually.  Production paths (matcher +
    backfill CLI) ALWAYS write an explicit ``created_by`` value via
    the CRUD layer; the default is the safety net, not the contract.

FK polarity rationale (parent spec § File 1 + Holden P1 catch
analogy from slot 0073):

    ``canonical_event_match_log.canonical_event_id``:
        ``BIGINT NULL REFERENCES canonical_events(id) ON DELETE SET NULL``
        -- audit history outlives canonical_events deletion (parent
        spec line 75-77 + slot 0073 ``canonical_market_id`` SET NULL
        precedent).  NULL also allowed for the ``create`` action where
        the canonical_event_id IS the row JUST created (the row
        reference lands AFTER the INSERT, in the same transaction).

    ``canonical_event_match_log.link_id``:
        ``BIGINT NULL REFERENCES canonical_event_links(id) ON DELETE SET NULL``
        -- audit history outlives link deletion (slot 0073 ``link_id``
        SET NULL precedent + ADR-118 V2.42 sub-amendment B audit-
        survival semantics applied to event-tier).

    ``canonical_event_match_log.platform_event_id``:
        ``INTEGER NULL REFERENCES platform_events(id) ON DELETE CASCADE``
        -- mirrors ``canonical_event_links.platform_event_id`` polarity
        which is CASCADE per Migration 0072 + Migration 0090
        auto-rename.  Different from slot 0073's ``platform_market_id``
        (deliberately NO FK / NOT NULL).  Slot 0073's L9 framing (log
        outlives the platform row) is appropriate for market-tier where
        re-keying is routine; for event-tier under Migration 0090's
        platform-prefix rename the FK polarity is uniformly CASCADE
        from canonical_event_links, and parallel application keeps the
        audit-log shape consistent with the link table it audits.
        NULL allowed for human-operator audit rows that don't carry
        a platform-tier anchor.

    ``canonical_event_match_log.algorithm_id``:
        ``BIGINT NOT NULL REFERENCES match_algorithm(id)`` -- every
        log row has an algorithm pointer.  The ``manual_v1`` algorithm
        (Migration 0071 seed, id=1) is the placeholder for human-
        decided rows; ``cohort5_event_matcher_v1`` (THIS migration's
        seed, id=2 after apply) is the matcher service's algorithm row.

    ``canonical_event_match_log.prior_link_id``:
        ``BIGINT NULL REFERENCES canonical_event_links(id) ON DELETE SET NULL``
        -- mirrors ``link_id`` polarity (slot 0073 ``prior_link_id``
        Holden P3 deliberate spec-strengthening; parallel application
        for slot B's event tier).

Pattern 81 (lookup convention) -- N/A carve-out for ``action``:

    The 6-value action set is closed (every value binds to matcher
    state-machine branches per Pattern 81 § "When NOT to Apply"); not
    a Pattern 81 lookup table.  Same carve-out shape as slot 0073's
    ``ACTION_VALUES`` and slot 0079's lifecycle_phase vocabulary.

Pattern 84 (NOT VALID + VALIDATE on populated tables) -- APPLIED FOR
``canonical_events.created_by``:

    ``canonical_events`` has 0 rows in dev at migration apply time per
    MCP probe (Pattern 91 V1.44 self-application).  The ADD COLUMN ...
    NOT NULL DEFAULT path is safe at zero-row scale because the table
    rewrite is trivial.  Production deploy: canonical_events is
    expected to still be near-empty until matcher backfill activates
    in session 108+; Pattern 84 NOT VALID + VALIDATE not required for
    this slot.  If a future cohort tightens ``created_by`` (e.g., drop
    the default to force explicit specification), Pattern 84 applies
    THEN.

Pattern 87 (Append-only migrations) -- REAFFIRMED CLEAN:

    DEVELOPMENT_PATTERNS V1.40.  Slot B is a NEW migration; Pattern 87
    fires when editing PREVIOUSLY-MERGED migrations.  This PR makes
    ZERO edits to migrations 0001-0090.  No forward-pointer comment is
    inserted into any shipped migration's docstring.

Round-trip discipline (PR #1081 round-trip CI gate):

    Slot B's ``downgrade()`` is a pure inverse of ``upgrade()``: every
    CREATE has a matching ``DROP IF EXISTS`` in downgrade.  Drop order
    respects object dependencies (indexes -> log table -> column ->
    seed row).  ``IF EXISTS`` used throughout for idempotent rollback
    per ``feedback_idempotent_migration_drops.md`` (session 59).  The
    round-trip gate auto-discovers slot B on push and runs ``downgrade
    -> upgrade head`` against it.

    The downgrade is intentionally lossy: the audit ledger contents
    are discarded; the ``created_by`` column is removed; the matcher
    algorithm seed row is removed.  This is by design;
    upgrade-then-downgrade-then-upgrade is the supported cycle (round-
    trip CI gate), not downgrade-and-keep-running on a populated
    production DB.

What slot B deliberately does NOT include (scope fence):

    * No append-only enforcement trigger on the log table itself --
      application-discipline only; trigger retrofit queued for a
      future slot after 30-day soak (slot 0073 + 0079 precedent).
    * No ``BEFORE UPDATE`` trigger for ``updated_at`` maintenance --
      append-only tables don't have ``updated_at`` column at all
      (the ``decided_at`` and ``created_at`` columns are write-once
      by definition).
    * No BACKFILL of canonical_events.created_by from existing rows --
      canonical_events has 0 rows at production deploy time per the
      MCP probe; the DEFAULT clause fills any future fixture inserts.
    * No NOT NULL tightening on existing canonical_events columns --
      out of scope; ``created_by`` is the only column added.
    * No view rewires -- slot B is pure schema landing + audit infra.
    * No matcher application code -- ships in
      ``src/precog/matching/canonical_event_matcher.py`` (separate
      file in the same PR for atomic deployability, but logically
      decoupled from this migration's DDL).
    * No NOT NULL on ``canonical_event_match_log.canonical_event_id``
      -- NULL allowed for ``create`` action rows where the
      canonical_event_id has just been INSERTed in the same
      transaction (the audit row records the decision; the FK can
      land as either NULL or the freshly-created id depending on the
      caller's ordering preference).
    * No NOT NULL on ``platform_event_id`` -- NULL allowed for
      operator-only audit rows that don't carry a platform-tier
      anchor (e.g., manual phase advancement audit notes).

Revision ID: 0091
Revises: 0090
Create Date: 2026-05-15

Issues: Epic #972 (Canonical Layer Foundation -- Phase B.5),
    #1184 Item 4 (Cohort 5+ Slot B matcher dispatch)
ADR: ADR-118 V2.44 (atomicity contract) + V2.46 (canonical layer
    relationships) + V2.49 (OQ-H1 NORMALIZE)
Build spec: ``memory/build_spec_slot_a_matcher_pm_memo.md`` (session 92)
Addendum: ``memory/build_spec_slot_b_matcher_addendum_session_107.md``
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0091"
down_revision: str = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical_event_match_log + ADD canonical_events.created_by + seed matcher algorithm row.

    Step order:

        1. ``canonical_event_match_log`` table (CREATE TABLE with all
           FK clauses + 2 inline CHECK constraints).
        2. 3 indexes on the new table for matcher heartbeat / operator
           visibility queries (decided_at DESC, canonical_event_id
           partial, action composite).
        3. ALTER TABLE canonical_events ADD COLUMN created_by NOT
           NULL DEFAULT 'legacy:pre-matcher' (Miles ❌ resolution).
        4. INSERT new match_algorithm row registering
           ``cohort5_event_matcher_v1`` (parent spec § File 1 step 3).
    """
    # =========================================================================
    # Step 1: canonical_event_match_log
    #
    # Column-level rationale (Builder docstring obligation per slot
    # 0073 + 0079 precedent):
    #
    #   - id BIGSERIAL PK: surrogate.  No child rows FK INTO this
    #     table; the PK exists for direct-row addressability in
    #     operator runbooks.
    #   - canonical_event_id BIGINT NULL ON DELETE SET NULL: audit log
    #     outlives event deletion.  NULL allowed for create-action
    #     rows where the canonical_event_id is the just-INSERTed row
    #     (within the same transaction).
    #   - link_id BIGINT NULL ON DELETE SET NULL: audit log outlives
    #     link deletion (slot 0073 link_id precedent).
    #   - platform_event_id INTEGER NULL ON DELETE CASCADE: mirrors
    #     canonical_event_links.platform_event_id polarity.  NULL
    #     allowed for human-only audit rows.
    #   - action VARCHAR(16) NOT NULL CHECK: 6-value event-tier vocab.
    #     Pattern 73 SSOT pointer to
    #     constants.py:CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES.
    #   - confidence NUMERIC(4,3) NULL CHECK: nullable because human
    #     overrides have no algorithmic confidence (slot 0073
    #     precedent).  Decimal-only per CLAUDE.md Critical Pattern #1.
    #   - algorithm_id BIGINT NOT NULL REFERENCES match_algorithm(id):
    #     every log row has an algorithm pointer.
    #   - features JSONB NULL: free-form input snapshot at decision
    #     time (slot 0073 precedent).
    #   - prior_link_id BIGINT NULL ON DELETE SET NULL: for relink /
    #     unlink rows, the predecessor link being superseded.
    #   - decided_by VARCHAR(64) NOT NULL: actor attribution.  Pattern
    #     73 SSOT pointer to constants.py:DECIDED_BY_PREFIXES.
    #   - decided_at TIMESTAMPTZ NOT NULL DEFAULT now(): canonical
    #     decision timestamp; drives operator audit hot-path ORDER BY.
    #   - note TEXT NULL: free-text operator-readable explanation.
    #   - created_at TIMESTAMPTZ NOT NULL DEFAULT now(): ADR-118 V2.42
    #     sub-amendment A canonical convention.  By convention
    #     created_at == decided_at on this table (within microseconds).
    #
    # NO updated_at column -- append-only; rows are write-once.
    # =========================================================================
    op.execute(
        """
        CREATE TABLE canonical_event_match_log (
            id                    BIGSERIAL    PRIMARY KEY,
            -- canonical_event_id NULL allowed for create-action rows where
            -- the row was just INSERTed in the same transaction.  ON DELETE
            -- SET NULL preserves audit history when event is deleted (rare).
            canonical_event_id    BIGINT       NULL REFERENCES canonical_events(id) ON DELETE SET NULL,
            -- link_id ON DELETE SET NULL per slot 0073 link_id precedent:
            -- audit history outlives link deletion.
            link_id               BIGINT       NULL REFERENCES canonical_event_links(id) ON DELETE SET NULL,
            -- platform_event_id ON DELETE CASCADE mirrors canonical_event_links
            -- polarity (Migration 0072 + 0090 auto-rename to platform_events).
            -- NULL allowed for human-only audit rows.
            platform_event_id     INTEGER      NULL REFERENCES platform_events(id) ON DELETE CASCADE,
            -- action vocabulary canonical home:
            -- src/precog/database/constants.py CANONICAL_EVENT_MATCH_LOG_ACTION_VALUES.
            -- 6-value event-tier vocab (different from slot 0073's 7-value
            -- market-tier ACTION_VALUES).
            action                VARCHAR(16)  NOT NULL,
            -- confidence nullable per slot 0073 precedent (human overrides
            -- have no algorithmic confidence).  CHECK uses NULL-tolerant form.
            confidence            NUMERIC(4,3) NULL,
            -- algorithm_id NOT NULL; matcher writes use cohort5_event_matcher_v1
            -- (seeded below); human overrides use manual_v1 (Migration 0071 seed).
            algorithm_id          BIGINT       NOT NULL REFERENCES match_algorithm(id),
            features              JSONB        NULL,
            -- prior_link_id ON DELETE SET NULL per slot 0073 Holden P3
            -- deliberate spec-strengthening (parallel application to event tier).
            prior_link_id         BIGINT       NULL REFERENCES canonical_event_links(id) ON DELETE SET NULL,
            -- decided_by value-set canonical home:
            -- src/precog/database/constants.py DECIDED_BY_PREFIXES.
            -- CHECK does NOT enforce format (free-text; CRUD validation).
            decided_by            VARCHAR(64)  NOT NULL,
            decided_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            note                  TEXT         NULL,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT ck_canonical_event_match_log_action CHECK (action IN (
                'create', 'retire', 'update_phase',
                'review_approve', 'review_reject', 'quarantine'
            )),
            CONSTRAINT ck_canonical_event_match_log_confidence CHECK (
                confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
            )
        )
        """
    )

    # =========================================================================
    # Step 2: 3 indexes on canonical_event_match_log
    #
    # Index strategy mirrors slot 0073's 4-index shape, slimmed to 3 indexes
    # because slot B does not have the same "by platform_market_id" alert
    # query catalog that slot 0073 had (the canonical equivalent for the
    # event tier is the canonical_event_id composite below).
    # =========================================================================

    # Operator audit hot-path: ORDER BY decided_at DESC dominates
    # operator runbook queries.  DESC index avoids server-side reverse-scan.
    op.execute(
        "CREATE INDEX idx_canonical_event_match_log_decided_at "
        "ON canonical_event_match_log (decided_at DESC)"
    )

    # Partial index on canonical_event_id IS NOT NULL: supports the
    # canonical operator runbook query "show me the decision history
    # for event X".  Partial form keeps the index lean (NULL rows are
    # create-action rows that already join through their fresh-INSERT
    # canonical_event_id once the caller stores it).
    op.execute(
        "CREATE INDEX idx_canonical_event_match_log_canonical_event_id "
        "ON canonical_event_match_log (canonical_event_id) "
        "WHERE canonical_event_id IS NOT NULL"
    )

    # Composite index on (action, decided_at DESC): supports the matcher's
    # operator-alert-query catalog -- "how many create actions in the last
    # hour?", "any quarantine actions since last operator handoff?".
    op.execute(
        "CREATE INDEX idx_canonical_event_match_log_action "
        "ON canonical_event_match_log (action, decided_at DESC)"
    )

    # =========================================================================
    # Step 3: canonical_events.created_by
    #
    # Miles ❌ resolution from session-92 council.  NOT NULL DEFAULT covers
    # any pre-existing rows in dev/staging (0 rows verified via MCP probe at
    # session 107).  Production-tier callers (matcher + backfill CLI) ALWAYS
    # specify created_by explicitly; the DEFAULT is the safety net for test
    # fixtures and legacy paths.
    #
    # 5-prefix vocabulary (closed; matches CREATED_BY_PREFIXES constant):
    #   matcher:slot-B:v1            -- steady-state matcher writes
    #   cli:matcher-backfill:v1      -- backfill CLI writes
    #   legacy:pre-matcher           -- DEFAULT for pre-existing rows
    #   human:<username>             -- operator-driven fixture inserts
    #   system:<context>             -- test fixtures / migrations
    #
    # No CHECK constraint on created_by because string-format validation is
    # Pattern 81 non-application territory (free-text actor field; slot
    # 0073 precedent).  CRUD-layer real-guard validation in create_canonical_event
    # is the discipline.
    # =========================================================================
    op.execute(
        """
        ALTER TABLE canonical_events
            ADD COLUMN created_by VARCHAR(64) NOT NULL DEFAULT 'legacy:pre-matcher'
        """
    )

    # =========================================================================
    # Step 4: seed match_algorithm row for cohort5_event_matcher_v1
    #
    # Parent spec § File 1 step 3 (with market_snapshots ->
    # platform_market_snapshots substitution per addendum § Δ2).  The
    # algorithm row's id is allocated by BIGSERIAL; matcher code resolves
    # the id at startup via SELECT name='cohort5_event_matcher_v1'.
    # =========================================================================
    op.execute(
        """
        INSERT INTO match_algorithm (name, version, code_ref, description) VALUES (
            'cohort5_event_matcher_v1',
            '1.0.0',
            'precog.matching.canonical_event_matcher',
            'Cohort 5+ matcher: creates canonical_events from upstream source state '
            '(games + game_states + platform_market_snapshots) with V2.44 atomicity contract. '
            'Operates in steady-state (pull-poller) and backfill (opt-in CLI) modes.'
        )
        """
    )


def downgrade() -> None:
    """Reverse 0091: drop log table indexes + canonical_event_match_log + matcher seed row + canonical_events.created_by.

    Drop order (FK-safe — see PR #1193 Glokta P1-2 catch):

        1. DROP 3 indexes on canonical_event_match_log (explicit drops for
           parity with slot 0073 / 0079 convention; DROP TABLE would
           cascade them but explicit ordering keeps the downgrade audit-
           friendly).
        2. DROP canonical_event_match_log table.  This MUST precede the
           seed-row DELETE because canonical_event_match_log.algorithm_id
           is ``BIGINT NOT NULL REFERENCES match_algorithm(id)`` with
           default ``ON DELETE NO ACTION`` — any log row referencing
           ``cohort5_event_matcher_v1`` would block the seed DELETE with
           an FK violation.  Round-trip CI gates pass on freshly-built
           DBs (zero log rows) but production downgrade after matcher
           activation would fail without this reordering.
        3. DELETE matcher seed row from match_algorithm.  Now safe — all
           FK references are gone with the log table.
        4. DROP canonical_events.created_by column.

    ``IF EXISTS`` used throughout for idempotent rollback per session 59
    ``feedback_idempotent_migration_drops.md``.

    The downgrade is intentionally lossy: audit ledger contents are
    discarded; canonical_events.created_by values are lost; matcher
    seed row is removed.  Upgrade-then-downgrade-then-upgrade is the
    supported cycle (round-trip CI gate per PR #1081); downgrade-and-
    keep-running on a populated production DB requires a separate
    backup/restore drill per Epic #1071.
    """
    # Step 1: drop 3 indexes on canonical_event_match_log.
    op.execute("DROP INDEX IF EXISTS idx_canonical_event_match_log_action")
    op.execute("DROP INDEX IF EXISTS idx_canonical_event_match_log_canonical_event_id")
    op.execute("DROP INDEX IF EXISTS idx_canonical_event_match_log_decided_at")

    # Step 2: drop canonical_event_match_log table BEFORE seed DELETE so
    # algorithm_id FK references go away first (Glokta P1-2; see docstring).
    op.execute("DROP TABLE IF EXISTS canonical_event_match_log")

    # Step 3: remove matcher seed row from match_algorithm.  Delete by
    # (name, version) which is the row's UNIQUE constraint (Migration 0071).
    op.execute(
        "DELETE FROM match_algorithm WHERE name = 'cohort5_event_matcher_v1' AND version = '1.0.0'"
    )

    # Step 4: drop canonical_events.created_by column.  IF EXISTS for
    # idempotent rollback.
    op.execute("ALTER TABLE canonical_events DROP COLUMN IF EXISTS created_by")
