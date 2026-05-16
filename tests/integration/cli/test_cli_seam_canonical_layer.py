"""Integration tests for the CLI seam closing the canonical-layer
CLI-orphan gap (session 109).

Two modules — ``canonical_observations_writer`` (slot 0078) and
``canonical_event_matcher`` (Slot B) — shipped with their factories
registered in ``SERVICE_FACTORIES`` but no CLI surface to add them to
``enabled_services``.  Session 109 closes that gap by:

    1. Extracting a Pattern 73 SSOT constant ``CANONICAL_LAYER_SERVICES``
       in ``schedulers.service_supervisor``.
    2. Expanding ``RunnerConfig.__post_init__`` to register each
       canonical-layer service with ``ServiceConfig.enabled`` matching
       the ``features.<service>.enabled`` YAML value.
    3. Adding ``--canonical-event-matcher`` and
       ``--canonical-observations-writer`` flags to
       ``cli/scheduler.py``'s ``start`` command.

These tests verify each of those wires end-to-end.

References:
    - memory/feedback_cli_orphan_pattern_canonical_layer.md
    - docs/operations/canonical_event_matcher_runbook.md § 4
    - docs/operations/canonical_observations_runbook.md § 3
    - CLAUDE.md § 8 (Pattern 73 SSOT)

Parallel Execution Note:
    Per ``feedback_clirunner_not_thread_safe.md``, each test
    instantiates its own CliRunner rather than sharing a fixture-level
    one, so pytest-xdist parallel workers don't race on shared stdout
    state.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner


@pytest.fixture
def isolated_app():
    """Create a completely isolated Typer app for integration testing.

    Mirrors the fixture in ``test_cli_scheduler_integration.py``.  A
    fresh app prevents pollution between parallel test workers when the
    shared global app registers commands at import time.
    """
    from precog.cli import db, scheduler, system

    fresh_app = typer.Typer(name="precog", help="Precog CLI (test instance)")
    fresh_app.add_typer(db.app, name="db")
    fresh_app.add_typer(scheduler.app, name="scheduler")
    fresh_app.add_typer(system.app, name="system")
    return fresh_app


def _make_supervised_mock_supervisor() -> MagicMock:
    """Build a supervisor mock that behaves correctly in non-foreground mode."""
    mock_supervisor = MagicMock()
    mock_supervisor.is_running = True
    mock_supervisor.start_all.return_value = None
    mock_supervisor.stop_all.return_value = None
    mock_supervisor.get_aggregate_metrics.return_value = {
        "uptime_seconds": 0,
        "services_healthy": 1,
        "services_total": 1,
        "total_restarts": 0,
        "total_errors": 0,
        "per_service": {},
    }
    return mock_supervisor


@pytest.fixture(autouse=True)
def _mock_migration_check():
    """Bypass migration parity check in all CLI seam tests."""
    from precog.database.migration_check import MigrationStatus

    ok = MigrationStatus(is_current=True, db_version="0091", head_version="0091")
    with patch("precog.database.migration_check.check_migration_parity", return_value=ok):
        yield


class TestCliFlagsAddToEnabledServices:
    """The CLI flags must add the canonical-layer service names to the
    ``enabled_services`` set passed into ``create_supervisor``."""

    def test_scheduler_start_canonical_event_matcher_flag_adds_to_enabled_services(
        self, isolated_app
    ) -> None:
        """``--canonical-event-matcher`` adds ``canonical_event_matcher``
        to the ``enabled_services`` set passed to ``create_supervisor``."""
        runner = CliRunner()

        with (
            patch(
                "precog.schedulers.service_supervisor.create_supervisor"
            ) as mock_create_supervisor,
            patch("precog.cli.scheduler._validate_startup", return_value=True),
            patch("precog.cli.scheduler._prevent_system_sleep_for_supervised"),
        ):
            mock_create_supervisor.return_value = _make_supervised_mock_supervisor()

            result = runner.invoke(
                isolated_app,
                [
                    "scheduler",
                    "start",
                    "--supervised",
                    "--no-espn",
                    "--no-kalshi",
                    "--canonical-event-matcher",
                ],
            )

            assert result.exit_code == 0, (
                f"start should exit 0; got {result.exit_code}: {result.output}"
            )
            mock_create_supervisor.assert_called_once()
            call_kwargs = mock_create_supervisor.call_args.kwargs
            enabled = call_kwargs.get("enabled_services")
            assert enabled is not None
            assert "canonical_event_matcher" in enabled, (
                f"expected canonical_event_matcher in enabled_services; got {enabled}"
            )

    def test_scheduler_start_canonical_observations_writer_flag_adds_to_enabled_services(
        self, isolated_app
    ) -> None:
        """``--canonical-observations-writer`` adds
        ``canonical_observations_writer`` to the ``enabled_services``
        set passed to ``create_supervisor``."""
        runner = CliRunner()

        with (
            patch(
                "precog.schedulers.service_supervisor.create_supervisor"
            ) as mock_create_supervisor,
            patch("precog.cli.scheduler._validate_startup", return_value=True),
            patch("precog.cli.scheduler._prevent_system_sleep_for_supervised"),
        ):
            mock_create_supervisor.return_value = _make_supervised_mock_supervisor()

            result = runner.invoke(
                isolated_app,
                [
                    "scheduler",
                    "start",
                    "--supervised",
                    "--no-espn",
                    "--no-kalshi",
                    "--canonical-observations-writer",
                ],
            )

            assert result.exit_code == 0, (
                f"start should exit 0; got {result.exit_code}: {result.output}"
            )
            mock_create_supervisor.assert_called_once()
            call_kwargs = mock_create_supervisor.call_args.kwargs
            enabled = call_kwargs.get("enabled_services")
            assert enabled is not None
            assert "canonical_observations_writer" in enabled, (
                f"expected canonical_observations_writer in enabled_services; got {enabled}"
            )

    def test_scheduler_start_both_canonical_flags_combine(self, isolated_app) -> None:
        """Both flags together add both service names plus optional
        ESPN/Kalshi defaults — verifies combinatorial composition."""
        runner = CliRunner()

        with (
            patch(
                "precog.schedulers.service_supervisor.create_supervisor"
            ) as mock_create_supervisor,
            patch("precog.cli.scheduler._validate_startup", return_value=True),
            patch("precog.cli.scheduler._prevent_system_sleep_for_supervised"),
        ):
            mock_create_supervisor.return_value = _make_supervised_mock_supervisor()

            result = runner.invoke(
                isolated_app,
                [
                    "scheduler",
                    "start",
                    "--supervised",
                    "--no-espn",
                    "--no-kalshi",
                    "--canonical-event-matcher",
                    "--canonical-observations-writer",
                ],
            )

            assert result.exit_code == 0, (
                f"start should exit 0; got {result.exit_code}: {result.output}"
            )
            mock_create_supervisor.assert_called_once()
            call_kwargs = mock_create_supervisor.call_args.kwargs
            enabled = call_kwargs.get("enabled_services")
            assert enabled is not None
            assert "canonical_event_matcher" in enabled
            assert "canonical_observations_writer" in enabled

    def test_scheduler_start_no_canonical_flags_omits_services(self, isolated_app) -> None:
        """Without the CLI flags, the canonical-layer services must
        stay out of ``enabled_services`` even though their factories
        are registered.  This is the seam's negative case."""
        runner = CliRunner()

        with (
            patch(
                "precog.schedulers.service_supervisor.create_supervisor"
            ) as mock_create_supervisor,
            patch("precog.cli.scheduler._validate_startup", return_value=True),
            patch("precog.cli.scheduler._prevent_system_sleep_for_supervised"),
        ):
            mock_create_supervisor.return_value = _make_supervised_mock_supervisor()

            result = runner.invoke(
                isolated_app,
                ["scheduler", "start", "--supervised"],
            )

            assert result.exit_code == 0, (
                f"start should exit 0; got {result.exit_code}: {result.output}"
            )
            mock_create_supervisor.assert_called_once()
            call_kwargs = mock_create_supervisor.call_args.kwargs
            enabled = call_kwargs.get("enabled_services")
            assert enabled is not None
            assert "canonical_event_matcher" not in enabled
            assert "canonical_observations_writer" not in enabled


