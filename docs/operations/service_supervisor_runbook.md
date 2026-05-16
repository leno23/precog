# ServiceSupervisor Operator Runbook

**Component:** `ServiceSupervisor` (umbrella process; not registered in `system_health` as itself)
**Module:** `src/precog/schedulers/service_supervisor.py`
**Cohort:** Phase 2.5 (ADR-100 Service Supervisor Pattern)
**Status:** Active code path — runs whenever `scheduler start --supervised` is invoked.

---

## 1. Service overview

ServiceSupervisor is the umbrella process that owns the lifecycle of every long-running data-collection service in production: ESPN poller, Kalshi REST poller, Kalshi WebSocket handler, temporal alignment writer, canonical observations writer, canonical event matcher. **Every other operator runbook in this directory describes a service that runs *under* the supervisor.** The supervisor itself is not a polling service — it is the manager.

**What the supervisor provides:**

- **Service lifecycle management** — start each enabled service, monitor health, restart on failure with exponential backoff, stop cleanly on shutdown signal.
- **Health checks** — periodic per-service health validation against the `system_health` table; status transitions (`healthy` → `degraded` → `down`) drive alerts and circuit-breaker behavior.
- **Circuit breaker** — repeated failures trip a per-service circuit breaker that stops restart attempts and surfaces an alert. Prevents runaway restart loops on persistently broken services.
- **Aggregate metrics** — uptime, total restarts, total errors, per-service health snapshot.
- **Cross-process IPC** — writes per-service status to `scheduler_status` table so a separate `precog scheduler status` invocation can see the live state.
- **Alert callback registry** — services and CLI register callbacks; alerts fire on health transitions and circuit-breaker trips.

**Architectural pattern:** Erlang/OTP "let it crash" philosophy. Services are allowed to fail; the supervisor catches the failure and decides whether to restart, escalate to circuit breaker, or shut down.

---

## 2. Architectural role

```
+---------------------------------------------------+
|  precog scheduler start --supervised --foreground |
|                       |                           |
|                       v                           |
|              ServiceSupervisor                    |
|                                                   |
|   +-----------+ +-----------+ +-------------+    |
|   | ESPN      | | Kalshi    | | Matcher     |    |
|   | Poller    | | REST      | | (Cohort 5+) |    |
|   +-----------+ +-----------+ +-------------+    |
|        |              |              |           |
|        v              v              v           |
|   +---------------------------------------+      |
|   |       system_health table              |     |
|   |       (per-service heartbeats)         |     |
|   +---------------------------------------+      |
+---------------------------------------------------+
```

The supervisor is the SINGLE entry point for production data collection. Direct invocation of individual pollers (`KalshiMarketPoller()`, etc.) bypasses health monitoring, circuit breaker, restart logic, and cross-process status visibility — appropriate only for one-shot backfill or debugging.

---

## 3. Health-check signals — `system_health`

Each managed service writes a row to `system_health` keyed by `component`. The supervisor reads these rows on every health-check tick (default 30s interval, `--health-interval`) and applies the `_determine_health()` rule.

```sql
SELECT component, status, details, last_check, alert_sent
FROM system_health
ORDER BY last_check DESC;
```

**Status rule** (per `_determine_health()`):

- `healthy`: service is running, error rate <5%, polls fresh (within 2x poll interval).
- `degraded`: error rate 5-25% OR last poll within (2x, 5x) interval.
- `down`: error rate >25% OR no poll for >5x interval.

**Component names registered today:**

- `espn_game_poller`
- `kalshi_market_poller`
- `kalshi_websocket` (prototype state — see `kalshi_websocket_runbook.md`)
- `temporal_alignment_writer`
- `canonical_observations_writer` (skeleton — see `canonical_observations_runbook.md`)
- `canonical_event_matcher` (functional, feature-flag gated — see `canonical_event_matcher_runbook.md`)

The supervisor itself does NOT write a `system_health` row for "the supervisor process." Liveness is inferred from the heartbeat freshness of the services it manages: if all services have stale heartbeats simultaneously, the supervisor itself is likely down.

---

## 4. Restart policy + exponential backoff

When a service raises an unhandled exception, the supervisor catches it and:

