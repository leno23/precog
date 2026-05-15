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
    backfill_all,
    compute_natural_key_hash,
    get_matcher_status_summary,
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
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'cohort5_event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            result = _match_one_candidate(
                cur,
                candidate,
                algorithm_id=algo_id,
                created_by="matcher:slot-B:v1",
                decided_by="service:matcher:slot-B:v1",
                t_match=__import__("decimal").Decimal("0.85"),
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
            assert ce_row["created_by"] == "matcher:slot-B:v1"
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
            assert log_row["decided_by"] == "service:matcher:slot-B:v1"

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
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'cohort5_event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            first = _match_one_candidate(
                cur,
                candidate,
                algorithm_id=algo_id,
                created_by="matcher:slot-B:v1",
                decided_by="service:matcher:slot-B:v1",
                t_match=__import__("decimal").Decimal("0.85"),
            )
        assert first.action == "create"

        # Re-run: ExclusionViolation -> matcher returns 'conflict'.
        with get_cursor(commit=True) as cur:
            second = _match_one_candidate(
                cur,
                candidate,
                algorithm_id=algo_id,
                created_by="matcher:slot-B:v1",
                decided_by="service:matcher:slot-B:v1",
                t_match=__import__("decimal").Decimal("0.85"),
            )
        assert second.action == "conflict"
        assert second.canonical_event_id == first.canonical_event_id
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
            cur.execute("SELECT id FROM match_algorithm WHERE name = 'cohort5_event_matcher_v1'")
            algo_id = int(cur.fetchone()["id"])
            result = _match_one_candidate(
                cur,
                candidate,
                algorithm_id=algo_id,
                created_by="matcher:slot-B:v1",
                decided_by="service:matcher:slot-B:v1",
                t_match=__import__("decimal").Decimal("0.85"),
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
        "recent_conflicts_24h",
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