class TestRunnerConfigDefaultsCanonicalServices:
    """``RunnerConfig.__post_init__`` must register the canonical-layer
    services with ``ServiceConfig.enabled`` reflecting the YAML flag."""

    def test_runner_config_defaults_canonical_services_to_yaml_enabled_state(self) -> None:
        """When ``features.<service>.enabled=True`` in system.yaml,
        the RunnerConfig default service must have ``enabled=True``."""
        from precog.schedulers import service_supervisor as ss

        # Both canonical services enabled in YAML.
        def yaml_reader(service_name: str) -> bool:
            return service_name in {
                "canonical_observations_writer",
                "canonical_event_matcher",
            }

        with patch.object(ss, "_read_canonical_service_yaml_enabled", side_effect=yaml_reader):
            config = ss.RunnerConfig()

        assert "canonical_observations_writer" in config.services
        assert "canonical_event_matcher" in config.services
        assert config.services["canonical_observations_writer"].enabled is True
        assert config.services["canonical_event_matcher"].enabled is True
        # And the legacy services remain present + unchanged.
        assert "espn" in config.services
        assert "kalshi_rest" in config.services

    def test_runner_config_canonical_services_disabled_by_default_when_yaml_flag_false(
        self,
    ) -> None:
        """When ``features.<service>.enabled=False`` in system.yaml,
        the RunnerConfig default service must have ``enabled=False`` —
        the fail-closed default that protects production."""
        from precog.schedulers import service_supervisor as ss

        with patch.object(ss, "_read_canonical_service_yaml_enabled", return_value=False):
            config = ss.RunnerConfig()

        assert config.services["canonical_observations_writer"].enabled is False
        assert config.services["canonical_event_matcher"].enabled is False

    def test_runner_config_canonical_services_use_human_readable_names(self) -> None:
        """Each canonical-layer service ServiceConfig must carry the
        human-readable name (operator-facing surface)."""
        from precog.schedulers import service_supervisor as ss

        with patch.object(ss, "_read_canonical_service_yaml_enabled", return_value=False):
            config = ss.RunnerConfig()

        assert config.services["canonical_observations_writer"].name == (
            "Canonical Observations Writer"
        )
        assert config.services["canonical_event_matcher"].name == "Canonical Event Matcher"