1. Increments the service's `restart_count`.
2. If `restart_count <= ServiceConfig.max_retries` (default 3, override via `--max-restarts`): sleeps `retry_delay * 2^attempt` seconds, then restarts the service. Default base delay = 5s, so attempts at 5s, 10s, 20s, 40s.
3. If `restart_count > max_retries`: trips the circuit breaker (see §5). No further restart attempts for that service in the supervisor's lifetime.

**Per-service tuning:** `ServiceConfig.max_retries` and `retry_delay` are per-service, set at construction time in `RunnerConfig.__post_init__` or factory functions in `SERVICE_FACTORIES`. The supervisor does not currently expose CLI flags for per-service tuning — `--max-restarts` is global.

**Restart count does NOT reset on successful poll cycles.** A service that restarts twice, runs cleanly for 6 hours, then crashes a third time will trip the breaker. This is intentional defensive behavior; service stability is the goal, not occasional crash tolerance.

---

## 5. Circuit breaker mechanism

The supervisor writes a row to `circuit_breaker_events` when it stops attempting to restart a service. The breaker is identified by `breaker_type`, which is per-service:

```sql
SELECT * FROM circuit_breaker_events
ORDER BY triggered_at DESC LIMIT 10;
```

**Breaker types in use:**

| Service | breaker_type |
|---|---|
| `espn_game_poller` | `api_failures` |
| `kalshi_market_poller` | `api_failures` |
| `kalshi_websocket` | `api_failures` |
| `temporal_alignment_writer` | `data_stale` |
| `canonical_observations_writer` | `data_stale` |
| `canonical_event_matcher` | `data_stale` |

**Note on `data_stale` for canonical-tier services:** this label is operationally inverted between game-weeks and off-weeks — during NFL off-week the matcher correctly has no work, but a `data_stale` trip in that window would mislead the operator. The matcher acknowledges this in module docstring lines 1281-1290; tracked for migration to `breaker_type='heartbeat'` in **#1198**.

**Auto-trip behavior** (`_auto_trip_circuit_breaker`): the supervisor trips the breaker automatically when a service reaches `status='down'` AND has exceeded `max_retries`. This is distinct from manual trips (`breaker_type='manual'`), which an operator can fire via a database row insert if needed.

**Recovery:** circuit breakers are NOT auto-reset within a supervisor process. The supervisor must be stopped + restarted for the breaker to clear. This is by design — if the breaker tripped because of a persistent code-side bug, restarting the same process without a code fix will trip it again. Stop, fix, redeploy, restart.

---

## 6. Shutdown discipline

**Signal handlers:** `SIGINT` (Ctrl+C) and `SIGTERM` invoke `_stop_supervised_mode()`, which calls `supervisor.stop_all()` on every registered service. Each service's `stop(wait=True)` blocks until the service drains in-flight work and exits its event loop.

**Timing invariant:** CLAUDE.md Critical Pattern #9 (Scheduler Shutdown Discipline) mandates `<5s stop` to dissolve the stale-row problem. **The supervisor's current default `timeout=20.0` on `BasePoller.stop` exceeds this invariant** — a 100-candidate batch with per-candidate ~50ms could observe full batch completion (~5s) before unwinding. Tracked in **#1206 Item 5** for a downward adjustment.

In practice, services use the `with get_cursor(commit=True)` context manager + SAVEPOINT discipline, so partial-batch interrupts roll back atomically. There is no half-written state risk — the timing concern is wall-clock to clean exit, not data integrity.

**Force-stop:** `kill -KILL` (or Task Manager → End Process) bypasses signal handlers entirely. Services do NOT clean up `scheduler_status` rows; the rows become stale and `precog scheduler status` will show "stale heartbeats" until the table is manually cleaned or the next supervisor invocation upserts fresh rows. The `cleanup_stale_schedulers` CRUD path (called at supervisor startup) prunes rows older than the staleness threshold.

---

## 7. Cross-process IPC — `scheduler_status` table

A separate `precog scheduler status` invocation can see the supervisor's per-service state via the `scheduler_status` table.

```sql
SELECT service_name, host_id, pid, status, started_at, last_heartbeat, stats
FROM scheduler_status
ORDER BY last_heartbeat DESC;
```

**Heartbeat cadence:** every service writes a heartbeat row on every successful poll cycle. The supervisor itself does not write a "supervisor liveness" row — supervisor liveness is implicit in service heartbeat freshness.

**Staleness threshold:** rows with `last_heartbeat` > 120s old are marked stale by `precog scheduler status`. This is a UX-side filter; the rows remain in the table.

