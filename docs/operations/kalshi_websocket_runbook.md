# Kalshi WebSocket Operator Runbook

**Component:** `kalshi_websocket` (system_health row: `component='websocket'`)
**Service key:** `kalshi_ws`
**Module:** `src/precog/schedulers/kalshi_websocket.py`
**Cohort / Phase:** Phase 2 (deferred until Schema Hardening Arc completes)
**Status:** **Prototype** — "subscribe-and-log" implementation; functional for ticker + orderbook_delta channels but full hybrid architecture per Epic #1043 is not yet built.

---

## 1. Service overview

The Kalshi WebSocket handler maintains a persistent WebSocket connection to Kalshi for real-time market data streaming. **It is currently a prototype, not a production-ready hybrid architecture.** The module docstring describes the *intended* hybrid design (real-time WebSocket primary + REST fallback with reconciliation); the implementation today subscribes to two channels and logs their messages without the fallback, reconciliation, or full channel coverage that production-grade operation requires.

**What works today:**

- Persistent WebSocket connection to either demo (`wss://demo-api.kalshi.co/trade-api/ws/v2`) or prod (`wss://api.elections.kalshi.com/trade-api/ws/v2`) URL — switched via `environment="demo"|"prod"` constructor argument.
- RSA-PSS authentication (same auth path as REST client; see ADR-047).
- Subscribe to `ticker` channel (price updates).
- Subscribe to `orderbook_delta` channel (orderbook bid/ask changes).
- `_handle_ticker_update()` writes to `platform_markets` via SCD Type 2 versioning (`update_market_with_versioning`).
- `_handle_orderbook_delta()` processes orderbook deltas (incomplete — surface-only).
- Auto-reconnect with exponential backoff on disconnect.
- ServiceSupervisor registration via `kalshi_ws` service key.

**What is NOT yet wired (Phase 2 epics):**

