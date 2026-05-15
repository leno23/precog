"""Unit tests for canonical_event_matcher module -- Cohort 5+ Slot B.

Covers (function-by-function):
    - compute_natural_key_hash: deterministic SHA-256 with home/away
      polarity invariance.
    - _format_resolution_window: tstzrange string construction.
    - MatchResult / BackfillReceipt dataclass behavior.
    - BackfillReceipt.summary_text format.
    - CanonicalEventMatcher class metadata (SERVICE_KEY, etc.).

Pure-unit tests; no DB; integration tests live in
``tests/integration/matching/`` (TODO: separate PR if scope expands).

Reference:
    - ``src/precog/matching/canonical_event_matcher.py``
    - ``memory/build_spec_slot_a_matcher_pm_memo.md``
    - ``memory/build_spec_slot_b_matcher_addendum_session_107.md``
"""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from precog.matching.canonical_event_matcher import (
    CREATED_BY_BACKFILL,
    CREATED_BY_MATCHER,
    DECIDED_BY_BACKFILL,
    DECIDED_BY_MATCHER,
    BackfillReceipt,
    CanonicalEventMatcher,
    MatchResult,
    _format_resolution_window,
    compute_natural_key_hash,
)

# =============================================================================
# Group 1: compute_natural_key_hash -- deterministic + polarity-invariant
# =============================================================================


class TestComputeNaturalKeyHash:
    """SHA-256 of canonical pipe-joined string; home/away polarity invariant."""

    def test_returns_32_byte_sha256_digest(self) -> None:
        nk = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        assert isinstance(nk, bytes)
        assert len(nk) == 32  # SHA-256 output

    def test_home_away_polarity_invariance(self) -> None:
        """Swapping home/away yields the same hash (load-bearing invariant).

        Slot B docstring: same real-world event canonicalizes to the
        same hash regardless of which platform's polarity convention
        surfaced the event first.
        """
        nk_a = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        nk_b = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="BUF",  # swapped
            away_team_code="MIA",
        )
        assert nk_a == nk_b

    def test_case_normalization(self) -> None:
        """sport + team_codes are uppercased before hashing."""
        nk_upper = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        nk_lower = compute_natural_key_hash(
            sport="nfl",
            game_date="2026-09-04",
            home_team_code="mia",
            away_team_code="buf",
        )
        assert nk_upper == nk_lower

    def test_different_dates_produce_different_hashes(self) -> None:
        nk_a = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        nk_b = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-05",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        assert nk_a != nk_b

    def test_known_value_regression(self) -> None:
        """Pin one hash value to detect inadvertent algorithm changes."""
        nk = compute_natural_key_hash(
            sport="NFL",
            game_date="2026-09-04",
            home_team_code="MIA",
            away_team_code="BUF",
        )
        # Compute the expected: SHA-256 of 'NFL|2026-09-04|BUF|MIA'
        # (sorted team codes ascending; BUF before MIA)
        expected = hashlib.sha256(b"NFL|2026-09-04|BUF|MIA").digest()
        assert nk == expected


# =============================================================================
# Group 2: _format_resolution_window -- tstzrange string construction
# =============================================================================


class TestFormatResolutionWindow:
    """tstzrange string with 6-hour envelope from game_time or game_date fallback."""

    def test_uses_game_time_when_present(self) -> None:
        game_time = datetime(2026, 9, 4, 17, 0, 0, tzinfo=UTC)
        result = _format_resolution_window(game_time, "2026-09-04")
        # Should be [game_time, game_time+6h]
        assert result.startswith("[2026-09-04T17:00:00+00:00")
        # 17:00 + 6h = 23:00
        assert "2026-09-04T23:00:00+00:00" in result
        assert result.endswith("]")

    def test_falls_back_to_game_date_midday_when_game_time_null(self) -> None:
        result = _format_resolution_window(None, "2026-09-04")
        # Fallback: noon UTC + 6h = 18:00 UTC
        assert "2026-09-04T12:00:00+00:00" in result
        assert "2026-09-04T18:00:00+00:00" in result


# =============================================================================
# Group 3: MatchResult dataclass
# =============================================================================


class TestMatchResult:
    """MatchResult fields and defaults."""

    def test_minimal_construction(self) -> None:
        r = MatchResult(action="create", platform_event_id=42)
        assert r.action == "create"
        assert r.platform_event_id == 42
        assert r.canonical_event_id is None
        assert r.link_id is None
        assert r.note is None

    def test_full_construction(self) -> None:
        r = MatchResult(
            action="create",
            platform_event_id=42,
            canonical_event_id=7,
            link_id=11,
            natural_key_hash="abc123",
            note="initial match",
        )
        assert r.canonical_event_id == 7
        assert r.link_id == 11
        assert r.natural_key_hash == "abc123"


