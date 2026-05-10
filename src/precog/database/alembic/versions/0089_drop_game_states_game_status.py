"""Cleanup epic Slot 4 -- DROP COLUMN game_states.game_status (sport-tier denorm cleanup, R5').

Cleanup epic Slot 4 (R5' component per session 98 council).
Source: ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md`` § 4.

Pattern 87: this file is immutable post-merge.

R5' rationale: ``games.game_status`` is authoritative (5,199 of 5,199 rows
carry it independently of game_states; 100% population MCP-verified at
session 99 build time); ``game_states.game_status`` is a per-tick echo
populated for live-poller-tracked games.  The redundancy is a sport-tier
denorm; dropping it closes the actual duplicate the original synthesis was
reaching for.

Spec § 0d-bis B-1 cascade: removing the column requires removing the
``game_status`` kwarg from ``crud_game_states.upsert_game_state`` /
``create_game_state`` / ``game_state_changed`` Python signatures + the
2 production callers (``espn_game_poller.py:1418``,
``seeding_manager.py:765``) + ~10 test files.  Per § 0d-bis B-2,
``espn_game_poller.py:1393`` (which passes ``game_status=`` to
``get_or_create_game()`` writing to the ``games`` table -- still
authoritative) is correct as-is and stays.

R5' SCD2 invariant survives: ``game_states`` change-detection key is
``game_state_key + period + clock_seconds + score deltas`` (per
``idx_game_states_game_state_key_current`` partial-unique index).
``game_status`` was never part of the SCD2 cycle key.

Edge cases (delayed/suspended status semantics for a specific
game_states row): use new ``derive_game_status()`` SSOT helper in
``crud_game_states.py`` to reconstruct from ``period`` + ``clock_seconds``
+ ``linescores`` + ``situation`` JSONB + parent ``games.game_status``.

Constraint name (per § 0d-bis M-1 build-time MCP verification): the live
constraint name is ``game_states_game_status_check`` (not
``ck_game_states_status`` as the build spec § 4 implied).  CASCADE drops
the constraint automatically when the column is dropped; the downgrade
restores the constraint with its live-name preserved.

Downgrade is intentionally lossy (per
``feedback_idempotent_migration_drops.md``): the schema is restored with
DEFAULT 'final' but production data of ``game_status`` is irretrievably
lost after upgrade -- there is no source-of-truth to backfill from at the
``game_states``-row level (the parent ``games.game_status`` does not
distinguish per-tick state).

Revision ID: 0089
Revises: 0088
Create Date: 2026-05-09

Issues: #1155 (canonical-layer simplification epic)
ADR: ADR-118 V2.46 head; V2.47 ships at Slot 5 (session 99)
Build spec: ``memory/build_spec_slot_4_lifecycle_redistribution_pm_memo.md`` § 4
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0089"
down_revision: str = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """DROP COLUMN game_states.game_status (with SELECT * view dependency dance).

    The ``current_game_states`` view (originally shipped in Migration 0001
    + last refreshed in Migration 0044 as ``SELECT * FROM game_states
    WHERE row_current_ind = TRUE``) depends on ``game_status`` because it
    projects every column.  Migration 0044 established the precedent
    discipline for column drops:

        1. DROP VIEW IF EXISTS current_game_states  -- frees the column
        2. ALTER TABLE DROP COLUMN game_status
        3. CREATE OR REPLACE VIEW current_game_states
            (the view now naturally excludes the dropped column)

    The inline CHECK constraint ``game_states_game_status_check`` is
    automatically dropped with the column (column-attached constraint
    discipline).

    ``IF EXISTS`` on view DROP per
    ``feedback_idempotent_migration_drops.md``: re-running the upgrade on
    a partially-rolled-back DB is a no-op rather than a crash.
    """
    # Step 1: Drop the SELECT * view (slot 0044 precedent).
    op.execute("DROP VIEW IF EXISTS current_game_states")

    # Step 2: Drop the column (CHECK constraint cascades).
    op.execute("ALTER TABLE game_states DROP COLUMN IF EXISTS game_status")

    # Step 3: Recreate the view with EXPLICIT column list matching post-0044
    # view shape (i.e., excluding canonical_event_id which was added by
    # Migration 0080 AFTER 0044's CREATE OR REPLACE VIEW SELECT * froze the
    # view's column list).
    #
    # Round-trip parity rationale (Ripley P0-1, session 98 fix-pass):
    #     SELECT * here would capture ALL current game_states columns
    #     INCLUDING canonical_event_id (added by 0080).  The view's column
    #     list is frozen at CREATE-time; the captured dependency would
    #     block 0080.downgrade()'s DROP COLUMN canonical_event_id when the
    #     round-trip CI gate downgrades through 0080.  Pre-Slot-4, the
    #     view's frozen column list (last refreshed at 0044) did NOT
    #     include canonical_event_id, so 0080.downgrade worked.  Explicit
    #     column list here preserves that invariant.
    #
    # No production consumer reads current_game_states.canonical_event_id
    # (verified via grep; no matches).  Callers needing canonical_event_id
    # query game_states directly.  Pattern 87 carve-out applies (this
    # migration is not yet merged; in-flight edit allowed).
    op.execute(
        "CREATE OR REPLACE VIEW current_game_states AS "
        "SELECT id, espn_event_id, home_team_id, away_team_id, venue_id, "
        "home_score, away_score, period, clock_seconds, clock_display, "
        "game_date, broadcast, neutral_site, season_type, week_number, "
        "league, situation, linescores, data_source, "
        "row_start_ts, row_end_ts, row_current_ind, "
        "game_id, league_id, game_state_key "
        "FROM game_states WHERE row_current_ind = TRUE"
    )


def downgrade() -> None:
    """Restore game_states.game_status column with default + 10-value CHECK.

    The downgrade is intentionally lossy at the data layer: the schema
    shape is restored with ``DEFAULT 'final'`` but production data is
    irretrievably lost after upgrade.

    The CHECK constraint is restored under its live name
    ``game_states_game_status_check`` (per § 0d-bis M-1 build-time
    verification of the existing constraint name pre-upgrade) so that
    round-trip parity is byte-equal at the schema level.

    View dependency dance mirrors slot 0044: DROP VIEW -> ADD COLUMN +
    CHECK -> CREATE OR REPLACE VIEW so that the restored view picks up
    the restored column.
    """
    # Step 1: Drop the view so the column add is unobstructed (defensive
    # mirror of upgrade); SELECT * will pick up the new column on recreate.
    op.execute("DROP VIEW IF EXISTS current_game_states")

    # Step 2: Restore column + CHECK constraint with live name.
    op.execute(
        """
        ALTER TABLE game_states
        ADD COLUMN game_status VARCHAR(32) NOT NULL DEFAULT 'final'
        """
    )
    op.execute(
        """
        ALTER TABLE game_states
        ADD CONSTRAINT game_states_game_status_check
        CHECK (game_status IN ('pre', 'in_progress', 'halftime',
            'end_of_period', 'final', 'final_ot', 'delayed',
            'postponed', 'cancelled', 'suspended'))
        """
    )

    # Step 3: Recreate the view with EXPLICIT column list including the
    # restored game_status column.  Mirror of upgrade-direction discipline
    # (Ripley P0-1 fix-pass): excludes canonical_event_id to preserve
    # post-0044 view shape so that 0080.downgrade()'s DROP COLUMN
    # canonical_event_id does not conflict on subsequent round-trip cycles.
    op.execute(
        "CREATE OR REPLACE VIEW current_game_states AS "
        "SELECT id, espn_event_id, home_team_id, away_team_id, venue_id, "
        "home_score, away_score, period, clock_seconds, clock_display, "
        "game_status, game_date, broadcast, neutral_site, season_type, "
        "week_number, league, situation, linescores, data_source, "
        "row_start_ts, row_end_ts, row_current_ind, "
        "game_id, league_id, game_state_key "
        "FROM game_states WHERE row_current_ind = TRUE"
    )
