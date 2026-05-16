"""Unit tests for the YAML default-services resolution helper in
``cli/scheduler.py``.

Tests the contract introduced session 110 to reduce the operator-facing
flag burden: ``scheduler.default_enabled_services`` in system.yaml is
the default-enabled service set; per-service CLI flags act as overrides
(None = follow YAML, True = add, False = remove).

References:
    - docs/operations/service_supervisor_runbook.md § 10
    - src/precog/config/system.yaml § scheduler.default_enabled_services
    - Session 110 user direction (reduce flag-count friction in operator
      workflows; lockstep with .bat / .sh wrapper scripts).
"""

from unittest.mock import MagicMock, patch

from precog.cli.scheduler import _resolve_enabled_services_from_yaml_and_flags


def _yaml_default(*services: str) -> MagicMock:
    """Build a ConfigLoader mock returning the given service list from
    ``scheduler.default_enabled_services``."""
    mock_config = MagicMock()
    mock_config.get.return_value = list(services)
    return mock_config


def test_yaml_default_loaded_with_no_overrides() -> None:
    """When all CLI flags are None, the resolved set matches YAML default exactly."""
    mock_config = _yaml_default("espn", "kalshi_rest")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert result == {"espn", "kalshi_rest"}


def test_cli_true_adds_service_not_in_yaml_default() -> None:
    """A True CLI flag adds the service even if it's absent from YAML default."""
    mock_config = _yaml_default("espn", "kalshi_rest")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=True,
            canonical_observations_writer=None,
        )
    assert "canonical_event_matcher" in result
    assert "espn" in result
    assert "kalshi_rest" in result


def test_cli_false_removes_service_from_yaml_default() -> None:
    """A False CLI flag removes the service from the resolved set."""
    mock_config = _yaml_default("espn", "kalshi_rest")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=False,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert "espn" not in result
    assert "kalshi_rest" in result


def test_yaml_default_with_canonical_service_included() -> None:
    """When YAML default includes a canonical service, it's in the set without CLI flag."""
    mock_config = _yaml_default("espn", "kalshi_rest", "canonical_event_matcher")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert result == {"espn", "kalshi_rest", "canonical_event_matcher"}


def test_cli_false_removes_canonical_service_in_yaml_default() -> None:
    """--no-canonical-event-matcher removes the service even if YAML default includes it."""
    mock_config = _yaml_default("espn", "kalshi_rest", "canonical_event_matcher")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=False,
            canonical_observations_writer=None,
        )
    assert "canonical_event_matcher" not in result
    assert "espn" in result
    assert "kalshi_rest" in result


def test_missing_yaml_falls_back_to_espn_kalshi_default() -> None:
    """ConfigLoader exception or missing key falls back to [espn, kalshi_rest]."""
    mock_config = MagicMock()
    mock_config.get.side_effect = RuntimeError("system.yaml missing")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert result == {"espn", "kalshi_rest"}


def test_malformed_yaml_value_falls_back_to_default() -> None:
    """If scheduler.default_enabled_services is not a list, fall back to default."""
    mock_config = MagicMock()
    # Misconfigured: scalar string instead of list
    mock_config.get.return_value = "not_a_list"
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    # Falls back to the safe default
    assert result == {"espn", "kalshi_rest"}


def test_cli_overrides_compose_independently() -> None:
    """Multiple CLI overrides apply independently to YAML default."""
    mock_config = _yaml_default("espn", "kalshi_rest")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=False,
            kalshi=None,
            canonical_event_matcher=True,
            canonical_observations_writer=True,
        )
    assert result == {"kalshi_rest", "canonical_event_matcher", "canonical_observations_writer"}


def test_empty_yaml_default_with_cli_additions() -> None:
    """Empty YAML default + CLI additions produces a set with only the CLI additions."""
    mock_config = _yaml_default()
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result = _resolve_enabled_services_from_yaml_and_flags(
            espn=True,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert result == {"espn"}


def test_cli_true_idempotent_when_already_in_yaml_default() -> None:
    """--espn (True) on a service already in YAML default is a no-op."""
    mock_config = _yaml_default("espn", "kalshi_rest")
    with patch("precog.config.config_loader.ConfigLoader", return_value=mock_config):
        result_with_flag = _resolve_enabled_services_from_yaml_and_flags(
            espn=True,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
        result_without_flag = _resolve_enabled_services_from_yaml_and_flags(
            espn=None,
            kalshi=None,
            canonical_event_matcher=None,
            canonical_observations_writer=None,
        )
    assert result_with_flag == result_without_flag


def test_real_system_yaml_loads_espn_and_kalshi_rest() -> None:
    """Integration smoke: real system.yaml at project root contains the expected
    default-enabled service list at scheduler.default_enabled_services."""
    # No patching — exercise the real ConfigLoader path.
    result = _resolve_enabled_services_from_yaml_and_flags(
        espn=None,
        kalshi=None,
        canonical_event_matcher=None,
        canonical_observations_writer=None,
    )
    # Project default (session 110): espn + kalshi_rest; canonical services
    # gated behind YAML feature flags + CLI opt-in until post-soak.
    assert "espn" in result, (
        "scheduler.default_enabled_services in system.yaml should include 'espn' "
        f"as the project default; got {result}"
    )
    assert "kalshi_rest" in result, (
        "scheduler.default_enabled_services in system.yaml should include 'kalshi_rest' "
        f"as the project default; got {result}"
    )