# =============================================================================
# Group 4: BackfillReceipt -- counts + summary_text format
# =============================================================================


class TestBackfillReceipt:
    """BackfillReceipt aggregates per-action counts; summary_text is operator-readable."""

    def test_zero_counts_default(self) -> None:
        r = BackfillReceipt()
        assert r.created == 0
        assert r.conflicts == 0
        assert r.queued_for_review == 0
        assert r.errors == 0
        assert r.batches_completed == 0
        assert r.error_excerpts == []

    def test_summary_text_includes_counts(self) -> None:
        r = BackfillReceipt(
            created=5,
            conflicts=2,
            queued_for_review=1,
            errors=0,
            batches_completed=3,
        )
        r.finished_at = datetime.now(UTC)
        text = r.summary_text()
        assert "5 created" in text
        assert "2 conflicts" in text
        assert "1 queued for review" in text
        assert "0 errors" in text
        assert "3 batches" in text

    def test_summary_text_when_unfinished(self) -> None:
        r = BackfillReceipt(created=1)
        text = r.summary_text()
        assert "n/a" in text  # finished_at is None


# =============================================================================
# Group 5: identity-string constants
# =============================================================================


class TestIdentityConstants:
    """Slot B identity strings follow CREATED_BY_PREFIXES / DECIDED_BY_PREFIXES."""

    def test_matcher_created_by_uses_matcher_prefix(self) -> None:
        assert CREATED_BY_MATCHER.startswith("matcher:")

    def test_backfill_created_by_uses_cli_prefix(self) -> None:
        assert CREATED_BY_BACKFILL.startswith("cli:")

    def test_matcher_decided_by_uses_service_prefix(self) -> None:
        assert DECIDED_BY_MATCHER.startswith("service:")

    def test_backfill_decided_by_uses_service_prefix(self) -> None:
        assert DECIDED_BY_BACKFILL.startswith("service:")

    def test_all_identities_within_64_char_boundary(self) -> None:
        """Slot 0073 #1085 finding #3 inheritance: every identity must fit VARCHAR(64)."""
        for identity in [
            CREATED_BY_MATCHER,
            CREATED_BY_BACKFILL,
            DECIDED_BY_MATCHER,
            DECIDED_BY_BACKFILL,
        ]:
            assert len(identity) <= 64, (
                f"identity {identity!r} exceeds VARCHAR(64) boundary (len={len(identity)})"
            )


# =============================================================================
# Group 6: CanonicalEventMatcher class metadata
# =============================================================================


class TestMatcherClassMetadata:
    """SERVICE_KEY / HEALTH_COMPONENT / BREAKER_TYPE class vars."""

    def test_service_key_is_canonical_event_matcher(self) -> None:
        assert CanonicalEventMatcher.SERVICE_KEY == "canonical_event_matcher"

    def test_health_component_matches_service_key(self) -> None:
        assert CanonicalEventMatcher.HEALTH_COMPONENT == CanonicalEventMatcher.SERVICE_KEY

    def test_breaker_type_is_data_stale(self) -> None:
        """Parallel to canonical_observations_writer + temporal_alignment_writer."""
        assert CanonicalEventMatcher.BREAKER_TYPE == "data_stale"

    def test_default_poll_interval_30s(self) -> None:
        """Per parent spec § File 7 + addendum -- 30s baseline."""
        assert CanonicalEventMatcher.DEFAULT_POLL_INTERVAL == 30

    def test_min_poll_interval_5s(self) -> None:
        assert CanonicalEventMatcher.MIN_POLL_INTERVAL == 5

    def test_instance_accepts_custom_config(self) -> None:
        """Construction accepts poll_interval / batch_size / t_match / breaker overrides."""
        m = CanonicalEventMatcher(
            poll_interval=60,
            batch_size=50,
            t_match=Decimal("0.75"),
            circuit_breaker_threshold=10,
        )
        assert m.poll_interval == 60
        assert m.batch_size == 50
        assert m.t_match == Decimal("0.75")
        assert m.circuit_breaker_threshold == 10

    def test_invalid_poll_interval_below_min_raises(self) -> None:
        with pytest.raises(ValueError, match="poll_interval must be at least"):
            CanonicalEventMatcher(poll_interval=2)  # below MIN_POLL_INTERVAL=5

    def test_job_name(self) -> None:
        m = CanonicalEventMatcher()
        assert m._get_job_name() == "Canonical Event Matcher"
