"""Rename ``match_algorithm`` seed ``cohort5_event_matcher_v1`` ->
``event_matcher_v1`` (drop session-shorthand from production data values).

Cohort 5+ housekeeping migration that scrubs internal session-planning
shorthand out of the canonical-layer matcher's production data values.
Migration 0091 (PR #1195) seeded the matcher's algorithm row under the
name ``cohort5_event_matcher_v1`` because the build was tagged as
"Cohort 5+ Slot B" in session-planning prose; the cohort/slot framing
is internal sequencing language that should not be encoded in a
permanent ``match_algorithm.name`` value.  Per session-110 user
direction (scope B, symmetric), the rename to ``event_matcher_v1`` is
paired with code-side updates to ``CREATED_BY_MATCHER`` and
``DECIDED_BY_MATCHER`` constants in the same PR so the column-value
rename and the in-code identity strings drop ``slot-B`` together (the
matcher has NOT been activated in production yet -- YAML flag still
``false`` -- so no historical rows reference the old strings).

Pattern 87 (Append-Only Migrations) compliance:

    Migration 0091's file is **immutable** per DEVELOPMENT_PATTERNS
    V1.40+.  Its docstring, comments, and SQL literals all retain the
    original ``cohort5_event_matcher_v1`` / ``matcher:slot-B:v1`` /
    ``service:matcher:slot-B:v1`` text as written.  The rename is
    documented HERE in Migration 0092's docstring instead -- this is
    the canonical Pattern-87 pattern (correct decisions in subsequent
    migrations' docstrings, never in shipped migrations).  Session
    79 PR #1063 origin: Glokta proposed a one-line forward-pointer
    comment in shipped Migration 0069; Ripley caught the proposal as
    itself violating the rule (which until then was project-folklore).
    Folklore promoted to Pattern 87 to prevent the next reviewer from
    reasonably proposing the same edit.

Scope (B symmetric):

    The migration UPDATE is paired with code-side changes in the same
    PR (sharable atomic rollout):

        * ``match_algorithm.name``: ``cohort5_event_matcher_v1`` -> ``event_matcher_v1``
          (THIS migration's UPDATE).
        * ``CREATED_BY_MATCHER`` constant (matcher.py:~116):
          ``"matcher:slot-B:v1"`` -> ``"matcher:v1"``.
        * ``DECIDED_BY_MATCHER`` constant (matcher.py:~126):
          ``"service:matcher:slot-B:v1"`` -> ``"service:matcher:v1"``.
        * Helper function rename:
          ``get_cohort5_event_matcher_algorithm_id`` -> ``get_event_matcher_algorithm_id``.
        * All matcher-internal lookup strings + docstring prose
          referencing the old names.
        * Operator runbook + schema docs updated.

    Because the matcher has not been activated in production
    (feature flag ``features.canonical_event_matcher.enabled`` is
    still ``false`` in system.yaml), ``canonical_event_match_log``
    has zero rows and ``canonical_events.created_by`` carries only
    the migration-default ``'legacy:pre-matcher'`` value.  No data
    migration of historical ``decided_by`` / ``created_by`` values
    is required -- the rename is forward-only for new writes.

Idempotency guarantee:

    The UPDATE matches by ``name = 'cohort5_event_matcher_v1' AND
    version = '1.0.0'``.  After the first apply, that WHERE clause
    matches zero rows on a re-run, making the UPDATE a no-op.  The
    seed row's BIGSERIAL ``id`` (= 2) is preserved across the rename
    because we issue an UPDATE rather than DELETE + INSERT -- this is
    load-bearing for ``canonical_event_match_log.algorithm_id`` FK
    references in any future log rows (zero today, but the discipline
    matters for the production cutover).

Round-trip discipline:

    Downgrade reverses the UPDATE.  Both upgrade and downgrade are
    pure data-side UPDATEs (no DDL), so the round-trip 0091 <-> 0092
    is symmetrical and trivially idempotent on re-runs.

Forward-pointer (to Migration 0091's seed row):

    Migration 0091 INSERTed the seed row under the name
    ``cohort5_event_matcher_v1`` (id=2 by BIGSERIAL allocation).
    THIS migration renames that same row to ``event_matcher_v1`` --
    same id, same code_ref, same description, same version.  Migration
    0091's docstring + INSERT statement reference the OLD name and
    must NOT be edited (Pattern 87); future readers tracing the seed
    row's history should expect "0091 INSERT under
    cohort5_event_matcher_v1 + 0092 UPDATE to event_matcher_v1" as
    the canonical sequence.

Revision ID: 0092
Revises: 0091
Create Date: 2026-05-16

Issues: Session 110 user direction (no dedicated issue filed -- mechanical
    rename closing session-planning-leak finding).
ADR: ADR-118 V2.44 (atomicity contract; matcher infrastructure) +
    Pattern 87 (Append-Only Migrations) compliance demonstration.
Build spec: ``memory/build_spec_migration_0092_rename_event_matcher.md``
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0092"
down_revision: str = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename match_algorithm.name from cohort5_event_matcher_v1 -> event_matcher_v1.

    Forward-only data migration (no DDL).  Matches by (name, version)
    which together form the UNIQUE constraint per Migration 0071, so
    at most one row is updated.

    Idempotent on re-runs: subsequent applies match zero rows (the row
    already carries the new name) and the UPDATE is a no-op.

    The seed row's BIGSERIAL ``id`` is preserved (UPDATE, not
    DELETE + INSERT) -- load-bearing for any future
    ``canonical_event_match_log.algorithm_id`` FK references.
    """
    op.execute(
        """
        UPDATE match_algorithm
        SET name = 'event_matcher_v1'
        WHERE name = 'cohort5_event_matcher_v1' AND version = '1.0.0'
        """
    )


def downgrade() -> None:
    """Reverse 0092: rename event_matcher_v1 -> cohort5_event_matcher_v1.

    Symmetric inverse of upgrade.  Same idempotency property: matches
    zero rows on re-runs after the first downgrade.  The seed row's id
    is preserved across the rename.

    Round-trip discipline: round-trip 0091 -> 0092 -> 0091 -> 0092
    is supported (round-trip CI gate per PR #1081 contract).
    """
    op.execute(
        """
        UPDATE match_algorithm
        SET name = 'cohort5_event_matcher_v1'
        WHERE name = 'event_matcher_v1' AND version = '1.0.0'
        """
    )