**Educational note:** the database-table approach solves the IPC problem cleanly. `scheduler start` (Process A) writes status to DB via heartbeat; `scheduler status` (Process B) reads status from DB. This works across processes, unlike in-memory global variables. Reference: Migration 0012 (scheduler_status table) + Issue #255.

---

## 8. Troubleshooting

### 8.1 Service marked `down` but supervisor still running

```sql
SELECT * FROM system_health WHERE status = 'down';
SELECT * FROM circuit_breaker_events ORDER BY triggered_at DESC LIMIT 5;
```

If the service has a recent `circuit_breaker_events` row: the supervisor tripped the breaker after exceeding `max_retries`. Check supervisor logs (`logs/precog_*.log`) for the original exception trace. Common causes: DB connection pool exhausted, migration parity gap (DB behind alembic head), credential / config missing for the service environment.

If no breaker row exists: the service may be transient-failing without exhausting retries. Watch the heartbeat freshness — if `last_check` advances, the service is restarting between checks. Increase `--health-interval` to widen the observation window.

### 8.2 `precog scheduler status` shows stale heartbeats

```sql
SELECT service_name, last_heartbeat,
       EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) AS seconds_stale
FROM scheduler_status
WHERE last_heartbeat < NOW() - INTERVAL '2 minutes';
```

Indicates the supervisor process crashed without clean shutdown, OR the service stopped writing heartbeats while the supervisor's outer loop continued. The supervisor's startup `cleanup_stale_schedulers` call will prune these rows on the next `scheduler start`.

### 8.3 Supervisor itself crashed

There is no "supervisor heartbeat" — liveness is inferred from service heartbeats. If ALL services show simultaneous staleness, the supervisor is likely dead.

```sql
SELECT service_name, status, last_heartbeat
FROM scheduler_status
ORDER BY last_heartbeat DESC;
```

Recovery: `precog scheduler start --supervised --foreground` (or whichever wrapper-script invocation matches your deployment). The startup path's `_validate_startup()` will fail fast on environment-config issues; if validation passes, services restart cleanly and stale heartbeat rows get upserted with fresh values.

### 8.4 Circuit breaker tripped, want to clear and restart

The breaker is NOT auto-reset within a supervisor process. To clear:

1. `precog scheduler stop` (graceful) or process kill (forced)
2. Investigate the root-cause that tripped the breaker (logs + `circuit_breaker_events.details`)
3. Apply fix (config, code, credentials, migration upgrade — whatever the root cause requires)
4. `precog scheduler start --supervised --foreground` — fresh process, no inherited breakers

**Do not** manually delete `circuit_breaker_events` rows to "reset" the breaker. The supervisor reads `breaker_type` from this table at startup via `get_active_breakers()` to know which services to skip. Deleting rows without a code-fix means the next restart will re-trip the breaker on the same exception.

### 8.5 Service not appearing in supervised mode

Two-axis enable model — both axes must be set:

- **CLI flag:** `--<service-name>` must be in the `scheduler start` invocation (e.g., `--canonical-event-matcher`).
- **YAML flag:** `features.<service>.enabled: true` in `src/precog/config/system.yaml` for canonical-layer services (gates `ServiceConfig.enabled` in `RunnerConfig.__post_init__`).

If only one axis is set:

- CLI flag set, YAML false: service is added to `enabled_services` but `ServiceConfig.enabled=False` causes `create_services()` to skip instantiation. Silent no-op.
- YAML true, CLI flag absent: service is not added to `enabled_services`; supervisor doesn't see it. Silent no-op.

ESPN and Kalshi REST default to enabled in both axes (no YAML feature flag needed). Canonical-layer services use the explicit two-axis gate.

See `feedback_cli_orphan_pattern_canonical_layer.md` for the failure mode this gate prevents.

---

## 9. Investigation queries

### 9.1 Aggregate uptime + restart history

```sql
SELECT
  service_name,
  COUNT(*) AS heartbeat_count,
  MIN(started_at) AS first_seen,
  MAX(last_heartbeat) AS most_recent,
  EXTRACT(EPOCH FROM (MAX(last_heartbeat) - MIN(started_at))) AS observed_seconds
FROM scheduler_status
GROUP BY service_name
ORDER BY observed_seconds DESC;
```

