@echo off
REM Production scheduler wrapper — invokes the supervised + foreground + verbose
REM scheduler against prod Kalshi env.  Reads the default-enabled service set
REM from src/precog/config/system.yaml (scheduler.default_enabled_services).
REM
REM Usage:
REM   scripts\start_scheduler_prod.bat
REM   scripts\start_scheduler_prod.bat --canonical-event-matcher
REM   scripts\start_scheduler_prod.bat --no-kalshi --canonical-event-matcher
REM
REM See docs/operations/service_supervisor_runbook.md for the activation
REM procedure, two-axis enable model, and per-service override semantics.
REM
REM Any flags passed to this script are forwarded to the underlying
REM `python main.py scheduler start` invocation via %*.

python main.py scheduler start --supervised --foreground --verbose --kalshi-env prod %*
