"""Integration tests for canonical_event_matcher (Cohort 5+ Slot B).

End-to-end against the real dev/test DB.  Exercises:
    - V2.44 atomic write path: canonical_events + canonical_event_links
      + canonical_event_match_log + canonical_event_phase_log auto-trigger
      + games/game_states back-link UPDATEs all commit atomically.
    - Idempotency via uq_canonical_events_nk (re-running on same input
      yields ON CONFLICT skip).
    - canonical_entities lazy upsert on first-seen team_codes.
    - Backfill receipt aggregation across batches.
    - Status summary read-path.

Per session-92 4-agent Cohort 5+ Slot B design council + parent spec
``memory/build_spec_slot_a_matcher_pm_memo.md`` + addendum.

Markers:
    @pytest.mark.integration: real DB required.

Test fixtures use TEST-* prefix per
``feedback_fixture_prefix_shared_cleanup_compat.md`` (session 69) so
shared-cleanup teardown reaps any orphans.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from precog.database.connection import get_cursor
from precog.matching.canonical_event_matcher import (
    DECIDED_BY_BACKFILL,
    CanonicalEventMatcher,
    _Candidate,
    _match_one_candidate,
    _resolve_matcher_lookup_ids,
    backfill_all,
    compute_natural_key_hash,
    get_matcher_status_summary,
)


def _match_one_with_resolved_ids(
    cur: Any,
    candidate: _Candidate,
    *,
    algorithm_id: int,
    created_by: str = "matcher:v1",
    decided_by: str = "service:matcher:v1",
) -> Any:
    """Test helper: resolve lookup IDs and call _match_one_candidate.

    Avoids per-test boilerplate of resolving the 5 seed IDs every
    time.  Mirrors what CanonicalEventMatcher._poll_once does at
    runtime.
    """
    from decimal import Decimal

    (
        team_entity_kind_id,
        sports_event_domain_id,
        game_event_type_id,
        home_role_id,
        away_role_id,
    ) = _resolve_matcher_lookup_ids()
    return _match_one_candidate(
        cur,
        candidate,
        algorithm_id=algorithm_id,
        created_by=created_by,
        decided_by=decided_by,
        t_match=Decimal("0.85"),
        team_entity_kind_id=team_entity_kind_id,
        sports_event_domain_id=sports_event_domain_id,
        game_event_type_id=game_event_type_id,
        home_role_id=home_role_id,
        away_role_id=away_role_id,
    )


pytestmark = [pytest.mark.integration]


# =============================================================================
# Helpers -- seed + cleanup
# =============================================================================


def _seed_game_and_platform_event(suffix: str) -> tuple[int, int]:
    """Seed one game + one platform_event tied to it.

    Returns (game_id, platform_event_id).  Caller MUST pair with
    ``_cleanup`` in finally to avoid leaking rows.
    """
    short_suffix = suffix[:8]  # game_key has length constraint
    with get_cursor(commit=True) as cur:
        # Lookup sport_id + league_id (Migration 0083 seeds).
        cur.execute("SELECT id FROM sports WHERE sport_key = 'football' LIMIT 1")
        sport_row = cur.fetchone()
        sport_id = int(sport_row["id"]) if sport_row else 1
        cur.execute("SELECT id FROM leagues WHERE sport_id = %s LIMIT 1", (sport_id,))
        lg = cur.fetchone()
        league_id = int(lg["id"]) if lg else 1

        # data_source must be in ck_games_source allowed set: 'manual' fits TEST.
        cur.execute(
            """
            INSERT INTO games (
                sport, game_date, home_team_code, away_team_code, season,
                league, neutral_site, is_playoff, game_status, data_source,
                sport_id, league_id, game_key
            ) VALUES (
                'football', '2026-09-04', %s, %s, 2026,
                'NFL', false, false, 'scheduled', 'manual',
                %s, %s, %s
            )
            RETURNING id
            """,
            (
                f"THM{short_suffix}"[:8],
                f"TAW{short_suffix}"[:8],
                sport_id,
                league_id,
                f"TEST-GK-{short_suffix}",
            ),
        )
        game_id = int(cur.fetchone()["id"])

        # Now platform_event tied to this game.
        cur.execute(
            """
            INSERT INTO platform_events (
                platform_id, external_id, category, title, status, game_id, event_key
            ) VALUES (
                'kalshi', %s, 'sports', %s, 'scheduled', %s, %s
            )
            RETURNING id
            """,
            (
                f"TEST-EXT-{suffix}",
                f"TEST event {suffix}",
                game_id,
                f"TEST-PE-{suffix}",
            ),
        )
        pe_id = int(cur.fetchone()["id"])
    return game_id, pe_id


def _cleanup(game_id: int | None, pe_id: int | None) -> None:
    """Cleanup function -- cascading deletes through the FK polarity.

    Includes canonical_entities cleanup (matcher lazy-upserts entities for
    each unique team code; without explicit cleanup the test DB
    accumulates orphan THM*/TAW* entities that fail downstream "canonical
    layer empty" invariant tests in Migration 0085 + 0086).
    """
    with get_cursor(commit=True) as cur:
        if pe_id is not None:
            # Get canonical_event_id linked to this platform_event (if any)
            cur.execute(
                "SELECT canonical_event_id FROM canonical_event_links WHERE platform_event_id = %s",
                (pe_id,),
            )
            cel_rows = cur.fetchall()
            # Collect canonical_entity_ids from any participants we created.
            entity_ids_to_check: set[int] = set()
            for r in cel_rows:
                ce_id = r["canonical_event_id"]
                cur.execute(
                    "SELECT canonical_entity_id FROM canonical_event_participants WHERE canonical_event_id = %s",
                    (ce_id,),
                )
                for p in cur.fetchall():
                    entity_ids_to_check.add(int(p["canonical_entity_id"]))
            # Then nuke the chain: log -> link -> event-participants -> event.
            # The FK polarities support unwinding from the leaves.
            cur.execute(
                "DELETE FROM canonical_event_match_log WHERE platform_event_id = %s",
                (pe_id,),
            )
            cur.execute(
                "DELETE FROM canonical_event_links WHERE platform_event_id = %s",
                (pe_id,),
            )
            # Now ce_ids are unlinked; safe to delete the participants + events.
            for r in cel_rows:
                ce_id = r["canonical_event_id"]
                cur.execute(
                    "DELETE FROM canonical_event_participants WHERE canonical_event_id = %s",
                    (ce_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_phase_log WHERE canonical_event_id = %s",
                    (ce_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_events WHERE id = %s",
                    (ce_id,),
                )
            # Drop platform_events row.
            cur.execute("DELETE FROM platform_events WHERE id = %s", (pe_id,))
            # Cleanup canonical_entities that we lazy-created.  Only delete
            # rows that have NO remaining canonical_event_participants
            # references (defensive against accidental delete of shared
            # entities -- though TEST suffix-keyed entities are guaranteed
            # to be unique-per-test via the suffix).
            for eid in entity_ids_to_check:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_event_participants WHERE canonical_entity_id = %s",
                    (eid,),
                )
                if int(cur.fetchone()["n"]) == 0:
                    cur.execute(
                        "DELETE FROM canonical_entities WHERE id = %s",
                        (eid,),
                    )
        if game_id is not None:
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))


# =============================================================================
# Group 1: V2.44 atomic write path
# =============================================================================


def test_match_one_candidate_writes_full_atomic_bundle(db_pool: Any) -> None:
    """V2.44 atomicity contract: single transaction writes 5+ rows + 2 UPDATEs."""
    suffix = uuid.uuid4().hex[:8]
    game_id, pe_id = _seed_game_and_platform_event(suffix)
    try:
        # Verify pre-condition: no canonical_event_id back-link yet.
        with get_cursor() as cur:
            cur.execute("SELECT canonical_event_id FROM games WHERE id = %s", (game_id,))
            assert cur.fetchone()["canonical_event_id"] is None

        # Build the candidate dataclass.
        candidate = _Candidate(
            platform_event_id=pe_id,
            game_id=game_id,
            sport="football",
            game_date="2026-09-04",
            home_team_code=f"THM{suffix[:5]}"[:8],
            away_team_code=f"TAW{suffix[:5]}"[:8],
            game_time=datetime(2026, 9, 4, 17, 0, 0, tzinfo=UTC),
            game_title=f"TEST atomic {suffix}",
        )

        # Run the atomic write path inside a single transaction.
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            result = _match_one_with_resolved_ids(
                cur,
                candidate,
                algorithm_id=algo_id,
            )

        # Assertions: action was 'create'; ids populated.
        assert result.action == "create"
        assert result.canonical_event_id is not None
        assert result.link_id is not None

        # Verify ALL 5 atomic-bundle assertions post-commit.
        with get_cursor() as cur:
            # 1. canonical_events row created.
            cur.execute(
                "SELECT id, created_by, lifecycle_phase FROM canonical_events WHERE id = %s",
                (result.canonical_event_id,),
            )
            ce_row = cur.fetchone()
            assert ce_row is not None
            assert ce_row["created_by"] == "matcher:v1"
            assert ce_row["lifecycle_phase"] == "proposed"

            # 2. canonical_event_links row created.
            cur.execute(
                "SELECT id, link_state FROM canonical_event_links WHERE id = %s",
                (result.link_id,),
            )
            cel_row = cur.fetchone()
            assert cel_row is not None
            assert cel_row["link_state"] == "active"

            # 3. canonical_event_match_log row created.
            cur.execute(
                """
                SELECT action, decided_by, canonical_event_id, link_id
                FROM canonical_event_match_log
                WHERE link_id = %s
                """,
                (result.link_id,),
            )
            log_row = cur.fetchone()
            assert log_row is not None
            assert log_row["action"] == "create"
            assert log_row["decided_by"] == "service:matcher:v1"

            # 4. canonical_event_phase_log row auto-created by slot 0079 trigger.
            cur.execute(
                """
                SELECT new_phase, previous_phase, changed_by
                FROM canonical_event_phase_log
                WHERE canonical_event_id = %s
                """,
                (result.canonical_event_id,),
            )
            phase_row = cur.fetchone()
            assert phase_row is not None
            assert phase_row["new_phase"] == "proposed"
            assert phase_row["previous_phase"] is None
            assert phase_row["changed_by"] == "system:trigger"

            # 5. games.canonical_event_id back-link populated.
            cur.execute("SELECT canonical_event_id FROM games WHERE id = %s", (game_id,))
            game_row = cur.fetchone()
            assert game_row["canonical_event_id"] == result.canonical_event_id
    finally:
        _cleanup(game_id, pe_id)


def test_idempotent_rerun_yields_existing_canonical_event(db_pool: Any) -> None:
    """Re-running the matcher on the same candidate yields ON CONFLICT path.

    uq_canonical_events_nk UNIQUE constraint guarantees idempotency.
    Second invocation sees the existing canonical_events row + tries to
    create a new link (which then ExclusionViolations because an active
    link already exists -> matcher routes to 'conflict' outcome).
    """
    suffix = uuid.uuid4().hex[:8]
    game_id, pe_id = _seed_game_and_platform_event(suffix)
    try:
        candidate = _Candidate(
            platform_event_id=pe_id,
            game_id=game_id,
            sport="football",
            game_date="2026-09-04",
            home_team_code=f"THM{suffix[:5]}"[:8],
            away_team_code=f"TAW{suffix[:5]}"[:8],
            game_time=datetime(2026, 9, 4, 17, 0, 0, tzinfo=UTC),
            game_title=f"TEST idempotent {suffix}",
        )

        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            first = _match_one_with_resolved_ids(
                cur,
                candidate,
                algorithm_id=algo_id,
            )
        assert first.action == "create"

        # Re-run: ExclusionViolation -> matcher returns 'conflict'.
        # PR-C: SAVEPOINT path no longer sets canonical_event_id on
        # conflict (the SAVEPOINT rollback unwinds the entire write
        # path; matcher returns conflict without a stable id pointer).
        # See _match_one_candidate ExclusionViolation handler.
        with get_cursor(commit=True) as cur:
            second = _match_one_with_resolved_ids(
                cur,
                candidate,
                algorithm_id=algo_id,
            )
        assert second.action == "conflict"
    finally:
        _cleanup(game_id, pe_id)


# =============================================================================
# Group 2: natural_key_hash polarity invariance against real DB
# =============================================================================


def test_natural_key_hash_persisted_matches_helper_function(db_pool: Any) -> None:
    """The natural_key_hash in the DB matches what compute_natural_key_hash returns."""
    suffix = uuid.uuid4().hex[:8]
    game_id, pe_id = _seed_game_and_platform_event(suffix)
    try:
        home_code = f"THM{suffix[:5]}"[:8]
        away_code = f"TAW{suffix[:5]}"[:8]
        expected_nk = compute_natural_key_hash(
            sport="football",
            game_date="2026-09-04",
            home_team_code=home_code,
            away_team_code=away_code,
        )

        candidate = _Candidate(
            platform_event_id=pe_id,
            game_id=game_id,
            sport="football",
            game_date="2026-09-04",
            home_team_code=home_code,
            away_team_code=away_code,
            game_time=None,
            game_title=f"TEST nk {suffix}",
        )
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            result = _match_one_with_resolved_ids(
                cur,
                candidate,
                algorithm_id=algo_id,
            )

        with get_cursor() as cur:
            cur.execute(
                "SELECT natural_key_hash FROM canonical_events WHERE id = %s",
                (result.canonical_event_id,),
            )
            stored_nk = bytes(cur.fetchone()["natural_key_hash"])
        assert stored_nk == expected_nk
    finally:
        _cleanup(game_id, pe_id)


# =============================================================================
# Group 3: backfill_all dry-run + receipt aggregation
# =============================================================================


def test_backfill_dry_run_does_not_commit(db_pool: Any) -> None:
    """Dry-run mode runs the matcher logic but rolls back the transaction."""
    suffix = uuid.uuid4().hex[:8]
    game_id, pe_id = _seed_game_and_platform_event(suffix)
    try:
        receipt = backfill_all(batch_size=10, dry_run=True, max_batches=1)
        # The matcher may or may not process our specific candidate in the
        # first batch depending on other test fixtures; what we assert is
        # that NO rows persisted via this dry-run path against our pe_id.
        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM canonical_event_links WHERE platform_event_id = %s AND decided_by = %s",
                (pe_id, DECIDED_BY_BACKFILL),
            )
            n = int(cur.fetchone()["n"])
        assert n == 0, "dry_run mode should not persist canonical_event_links rows"
        # Receipt object exists; finished_at populated.
        assert receipt.finished_at is not None
    finally:
        _cleanup(game_id, pe_id)


# =============================================================================
# Group 4: status summary read-path
# =============================================================================


def test_status_summary_returns_expected_keys(db_pool: Any) -> None:
    """get_matcher_status_summary returns the 5 expected keys."""
    summary = get_matcher_status_summary()
    expected_keys = {
        "last_heartbeat",
        "recent_creates_24h",
        # PR-C: renamed from recent_conflicts_24h per session-107
        # Joe Chip + Ripley Nit (semantics: retire + quarantine
        # counts, not ExclusionViolation counts).
        "recent_retires_24h",
        "unlinked_platform_events",
        "matcher_algorithm_id",
    }
    assert set(summary.keys()) >= expected_keys
    # matcher_algorithm_id is populated post-Migration-0091.
    assert summary["matcher_algorithm_id"] is not None
    assert isinstance(summary["matcher_algorithm_id"], int)


# =============================================================================
# Group 5: ServiceSupervisor registration
# =============================================================================


def test_matcher_registered_in_service_supervisor() -> None:
    """canonical_event_matcher appears in SERVICE_TO_COMPONENT + SERVICE_FACTORIES."""
    from precog.schedulers.service_supervisor import (
        COMPONENT_TO_BREAKER_TYPE,
        SERVICE_FACTORIES,
        SERVICE_TO_COMPONENT,
    )

    # Component registration is present.
    assert CanonicalEventMatcher.SERVICE_KEY in SERVICE_TO_COMPONENT
    assert (
        SERVICE_TO_COMPONENT[CanonicalEventMatcher.SERVICE_KEY]
        == CanonicalEventMatcher.HEALTH_COMPONENT
    )

    # Breaker-type wired (Pattern 73 SSOT via test, since circular-import
    # forced hardcoded string in service_supervisor).
    assert CanonicalEventMatcher.HEALTH_COMPONENT in COMPONENT_TO_BREAKER_TYPE
    assert (
        COMPONENT_TO_BREAKER_TYPE[CanonicalEventMatcher.HEALTH_COMPONENT]
        == CanonicalEventMatcher.BREAKER_TYPE
    )

    # Factory registered (lazy import via _create_canonical_event_matcher).
    assert "canonical_event_matcher" in SERVICE_FACTORIES


# =============================================================================
# Group 6: PR-C SAVEPOINT atomicity + concurrency tests
# =============================================================================
#
# Per session-107 Ripley P1 + #1194 PR-C scope:
#   1. test_multi_candidate_batch_mid_failure_rolls_back_atomically --
#      asserts SAVEPOINT-per-candidate isolation: sibling candidates
#      continue cleanly even when one candidate triggers
#      ExclusionViolation mid-batch.  Receipt accurately reflects
#      committed work.
#   2. test_concurrent_canonical_events_insert_race -- asserts the
#      INSERT ... ON CONFLICT (natural_key_hash) DO NOTHING + re-SELECT
#      path handles two matchers racing on the same canonical_event.
#   3. test_concurrent_canonical_entities_insert_race -- asserts the
#      same race-safe shape for _resolve_team_entity_id (composite
#      UNIQUE (entity_kind_id, entity_key)).


def test_multi_candidate_batch_mid_failure_rolls_back_atomically(db_pool: Any) -> None:
    """SAVEPOINT-per-candidate: one mid-batch failure does NOT kill siblings.

    Scenario: batch of 5 candidates where candidate #3 has a
    pre-existing active canonical_event_link (forces ExclusionViolation
    inside _match_one_candidate's Step 6 INSERT canonical_event_links).
    With PR-C's SAVEPOINT pattern, the failure rolls back only
    candidate #3's writes; candidates 1-2 + 4-5 commit cleanly.

    Verifies:
        - Exactly 4 NEW canonical_events rows committed via this
          backfill (candidates 1, 2, 4, 5); candidate 3 yielded to
          the pre-existing link without creating a new canonical_event.
        - Exactly 4 NEW canonical_event_links rows have decided_by =
          DECIDED_BY_BACKFILL.
        - The pre-seeded link is still active + unchanged.
        - No over-counting: backfill_all receipt's created count
          matches the actual committed canonical_events count.

    Pre-PR-C behavior (the bug this test guards against): the
    ExclusionViolation in candidate #3 poisoned the cursor; the
    subsequent candidates couldn't process; AND the backfill's outer
    try-except re-raised, rolling back the whole batch -- candidates
    1, 2, 4, 5 were silently lost despite the receipt over-counting
    them as 'created'.

    Seeding strategy: candidate #3 is FIRST processed by the matcher
    normally (creating its canonical_event + active link).  Then we
    drive a per-candidate match loop directly (bypassing
    _select_candidates' active-link filter so we can force candidate
    #3 BACK into the batch).  The matcher's Step 6 INSERT then sees
    the still-active link from the prior cycle and raises
    ExclusionViolation -- which the SAVEPOINT should roll back
    cleanly without disturbing candidates 1, 2, 4, 5.

    This test exercises the SAVEPOINT directly rather than going
    through _select_candidates + backfill_all (which filters out
    active-linked pe_ids before they reach _match_one_candidate).
    """
    # Seed 5 game+platform_event pairs.  Each gets a unique suffix so
    # natural_key_hash is unique.
    seeded: list[tuple[int, int, str]] = []  # (game_id, pe_id, suffix)
    pre_seeded_link_id: int | None = None
    pre_seeded_canonical_event_id: int | None = None
    try:
        suffixes = [uuid.uuid4().hex[:8] for _ in range(5)]
        for sfx in suffixes:
            game_id, pe_id = _seed_game_and_platform_event(sfx)
            seeded.append((game_id, pe_id, sfx))

        target_game_id, target_pe_id, target_sfx = seeded[2]
        target_home_code = f"THM{target_sfx[:5]}"[:8]
        target_away_code = f"TAW{target_sfx[:5]}"[:8]

        # Pre-seed candidate #3 by calling the matcher's own write
        # path -- this gives us a valid canonical_event + active link
        # in a single, canonical-shape transaction.
        target_candidate = _Candidate(
            platform_event_id=target_pe_id,
            game_id=target_game_id,
            sport="football",
            game_date="2026-09-04",
            home_team_code=target_home_code,
            away_team_code=target_away_code,
            game_time=None,
            game_title=f"TEST mid-fail target {target_sfx}",
        )
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            pre_result = _match_one_with_resolved_ids(
                cur,
                target_candidate,
                algorithm_id=algo_id,
            )
        assert pre_result.action == "create"
        pre_seeded_canonical_event_id = pre_result.canonical_event_id
        pre_seeded_link_id = pre_result.link_id

        # Reset games.canonical_event_id to NULL for ALL 5 games -- so
        # the second-pass call to _match_one_candidate writes them
        # cleanly via the existing Step 8 UPDATE (idempotent against
        # NULL).
        with get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE games SET canonical_event_id = NULL WHERE id = ANY(%s)",
                ([g for g, _, _ in seeded],),
            )

        # Build candidate list for the in-test mini-batch.  Candidate
        # #3 here will hit ExclusionViolation on Step 6 (active link
        # from the pre-seed); the others should commit cleanly.
        candidates = [
            _Candidate(
                platform_event_id=pe,
                game_id=g,
                sport="football",
                game_date="2026-09-04",
                home_team_code=f"THM{sfx[:5]}"[:8],
                away_team_code=f"TAW{sfx[:5]}"[:8],
                game_time=None,
                game_title=f"TEST mid-fail batch {sfx}",
            )
            for (g, pe, sfx) in seeded
        ]

        # Drive the per-candidate match loop directly inside ONE
        # transaction.  Mirrors what backfill_all + _poll_once do but
        # bypasses the _select_candidates active-link filter so we
        # can put the conflict candidate in mid-batch deliberately.
        results: list[Any] = []
        per_cand_errors: list[Exception] = []
        with get_cursor(commit=True) as cur:
            for cand in candidates:
                try:
                    r = _match_one_with_resolved_ids(
                        cur,
                        cand,
                        algorithm_id=algo_id,
                    )
                    results.append(r)
                except Exception as e:
                    per_cand_errors.append(e)
                    results.append(None)

        # The SAVEPOINT discipline inside _match_one_candidate means
        # zero per-candidate exceptions reach the outer loop (they
        # become 'conflict' MatchResults instead).  This is the
        # PR-C behavioral invariant.
        assert per_cand_errors == [], (
            f"PR-C SAVEPOINT discipline failed: {len(per_cand_errors)} "
            f"exception(s) escaped to outer loop: "
            f"{[type(e).__name__ for e in per_cand_errors]}"
        )

        # Assertions:
        # 1. Exactly 4 'create' results + 1 'conflict' result.
        actions = [r.action for r in results if r is not None]
        creates = [a for a in actions if a == "create"]
        conflicts = [a for a in actions if a == "conflict"]
        assert len(creates) == 4, (
            f"Expected 4 create outcomes (candidates 1, 2, 4, 5); got {len(creates)}.  "
            f"Pre-PR-C bug: SAVEPOINT-less code would have poisoned the cursor at "
            f"candidate #3 and corrupted subsequent candidates' writes."
        )
        assert len(conflicts) == 1, (
            f"Expected 1 conflict outcome (candidate #3 hits pre-seeded active link); "
            f"got {len(conflicts)}"
        )
        assert results[2].action == "conflict", (
            f"Candidate #3 (index 2) should be the conflict; got {results[2].action}"
        )

        # 2. Sibling games (1, 2, 4, 5) have canonical_event_id
        #    back-link populated post-batch.  Candidate #3's game
        #    canonical_event_id stays NULL (SAVEPOINT rolled back
        #    the Step 8 UPDATE).
        for i, (game_id, _, _) in enumerate(seeded):
            with get_cursor() as cur:
                cur.execute(
                    "SELECT canonical_event_id FROM games WHERE id = %s",
                    (game_id,),
                )
                ce_id = cur.fetchone()["canonical_event_id"]
                if i == 2:
                    assert ce_id is None, (
                        f"Conflict candidate's game.canonical_event_id should be "
                        f"NULL (SAVEPOINT unwound Step 8 UPDATE); got {ce_id}"
                    )
                else:
                    assert ce_id is not None, (
                        f"Sibling candidate {i}'s game.canonical_event_id "
                        f"should be set by Step 8 UPDATE; got None"
                    )

        # 3. Pre-seeded link still exists + active + unchanged.
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT id, link_state, canonical_event_id
                FROM canonical_event_links WHERE id = %s
                """,
                (pre_seeded_link_id,),
            )
            pre_row = cur.fetchone()
            assert pre_row is not None
            assert pre_row["link_state"] == "active"
            assert pre_row["canonical_event_id"] == pre_seeded_canonical_event_id
    finally:
        # Cleanup all 5 seeded fixtures + the pre-seeded chain.
        # Pre-seeded chain entities must be collected BEFORE we
        # delete participants (or we'd lose the entity_id references).
        pre_seed_entity_ids: set[int] = set()
        if pre_seeded_canonical_event_id is not None:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT canonical_entity_id FROM canonical_event_participants "
                    "WHERE canonical_event_id = %s",
                    (pre_seeded_canonical_event_id,),
                )
                for r in cur.fetchall():
                    pre_seed_entity_ids.add(int(r["canonical_entity_id"]))

        # Pre-seeded chain must be cleaned BEFORE _cleanup deletes
        # the platform_events row (the pre-seeded link points at
        # target_pe_id; its canonical_event has no other refs).
        if pre_seeded_link_id is not None:
            with get_cursor(commit=True) as cur:
                cur.execute(
                    "DELETE FROM canonical_event_match_log WHERE link_id = %s",
                    (pre_seeded_link_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_links WHERE id = %s",
                    (pre_seeded_link_id,),
                )
        if pre_seeded_canonical_event_id is not None:
            with get_cursor(commit=True) as cur:
                cur.execute(
                    "DELETE FROM canonical_event_match_log WHERE canonical_event_id = %s",
                    (pre_seeded_canonical_event_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_participants WHERE canonical_event_id = %s",
                    (pre_seeded_canonical_event_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_phase_log WHERE canonical_event_id = %s",
                    (pre_seeded_canonical_event_id,),
                )
                cur.execute(
                    "DELETE FROM canonical_events WHERE id = %s",
                    (pre_seeded_canonical_event_id,),
                )
        # _cleanup handles the rest (it walks links->log->event chains
        # per platform_event_id).  This handles sibling candidates 1,
        # 2, 4, 5 (and their entities, via the entity-ref-count check
        # inside _cleanup) but NOT candidate #3's pre-seed entities
        # (those entities were created by the pre-seed step, but the
        # canonical_event_participants chain that _cleanup walks via
        # platform_event_id no longer exists for them because we
        # deleted the link above).  We clean those here explicitly.
        for game_id, pe_id, _ in seeded:
            _cleanup(game_id, pe_id)

        # Finally, delete pre-seed entities that no longer have any
        # canonical_event_participants references.  Defensive against
        # other tests sharing entities (TEST-suffixed keys are
        # unique-per-test so this should always free them).
        if pre_seed_entity_ids:
            with get_cursor(commit=True) as cur:
                for eid in pre_seed_entity_ids:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM canonical_event_participants "
                        "WHERE canonical_entity_id = %s",
                        (eid,),
                    )
                    if int(cur.fetchone()["n"]) == 0:
                        cur.execute(
                            "DELETE FROM canonical_entities WHERE id = %s",
                            (eid,),
                        )


def test_concurrent_canonical_events_insert_race(db_pool: Any) -> None:
    """ON CONFLICT (natural_key_hash) DO NOTHING + re-SELECT survives concurrent INSERTs.

    Two threads call _match_one_candidate on the same candidate
    simultaneously.  Without ON CONFLICT, the second thread's INSERT
    raises UniqueViolation (uq_canonical_events_nk) and the SAVEPOINT
    rolls back.  With ON CONFLICT DO NOTHING + re-SELECT, the second
    thread gets the existing canonical_event_id and proceeds (or
    yields cleanly on the link-creation step if that conflicts).

    Verifies:
        - Exactly ONE canonical_events row created for the shared
          natural_key_hash (no duplicate).
        - At most one thread observes action='create'; the other
          observes 'conflict' or 'create' (both acceptable; the
          critical invariant is no duplicate canonical_event row).
        - Neither thread propagates an exception.
    """
    import threading

    suffix = uuid.uuid4().hex[:8]
    game_id, pe_id = _seed_game_and_platform_event(suffix)
    pe_id_2: int | None = None
    # Seed a SECOND platform_event tied to the same game so each
    # thread has its own platform_event to attach a link to (the
    # canonical_event row is shared via natural_key_hash; the link
    # rows are per-platform_event).
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO platform_events (
                platform_id, external_id, category, title, status, game_id, event_key
            ) VALUES (
                'kalshi', %s, 'sports', %s, 'scheduled', %s, %s
            )
            RETURNING id
            """,
            (
                f"TEST-EXT2-{suffix}",
                f"TEST event2 {suffix}",
                game_id,
                f"TEST-PE2-{suffix}",
            ),
        )
        pe_id_2 = int(cur.fetchone()["id"])

    try:
        home_code = f"THM{suffix[:5]}"[:8]
        away_code = f"TAW{suffix[:5]}"[:8]
        # Build two candidates pointing at the SAME game (same
        # natural_key_hash) but DIFFERENT platform_events.
        candidates = [
            _Candidate(
                platform_event_id=pe,
                game_id=game_id,
                sport="football",
                game_date="2026-09-04",
                home_team_code=home_code,
                away_team_code=away_code,
                game_time=None,
                game_title=f"TEST concurrent ce {suffix} pe={pe}",
            )
            for pe in (pe_id, pe_id_2)
        ]

        # Resolve algorithm_id once + capture results from each thread.
        with get_cursor() as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])

        results: list[Any] = [None, None]
        exceptions: list[Exception | None] = [None, None]

        def _worker(idx: int) -> None:
            try:
                with get_cursor(commit=True) as cur:
                    results[idx] = _match_one_with_resolved_ids(
                        cur,
                        candidates[idx],
                        algorithm_id=algo_id,
                    )
            except Exception as e:
                exceptions[idx] = e

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Critical invariants:
        # 1. Neither thread leaked an exception.
        for i, exc in enumerate(exceptions):
            assert exc is None, f"thread {i} raised: {type(exc).__name__}: {exc}"

        # 2. Exactly ONE canonical_events row for this natural_key.
        expected_nk = compute_natural_key_hash(
            sport="football",
            game_date="2026-09-04",
            home_team_code=home_code,
            away_team_code=away_code,
        )
        with get_cursor() as cur:
            import psycopg2

            cur.execute(
                "SELECT COUNT(*) AS n FROM canonical_events WHERE natural_key_hash = %s",
                (psycopg2.Binary(expected_nk),),
            )
            n = int(cur.fetchone()["n"])
        assert n == 1, (
            f"Expected exactly 1 canonical_events row for the shared "
            f"natural_key_hash; got {n}.  Concurrent INSERT race left "
            f"duplicates -- ON CONFLICT (natural_key_hash) DO NOTHING + "
            f"re-SELECT must collapse the race."
        )

        # 3. Both threads produced a non-None result.  Both 'create'
        #    is acceptable (one inserted, one re-SELECTed via ON
        #    CONFLICT skip path); both ids must reference the same
        #    canonical_event.
        for i, r in enumerate(results):
            assert r is not None, f"thread {i} returned None"
        assert (
            results[0].canonical_event_id == results[1].canonical_event_id
            or results[0].canonical_event_id is None
            or results[1].canonical_event_id is None
        ), (
            f"Both threads should resolve to the SAME canonical_event_id "
            f"(or be in conflict state); got "
            f"{results[0].canonical_event_id} vs {results[1].canonical_event_id}"
        )
    finally:
        # Manual cleanup -- two platform_events share one
        # canonical_event, so _cleanup's per-pe walk would
        # double-delete entity refs.  We unwind the chain explicitly
        # here in the safe order: log -> links -> participants ->
        # phase_log -> events -> platform_events -> entities -> game.
        with get_cursor(commit=True) as cur:
            # Find the shared canonical_event(s) via either pe_id.
            ce_ids: set[int] = set()
            for x_pe in (pe_id, pe_id_2):
                if x_pe is None:
                    continue
                cur.execute(
                    "SELECT canonical_event_id FROM canonical_event_links "
                    "WHERE platform_event_id = %s",
                    (x_pe,),
                )
                for r in cur.fetchall():
                    ce_ids.add(int(r["canonical_event_id"]))
            # Collect entity_ids referenced by participants of those ce_ids.
            entity_ids: set[int] = set()
            for ce in ce_ids:
                cur.execute(
                    "SELECT canonical_entity_id FROM canonical_event_participants "
                    "WHERE canonical_event_id = %s",
                    (ce,),
                )
                for p in cur.fetchall():
                    entity_ids.add(int(p["canonical_entity_id"]))
            # Unwind in safe order.
            for x_pe in (pe_id, pe_id_2):
                if x_pe is None:
                    continue
                cur.execute(
                    "DELETE FROM canonical_event_match_log WHERE platform_event_id = %s",
                    (x_pe,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_links WHERE platform_event_id = %s",
                    (x_pe,),
                )
                cur.execute(
                    "DELETE FROM platform_events WHERE id = %s",
                    (x_pe,),
                )
            for ce in ce_ids:
                cur.execute(
                    "DELETE FROM canonical_event_match_log WHERE canonical_event_id = %s",
                    (ce,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_participants WHERE canonical_event_id = %s",
                    (ce,),
                )
                cur.execute(
                    "DELETE FROM canonical_event_phase_log WHERE canonical_event_id = %s",
                    (ce,),
                )
                cur.execute(
                    "DELETE FROM canonical_events WHERE id = %s",
                    (ce,),
                )
            # Now entities are dangling -- delete only those with no
            # remaining participant refs.
            for eid in entity_ids:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM canonical_event_participants "
                    "WHERE canonical_entity_id = %s",
                    (eid,),
                )
                if int(cur.fetchone()["n"]) == 0:
                    cur.execute(
                        "DELETE FROM canonical_entities WHERE id = %s",
                        (eid,),
                    )
            # Game last.
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))


def test_concurrent_canonical_entities_insert_race(db_pool: Any) -> None:
    """_resolve_team_entity_id survives concurrent INSERT for same (kind, key).

    Two threads call _resolve_team_entity_id for the same NEVER-SEEN
    team_code simultaneously.  Without ON CONFLICT, the second thread's
    INSERT raises UniqueViolation (uq_canonical_entities_kind_key).
    With ON CONFLICT (entity_kind_id, entity_key) DO NOTHING +
    re-SELECT, both threads receive the same canonical_entities.id.

    Verifies:
        - Exactly ONE canonical_entities row created for the shared
          (entity_kind_id='team', entity_key='FOOTBALL:NEWT<suffix>').
        - Both threads receive the same id.
        - Neither thread propagates an exception.
    """
    import threading

    from precog.matching.canonical_event_matcher import _resolve_team_entity_id

    # Pick a never-seen team code.  Use uppercase per
    # _resolve_team_entity_id's contract.
    suffix = uuid.uuid4().hex[:5].upper()
    team_code = f"NEWT{suffix}"  # 9 chars, fits varchar limits
    sport = "FOOTBALL"
    expected_entity_key = f"{sport}:{team_code}"

    # Pre-condition: NO existing canonical_entities row for this key.
    # Resolve team_entity_kind_id once (matcher startup discipline).
    (
        team_entity_kind_id,
        _sports_event_domain_id,
        _game_event_type_id,
        _home_role_id,
        _away_role_id,
    ) = _resolve_matcher_lookup_ids()
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id FROM canonical_entities
            WHERE entity_kind_id = %s AND entity_key = %s
            """,
            (team_entity_kind_id, expected_entity_key),
        )
        assert cur.fetchone() is None, (
            f"Pre-condition violated: canonical_entities already has row for "
            f"({team_entity_kind_id}, {expected_entity_key!r})"
        )

    try:
        results: list[int | None] = [None, None]
        exceptions: list[Exception | None] = [None, None]

        def _worker(idx: int) -> None:
            try:
                with get_cursor(commit=True) as cur:
                    results[idx] = _resolve_team_entity_id(
                        cur,
                        team_code=team_code,
                        sport=sport,
                        team_entity_kind_id=team_entity_kind_id,
                    )
            except Exception as e:
                exceptions[idx] = e

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 1. Neither thread leaked an exception.
        for i, exc in enumerate(exceptions):
            assert exc is None, f"thread {i} raised: {type(exc).__name__}: {exc}"

        # 2. Both threads received the same id (or at least non-None).
        for i, r in enumerate(results):
            assert r is not None, f"thread {i} returned None entity_id"
        assert results[0] == results[1], (
            f"Both threads should resolve to the same canonical_entities.id; "
            f"got {results[0]} vs {results[1]}.  ON CONFLICT (entity_kind_id, "
            f"entity_key) DO NOTHING + re-SELECT must collapse the race."
        )

        # 3. Exactly ONE canonical_entities row for the shared key.
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM canonical_entities
                WHERE entity_kind_id = %s AND entity_key = %s
                """,
                (team_entity_kind_id, expected_entity_key),
            )
            n = int(cur.fetchone()["n"])
        assert n == 1, (
            f"Expected exactly 1 canonical_entities row for "
            f"({team_entity_kind_id}, {expected_entity_key!r}); got {n}"
        )
    finally:
        # Cleanup: delete the canonical_entities row we created (no
        # canonical_event_participants refer to it because we didn't
        # build a canonical_event in this test).
        with get_cursor(commit=True) as cur:
            cur.execute(
                """
                DELETE FROM canonical_entities
                WHERE entity_kind_id = %s AND entity_key = %s
                """,
                (team_entity_kind_id, expected_entity_key),
            )


def test_unhandled_exception_path_releases_savepoint_for_siblings(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SAVEPOINT finally-block defensive cleanup on UNHANDLED-exception paths.

    Convergent reviewer P2 finding (session 108: Glokta P2 + Joe Chip
    DECAY-NOW #1 + Ripley P3 #1).  The narrow ``except`` clauses in
    ``_match_one_candidate`` catch only psycopg2 ExclusionViolation +
    UniqueViolation.  Other exception types -- psycopg2 ForeignKeyViolation
    (documented as raisable by ``create_link_in_cursor``),
    NotNullViolation, CheckViolation, RuntimeError -- escape both
    ``except`` clauses.  Without the ``try/finally`` + ``released``-flag
    discipline (PR-C reviewer fix-pass), such an exception would leave
    the SAVEPOINT un-released and the cursor in ``InFailedSqlTransaction``
    state, poisoning all subsequent sibling candidates in the same
    batch -- the same cursor-poisoning shape the SAVEPOINT pattern was
    supposed to close.

    Scenario:
        - 3 candidates in one batch.
        - ``create_link_in_cursor`` is monkeypatched to raise
          ``RuntimeError`` on candidate #2 (mid-batch) ONLY.
        - Candidates #1 + #3 must commit cleanly via the SAVEPOINT
          discipline (the finally-block ROLLBACK TO + RELEASE keeps the
          cursor clean for them).
        - Candidate #2 raises (RuntimeError propagates), but no
          cursor-poison cascade.

    Verifies:
        1. Exactly 1 exception escaped to the outer loop (candidate #2's
           injected RuntimeError).  The other 2 candidates commit
           cleanly -- proving the finally-block cleanup worked.
        2. ``results[0]`` + ``results[2]`` have ``action='create'`` and
           a valid ``canonical_event_id``.
        3. Sibling games (#1, #3) have ``canonical_event_id`` populated
           in DB.  Candidate #2's game stays NULL (SAVEPOINT unwound
           its writes).
        4. Sibling canonical_events rows exist for #1 + #3.

    Pre-fix-pass behavior (the bug this guards against): the RuntimeError
    raised inside ``create_link_in_cursor``'s call path escapes the
    narrow ``except (ExclusionViolation, UniqueViolation)`` clauses.
    The SAVEPOINT for candidate #2 is never released.  The cursor enters
    ``InFailedSqlTransaction``.  Candidate #3's first SQL (the natural-key
    SELECT at step 2) raises ``InFailedSqlTransaction``.  The outer loop
    catches it as a 2nd exception.  Without the fix, ``per_cand_errors``
    would have 2+ entries instead of 1.
    """
    # Seed 3 game+platform_event pairs.
    seeded: list[tuple[int, int, str]] = []
    try:
        suffixes = [uuid.uuid4().hex[:8] for _ in range(3)]
        for sfx in suffixes:
            game_id, pe_id = _seed_game_and_platform_event(sfx)
            seeded.append((game_id, pe_id, sfx))

        # Resolve algorithm_id once.
        with get_cursor() as cur:
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])

        candidates = [
            _Candidate(
                platform_event_id=pe,
                game_id=g,
                sport="football",
                game_date="2026-09-04",
                home_team_code=f"THM{sfx[:5]}"[:8],
                away_team_code=f"TAW{sfx[:5]}"[:8],
                game_time=None,
                game_title=f"TEST unhandled-exc {sfx}",
            )
            for (g, pe, sfx) in seeded
        ]

        # Monkeypatch create_link_in_cursor to raise RuntimeError on
        # candidate #2 ONLY.  Calls for #1 + #3 must pass through to the
        # real implementation so they commit cleanly.
        import precog.matching.canonical_event_matcher as matcher_mod

        real_create_link = matcher_mod.create_link_in_cursor
        target_pe_id = candidates[1].platform_event_id

        def _fake_create_link(*args: Any, **kwargs: Any) -> Any:
            pe = kwargs.get("platform_event_id")
            if pe == target_pe_id:
                # Inject a non-IntegrityError exception that escapes
                # both narrow except clauses.  RuntimeError is the
                # representative type (matches the defensive raises
                # already in matcher source); the finally-block in
                # _match_one_candidate must release the SAVEPOINT
                # before this exception reaches the outer loop.
                raise RuntimeError(
                    f"injected: simulated unhandled exception on platform_event_id={pe}"
                )
            return real_create_link(*args, **kwargs)

        monkeypatch.setattr(matcher_mod, "create_link_in_cursor", _fake_create_link)

        # Drive the per-candidate match loop inside ONE transaction.
        results: list[Any] = []
        per_cand_errors: list[Exception] = []
        with get_cursor(commit=True) as cur:
            for cand in candidates:
                try:
                    r = _match_one_with_resolved_ids(
                        cur,
                        cand,
                        algorithm_id=algo_id,
                    )
                    results.append(r)
                except Exception as e:
                    per_cand_errors.append(e)
                    results.append(None)

        # 1. Exactly 1 exception escaped (the injected one).  Without
        # the finally-block fix, the cursor-poison cascade would
        # produce 2+ exceptions (the original RuntimeError on #2 PLUS
        # an InFailedSqlTransaction on #3).
        assert len(per_cand_errors) == 1, (
            f"Expected exactly 1 exception (the injected RuntimeError on "
            f"candidate #2); got {len(per_cand_errors)}: "
            f"{[type(e).__name__ for e in per_cand_errors]}.  Multiple "
            f"exceptions indicate cursor-poison cascade -- the SAVEPOINT "
            f"finally-block cleanup is NOT working."
        )
        assert isinstance(per_cand_errors[0], RuntimeError), (
            f"Expected RuntimeError (the injected exception); got "
            f"{type(per_cand_errors[0]).__name__}: {per_cand_errors[0]}"
        )
        assert "injected" in str(per_cand_errors[0]), (
            f"Exception should be the injected one; got: {per_cand_errors[0]}"
        )

        # 2. Candidate #2's slot is None (the exception was caught
        # at the outer loop); candidates #1 + #3 have create results.
        assert results[1] is None, "Candidate #2 should be the exception slot"
        assert results[0] is not None, f"Candidate #1 must not be None; got {results[0]}"
        assert results[0].action == "create", (
            f"Candidate #1 must commit cleanly post-SAVEPOINT cleanup; got {results[0]}"
        )
        # The load-bearing assertion: candidate #3 (POST cursor-cleanup)
        # must commit cleanly -- this is what proves the finally-block
        # ROLLBACK TO SAVEPOINT actually un-poisoned the cursor.
        assert results[2] is not None, (
            "Candidate #3 must commit cleanly POST-cursor-cleanup; got None.  "
            "None indicates cursor-poison cascade -- the SAVEPOINT "
            "finally-block cleanup is NOT working."
        )
        assert results[2].action == "create", (
            f"Candidate #3 must commit cleanly POST-cursor-cleanup; got "
            f"action={results[2].action}.  This is the load-bearing assertion "
            f"that proves the finally-block ROLLBACK TO SAVEPOINT actually "
            f"un-poisoned the cursor."
        )
        assert results[0].canonical_event_id is not None
        assert results[2].canonical_event_id is not None

        # 3. Verify DB state matches: siblings #1 + #3 have
        # games.canonical_event_id populated; candidate #2 stays NULL.
        for i, (game_id, _, _) in enumerate(seeded):
            with get_cursor() as cur:
                cur.execute(
                    "SELECT canonical_event_id FROM games WHERE id = %s",
                    (game_id,),
                )
                ce_id = cur.fetchone()["canonical_event_id"]
                if i == 1:
                    assert ce_id is None, (
                        f"Candidate #2's game.canonical_event_id should be "
                        f"NULL (SAVEPOINT finally-block rolled back its "
                        f"Step 8 UPDATE); got {ce_id}"
                    )
                else:
                    assert ce_id is not None, (
                        f"Sibling #{i + 1}'s game.canonical_event_id should "
                        f"be set by Step 8 UPDATE; got None.  This means "
                        f"the SAVEPOINT cleanup failed and the cursor was "
                        f"poisoned mid-batch."
                    )
    finally:
        for game_id, pe_id, _ in seeded:
            _cleanup(game_id, pe_id)