class TestPattern73SsotConstantParity:
    """The ``CANONICAL_LAYER_SERVICES`` constant is the Pattern 73 SSOT
    artifact spanning four surfaces.  This test verifies the constant
    aligns with the ``SERVICE_FACTORIES`` registry — the single shape-
    drift check that catches "added factory but forgot constant" or
    vice versa.
    """

    def test_canonical_layer_services_constant_matches_service_factories_keys(self) -> None:
        """Every entry in ``CANONICAL_LAYER_SERVICES`` MUST be a key in
        ``SERVICE_FACTORIES`` (the registry the supervisor reads to
        instantiate the service).  If this test fails, the constant has
        drifted from the registry and the CLI flag will route to a
        service that the factory cannot construct.
        """
        from precog.schedulers.service_supervisor import (
            CANONICAL_LAYER_SERVICES,
            SERVICE_FACTORIES,
        )

        missing = [name for name in CANONICAL_LAYER_SERVICES if name not in SERVICE_FACTORIES]
        assert missing == [], (
            f"CANONICAL_LAYER_SERVICES entries missing from SERVICE_FACTORIES: "
            f"{missing}.  Pattern 73 SSOT drift — either add the factory or "
            f"remove the constant entry."
        )

    def test_canonical_layer_services_constant_is_non_empty_tuple(self) -> None:
        """Defensive check: the constant should be a tuple (immutable)
        with at least one entry.  Catches accidental refactors that
        change the type or empty the collection."""
        from precog.schedulers.service_supervisor import CANONICAL_LAYER_SERVICES

        assert isinstance(CANONICAL_LAYER_SERVICES, tuple)
        assert len(CANONICAL_LAYER_SERVICES) >= 1