### 9.2 Recent circuit-breaker trips with context

```sql
SELECT breaker_type, triggered_at, details
FROM circuit_breaker_events
WHERE triggered_at > NOW() - INTERVAL '7 days'
ORDER BY triggered_at DESC;
```

### 9.3 Per-service error rate

```sql
SELECT component, status, last_check,
       (details->>'error_count')::int AS errors,
       (details->>'poll_count')::int AS polls,
       CASE WHEN (details->>'poll_count')::int > 0
            THEN ROUND(100.0 * (details->>'error_count')::int / (details->>'poll_count')::int, 2)
            ELSE NULL END AS error_pct
FROM system_health
ORDER BY error_pct DESC NULLS LAST;
```

### 9.4 Services that have never reported

```sql
SELECT s.service_name
FROM (VALUES
  ('espn_game_poller'), ('kalshi_market_poller'), ('kalshi_websocket'),
  ('temporal_alignment_writer'), ('canonical_observations_writer'),
  ('canonical_event_matcher')
) AS s(service_name)
LEFT JOIN scheduler_status ss ON ss.service_name = s.service_name
WHERE ss.service_name IS NULL;
```

Rows here = services in `SERVICE_FACTORIES` that have never heartbeat'd. Most often means the service has never been enabled (either YAML or CLI axis missing) on this host.

---

## 10. Operational scenarios

### 10.1 Standard production startup

```powershell
python main.py scheduler start --supervised --foreground --verbose --kalshi-env prod
```

(Or `precog scheduler start ...` if PATH is configured; otherwise wrapper script at `scripts/start_scheduler_prod.bat` once added — see #1207 sibling task.)

Default services on this invocation: ESPN + Kalshi REST. To include canonical-layer services: append `--canonical-event-matcher` and/or `--canonical-observations-writer` (and ensure the corresponding YAML flag is true).

### 10.2 Matcher-only run (incident response or backfill validation)

```powershell
python main.py scheduler start --supervised --foreground --no-espn --no-kalshi --canonical-event-matcher
```

Only the canonical event matcher runs. Useful when validating matcher behavior in isolation without ESPN/Kalshi polling noise.

### 10.3 Graceful shutdown

`Ctrl+C` in the foreground terminal. The supervisor catches `SIGINT`, calls `stop_all()`, prints final aggregate metrics ("Supervisor stopped after Ns (N restarts, N errors)"), and exits.

### 10.4 Cross-process status check while supervisor is running

In a second terminal:

```powershell
python main.py scheduler status --verbose
```

Shows per-service heartbeat freshness, started-at timestamps, error counts. Reads from `scheduler_status` table (cross-process IPC).

---

## 11. Forward pointers

- **Service-default-set YAML config** — proposed session 110 implementation. Adds `scheduler.default_enabled_services` to `system.yaml` so `scheduler start --supervised` with no per-service flags reads the default set. Reduces the 8+ flag invocation to ~4 flags.
- **`<5s shutdown invariant fix`** — **#1206 Item 5**. Lowers `BasePoller.stop` default timeout from 20s to ≤5s per CLAUDE.md Pattern 9.
- **`breaker_type='heartbeat'` migration** — **#1198**. New breaker type to replace the operationally-inverted `data_stale` label for canonical-tier services.
- **Supervisor self-heartbeat row** — not currently planned, but worth considering: a `system_health` row for `component='service_supervisor'` would give operators a single liveness query rather than the "all services stale = supervisor down" inference. File a follow-up if soak surfaces operator confusion.

---

## 12. References

- **Module:** `src/precog/schedulers/service_supervisor.py`
- **ADR:** ADR-100 (Service Supervisor Pattern)
- **Tests:** `tests/integration/scheduler/test_service_supervisor_*.py`
- **Database tables:** `system_health`, `scheduler_status`, `circuit_breaker_events`
- **CLI:** `src/precog/cli/scheduler.py` (`scheduler start`, `scheduler stop`, `scheduler status`)
- **Related runbooks:** `kalshi_websocket_runbook.md`, `canonical_event_matcher_runbook.md`, `canonical_observations_runbook.md`
- **Project pattern:** CLAUDE.md Critical Pattern #9 (Scheduler Shutdown Discipline, <5s invariant)
- **CLI-orphan prevention:** `memory/feedback_cli_orphan_pattern_canonical_layer.md`
