#!/usr/bin/env bash
# Production scheduler wrapper — invokes the supervised + foreground + verbose
# scheduler against prod Kalshi env. Reads the default-enabled service set
# from src/precog/config/system.yaml (scheduler.default_enabled_services).
#
# Usage:
#   ./scripts/start_scheduler_prod.sh
#   ./scripts/start_scheduler_prod.sh --canonical-event-matcher
#   ./scripts/start_scheduler_prod.sh --no-kalshi --canonical-event-matcher
#
# See docs/operations/service_supervisor_runbook.md for the activation
# procedure, two-axis enable model, and per-service override semantics.
#
# Any flags passed to this script are forwarded to the underlying
# `python main.py scheduler start` invocation via "$@".

set -euo pipefail
python main.py scheduler start --supervised --foreground --verbose --kalshi-env prod "$@"