- **`fill` channel handler** ([#1039](https://github.com/mutantamoeba/precog/issues/1039), priority-high) — module docstring line 43 documents the channel ("User's own fill notifications") but no `_handle_fill_notification()` exists. Phase 2 blocker for Epic #504 manual trade placement.
- **`trade` channel handler** — docstring line 42 lists the channel but it is not subscribed to and there is no handler.
- **Hybrid REST fallback** ([#1043](https://github.com/mutantamoeba/precog/issues/1043), epic) — on WebSocket disconnect, the design promises REST poller activation with bounded-latency failover + post-recovery gap reconciliation. Today the handler logs the disconnect and waits for reconnect; the REST poller runs on its own independent schedule, with no coordination.
- **Full orderbook depth processing** — `_handle_orderbook_delta()` exists but does not maintain a full local orderbook book state. Currently a surface-only delta receiver.
- **Cross-environment failover discipline** — the demo/prod URL switch works statically at construction time, but there is no automatic failover or environment-validation between the WebSocket connection and the surrounding `KALSHI_ENV` / `MARKET_MODE` axes.

**What reads from this component today:**

- `platform_markets` rows updated via SCD versioning from ticker updates (when supervised mode is running with `kalshi_ws` enabled).
- Operator queries against `system_health WHERE component='websocket'` for liveness.

**Phase 2 maturation path:** Epic [#1043](https://github.com/mutantamoeba/precog/issues/1043) tracks the full maturation. Blocked behind Schema Hardening Arc completion + service-layer scaffold ([#984](https://github.com/mutantamoeba/precog/issues/984), [#990](https://github.com/mutantamoeba/precog/issues/990)). Not in scope for sessions 110-115.

---

## 2. Architectural intent vs current reality

The module's intended hybrid architecture (per docstring lines 16-37):

```
+-----------------------------------------------+
|              MarketDataManager                |
| (orchestrates polling + WS, handles failover) |
+--------------+----------------+---------------+
               |                |
+--------------v-----+ +--------v-----------+
|  KalshiMarketPoll  | | KalshiWebSocketHdr |
|  (REST)            | | (real-time)        |
|  - Initial sync    | | - Sub-sec updates  |
|  - WS fallback     | | - Primary feed     |
|  - Validation      | | - Orderbook depth  |
+--------------------+ +--------------------+
```

**What exists today:** the two boxes on the bottom row exist as independent services. There is no `MarketDataManager` orchestrating them. The REST poller and WebSocket handler each write to `platform_markets` independently when both are enabled. No coordination, no failover, no reconciliation. SCD Type 2 versioning prevents data loss (both paths produce versioned history) but there is no consistency guarantee that the latest version reflects the most-recent observation across both feeds.

For Phase 1 (current — data collection MVP), this is acceptable: the REST poller is the authoritative path; the WebSocket prototype provides a secondary low-latency feed without consistency guarantees. Phase 2 (manual trade placement) needs the hybrid architecture because trade-confirmation latency matters; the fill channel + REST-fallback design are gating dependencies.

---

## 3. Health-check signals — `system_health`

```sql
SELECT component, status, details, last_check, alert_sent
FROM system_health
WHERE component = 'websocket'
ORDER BY last_check DESC LIMIT 1;
```

**Status rule** (inherited from ServiceSupervisor `_determine_health()`):

- `healthy`: WebSocket connected, error rate <5%, recent message receipt.
- `degraded`: error rate 5-25% OR no message for > 2x poll interval.
- `down`: error rate >25% OR no message for > 5x poll interval.

**Prototype-state caveat:** the "no message" condition fires legitimately when Kalshi has no active market activity (e.g., between sports seasons). Today this generates spurious `degraded`/`down` transitions during off-weeks. The matcher's `data_stale` semantics discussion in [#1198](https://github.com/mutantamoeba/precog/issues/1198) applies here too — a `heartbeat`-style breaker would be more accurate for low-activity periods.

---

## 4. Circuit breaker

`breaker_type = 'api_failures'`. Same breaker semantics as the Kalshi REST poller — repeated connection failures or auth errors trip the breaker. See `service_supervisor_runbook.md` § 5 for the full mechanism.

```sql
SELECT * FROM circuit_breaker_events
WHERE breaker_type = 'api_failures'
  AND triggered_at > NOW() - INTERVAL '24 hours'
ORDER BY triggered_at DESC;
```

**Common trip causes:**

- Expired or rotated Kalshi API credentials.
- RSA-PSS private key file missing or unreadable.
- Demo/prod URL mismatch with the configured `KALSHI_ENV`.
- Network partition between host and Kalshi infrastructure.

---

## 5. Starting + stopping the WebSocket handler

The WebSocket handler is registered in `SERVICE_FACTORIES` under key `kalshi_ws`. By default it is NOT in the supervisor's enabled services — operators must opt in.

**To enable WebSocket alongside REST polling:**

The current `scheduler start` CLI does not expose a dedicated `--kalshi-websocket` flag. The handler is started via `enabled_services = {"kalshi_ws", "kalshi_rest", "espn"}` programmatic invocation, OR by ad-hoc CLI patching. **This is the same CLI-orphan gap** that PR #1209 closed for canonical-layer services; the same fix shape would close it for `kalshi_ws`. Tracked indirectly via Epic [#1043](https://github.com/mutantamoeba/precog/issues/1043).

For the current prototype era, the typical operator path is:

```python
# Wrapper-script form (see scripts/start_scheduler_prod.bat once it lands)
from precog.schedulers.service_supervisor import create_supervisor
supervisor = create_supervisor(
    environment="development",
    kalshi_env="prod",
    enabled_services={"espn", "kalshi_rest", "kalshi_ws"},
    ...
)
supervisor.start_all()
```

**Shutdown:** `Ctrl+C` in foreground or `precog scheduler stop`. The WebSocket handler's `stop(wait=True, timeout=5.0)` drains in-flight messages and closes the connection within the 5s timeout — satisfies CLAUDE.md Pattern 9.

---

## 6. Troubleshooting

### 6.1 WebSocket connected but no messages

```sql
SELECT * FROM system_health WHERE component = 'websocket';
-- look at last_check freshness + details JSONB
```

If `last_check` is fresh but no `platform_markets` rows are being written: the handler is connected but messages aren't flowing through to the DB writer. Common cause: subscribed-tickers list is empty (the WebSocket subscribes only to tickers passed at startup). Check the construction arguments to `KalshiWebSocketHandler`.

### 6.2 Repeated disconnect / reconnect loops

```sql
SELECT * FROM circuit_breaker_events
WHERE breaker_type = 'api_failures'
ORDER BY triggered_at DESC LIMIT 5;
```

Auth failure is the most common cause — RSA-PSS signature rejected. Verify:

- `PROD_KALSHI_API_KEY` env var matches the active key in Kalshi's portal.
- `PROD_KALSHI_PRIVATE_KEY_PATH` points at a readable file.
- Private key has not been rotated since the supervisor process started (key rotation requires supervisor restart).

### 6.3 Missing fill notifications during manual trading

**Expected.** The fill channel handler is not implemented today (per [#1039](https://github.com/mutantamoeba/precog/issues/1039)). Phase 2 manual trade placement (#504) is blocked behind this work. Manual trades placed today via the REST `place_order` path will land in Kalshi but the application will not receive a real-time fill notification — fills must be polled via `kalshi_client.get_fills()` until #1039 ships.

### 6.4 Orderbook depth queries return surface-only data

**Expected.** `_handle_orderbook_delta()` processes deltas at the surface but does not maintain a full local orderbook book state. Use the REST `kalshi_client.get_orderbook()` endpoint for full-depth orderbook reads until [#1043](https://github.com/mutantamoeba/precog/issues/1043) wires the local book state.

---

## 7. Investigation queries

### 7.1 Recent ticker-channel write rate (via platform_markets versioning)

```sql
SELECT
  date_trunc('hour', valid_from) AS hour,
  COUNT(*) AS market_versions_created
FROM platform_markets
WHERE valid_from > NOW() - INTERVAL '24 hours'
  AND created_by LIKE '%websocket%'
GROUP BY hour
ORDER BY hour DESC;
```

Caveat: this query only works if WebSocket-written rows are distinguishable from REST-written rows via `created_by` — verify your env's writer conventions before relying on this.

### 7.2 WebSocket-specific health-row history

```sql
SELECT last_check, status, details
FROM system_health
WHERE component = 'websocket'
ORDER BY last_check DESC
LIMIT 100;
```

Look for state-transition patterns: frequent `healthy → degraded` flips suggest intermittent connection instability rather than persistent failure.

### 7.3 Auth-failure diagnostic

```sql
SELECT triggered_at, details
FROM circuit_breaker_events
WHERE breaker_type = 'api_failures'
  AND details::text LIKE '%signature%' OR details::text LIKE '%auth%'
ORDER BY triggered_at DESC LIMIT 10;
```

---

## 8. Phase 2 maturation roadmap (per Epic #1043)

Pre-conditions before Phase 2 maturation work can start:

1. **Schema Hardening Arc (Epic #745) completes** — currently in Cohort 5+. Slot B matcher shipped session 107; Slot C/D/E pending.
2. **Service-layer scaffold ([#984](https://github.com/mutantamoeba/precog/issues/984), [#990](https://github.com/mutantamoeba/precog/issues/990))** — the `MarketDataManager` orchestrator that owns the hybrid is part of this scaffold.

Maturation sequence once unblocked:

- **[#1039](https://github.com/mutantamoeba/precog/issues/1039)** — fill channel handler. Phase 2 blocker for manual trading.
- **[#1043](https://github.com/mutantamoeba/precog/issues/1043) sub-issues** — hybrid orchestration (MarketDataManager), REST fallback on disconnect, gap reconciliation on reconnect, full orderbook book state, cross-env failover discipline.
- **CLI seam closure** — `--kalshi-websocket` flag added to `scheduler start`, matching the Pattern 73 SSOT shape PR #1209 established for canonical-layer services.
- **Operator runbook revisions** — when each piece lands, this runbook's "What is NOT yet wired" section shrinks; the "Maturation roadmap" section shortens. This runbook is the maturation checklist.

---

## 9. Operational scenarios (current prototype)

### 9.1 WebSocket as low-latency secondary feed (no consistency guarantee)

```powershell
# Once --kalshi-websocket flag is added (currently requires wrapper script)
python main.py scheduler start --supervised --foreground --verbose
# (today: WebSocket runs only if added programmatically to enabled_services)
```

Both REST poller and WebSocket write to `platform_markets`. SCD versioning prevents row clobbering; latest-version row may come from either path depending on race timing.

### 9.2 REST-only (recommended for production until #1043 ships)

```powershell
python main.py scheduler start --supervised --foreground --verbose --kalshi-env prod
# Default services: ESPN + Kalshi REST. WebSocket not enabled.
```

This is the production-recommended invocation today. The WebSocket prototype is appropriate for dev / staging exploration, not production market-data primary.

### 9.3 WebSocket-only dev exploration

Useful when debugging WebSocket-specific behavior. Requires wrapper-script invocation (or the equivalent ad-hoc CLI patch).

---

## 10. References

- **Module:** `src/precog/schedulers/kalshi_websocket.py`
- **ADR:** ADR-047 (RSA-PSS Authentication Pattern)
- **Epic:** [#1043](https://github.com/mutantamoeba/precog/issues/1043) (WebSocket Maturation)
- **Phase-2 blocker:** [#1039](https://github.com/mutantamoeba/precog/issues/1039) (fill channel handler)
- **API guide:** `docs/api-integration/API_INTEGRATION_GUIDE_V2.0.md`
- **Sibling REST runbook:** *(not yet authored — P2 of #1207, slotted for session 111)*
- **Supervisor runbook:** `service_supervisor_runbook.md`
- **Phase-2 epic (manual trading):** [#504](https://github.com/mutantamoeba/precog/issues/504)
- **Service-layer scaffold:** [#984](https://github.com/mutantamoeba/precog/issues/984), [#990](https://github.com/mutantamoeba/precog/issues/990)

---

## 11. Honesty note

This runbook describes a **prototype**, not a production-grade implementation. The "What is NOT yet wired" section is load-bearing — operators reading this runbook before [#1043](https://github.com/mutantamoeba/precog/issues/1043) lands should treat the WebSocket as a low-latency *secondary* observation surface, not as the authoritative market-data feed. The REST poller remains the authoritative path until the hybrid architecture is built.

When [#1043](https://github.com/mutantamoeba/precog/issues/1043) ships, this runbook should be rewritten — its current shape (prototype-honest disclosure + maturation-roadmap-as-checklist) is appropriate for the prototype era and becomes wrong once the hybrid lands. The rewrite is itself a deliverable of [#1043](https://github.com/mutantamoeba/precog/issues/1043).
