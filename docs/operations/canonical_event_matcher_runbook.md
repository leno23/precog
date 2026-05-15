# Canonical Event Matcher Operator Runbook

**Component:** `canonical_event_matcher`
**Database tables touched:** `canonical_events`, `canonical_event_links`, `canonical_event_match_log` (Migration 0091), `canonical_event_phase_log`, `canonical_event_participants`, `canonical_entities`, `games`, `game_states`
**Cohort:** 5+ Slot B (ADR-118 V2.44 atomicity contract)
**Status:** Active code path — feature flag `features.canonical_event_matcher.enabled` defaults to `false` until session 108+ soak window opens.

---

## 1. Service overview

The canonical event matcher is the canonical-tier identity engine for sports events. It bridges the platform-tier (`games`, `platform_events`) and the canonical-tier (`canonical_events`, `canonical_event_links`) by creating canonical_events rows whose natural-key hash deterministically identifies a real-world sports event across platforms.

**What Slot B ships:** the matcher module + Migration 0091 (audit ledger + provenance column + algorithm seed) + new CRUD modules (`crud_canonical_event_match_log.py`, plus `create_link` helper on existing `crud_canonical_event_links.py`) + CLI command group (`precog matcher backfill`, `precog matcher status`) + ServiceSupervisor registration + system.yaml feature flag + this runbook.

**What is NOT yet wired:**

- **Phase advancement.** The matcher creates rows with `lifecycle_phase='proposed'` ONLY. Advancement to `listed → pre_event → live → completed` is a separate downstream slot.
- **Reconciler integration.** The reconciler (parent spec § Q6: separate slot, deferred to Slot F) will read matcher-aware stale state and produce reconciliation_outcomes. Not in Slot B scope.
- **Polymarket / non-Kalshi platforms.** Migration 0090 prepared the schema for multi-platform but Slot B's matcher hot-path only sees Kalshi `platform_events` (the only platform currently producing platform_events rows).

**What reads from matcher output:**

- Future Cohort 5+ lifecycle / reconciler slots.
- Cohort 6+ analytics (model training / backtesting datasets that join via canonical_events).
- Operator runbook queries (this document).

---

## 2. V2.44 atomicity contract

Per ADR-118 V2.44, every matcher decision is bundled into a single transaction:

```sql
BEGIN;
  -- Step 1: SELECT canonical_events WHERE natural_key_hash = nk (idempotency check)
  -- Step 2: lazy-resolve canonical_entities for home + away team_codes
  -- Step 3: INSERT canonical_events (RETURNING id)
  -- Step 4: INSERT canonical_event_participants (home + away)
  -- Step 5: INSERT canonical_event_links (calls create_link_in_cursor)
  -- Step 6: INSERT canonical_event_match_log (calls append_event_match_log_row_in_cursor)
  -- Step 7: AUTO-TRIGGER: canonical_event_phase_log row from canonical_events INSERT
  -- Step 8: UPDATE games SET canonical_event_id = <new>
  -- Step 9: UPDATE game_states SET canonical_event_id = <new> WHERE game_id = ...
COMMIT;
```

For each candidate, either all 9 steps commit or all 9 roll back via the per-candidate SAVEPOINT (V2.44 atomicity preserved within-candidate). Post-PR-C (#1195, session 108), per-candidate exceptions trigger `ROLLBACK TO SAVEPOINT` for that candidate only — sibling candidates in the same batch commit normally. The next poll cycle re-tries failed candidates from a clean state. Idempotency is guaranteed by `uq_canonical_events_nk` UNIQUE constraint on `natural_key_hash` — re-runs of the same candidate yield an `existing` canonical_event_id rather than duplicate creation.

---

## 3. Health-check signals — `system_health` for `component='canonical_event_matcher'`

The matcher registers with ServiceSupervisor at startup; its heartbeat lands one row in `system_health` keyed by component name.

```sql
SELECT component, status, details, last_check, alert_sent
FROM system_health
WHERE component = 'canonical_event_matcher'
ORDER BY last_check DESC
LIMIT 1;
```

**Status values:**

- `healthy`: matcher is running, error rate < 5%, polls fresh.
- `degraded`: error rate 5-25% OR last poll > 2x interval.
- `down`: error rate > 25% OR no poll for > 5x interval.

**Slot B baseline** (feature flag enabled, normal operation):

- Expect 1 heartbeat per `poll_interval_sec` (default 30s).
- Expect `items_created` count to climb during initial activation as the matcher works through the pending backlog, then plateau near zero in steady state (new platform_events surface infrequently in production).

---

## 4. Feature-flag toggle — `features.canonical_event_matcher.enabled`

**Default:** `false` at slot-B deploy time.

**Important asymmetry (PR-C clarification):** there are TWO independent activation paths and they behave differently:

- **Backfill CLI (`python main.py matcher backfill --all`)** — does NOT check the feature flag. The CLI invokes the matcher logic directly via `backfill_all()`. Operators can run backfills regardless of flag state. This is intentional — backfill is a one-time bulk operation, not a long-running service.
- **Steady-state supervised poller (`python main.py scheduler start --supervised`)** — the matcher service is registered with `ServiceSupervisor` unconditionally (so health-check coverage stays uniform), BUT instantiation of the service is gated by the `enabled_services` set passed to `create_services()`. There is currently no CLI flag (e.g., `--canonical-event-matcher`) that adds the matcher to `enabled_services`; activation requires either a code-path update to the scheduler CLI to add a flag, OR direct invocation of `create_services(config, enabled_services={"canonical_event_matcher", ...})` from a wrapper script.

The `features.canonical_event_matcher.enabled` YAML key is documentation of operator intent (read by the runbook + this doc) — it is NOT currently consulted by `create_services()` or any `is_feature_enabled()` helper. A future slot may wire the YAML flag to gate `enabled_services` membership automatically; until then operators must drive enablement via the wrapper-script approach.

**Activation procedure** (session 108+ soak window, or any operator-driven enablement):

1. **Pre-flight checks** — run the matcher's test suites locally:
   ```powershell
   python -m pytest tests/integration/database/test_migration_0091_canonical_event_match_log.py -v
   python -m pytest tests/unit/database/test_crud_canonical_event_match_log_unit.py -v
   python -m pytest tests/unit/matching/test_canonical_event_matcher_unit.py -v
   python -m pytest tests/integration/matching/test_canonical_event_matcher_integration.py -v
   ```
   All MUST pass before flipping the flag.

2. **Run a backfill dry-run** to see what the matcher would do without committing:
   ```powershell
   python main.py matcher backfill --all --dry-run --batch-size 100
   ```
   Inspect the receipt: number of candidates, expected per-action counts, any errors. (Backfill ignores the feature flag — see asymmetry note above.)

3. **Run the live backfill** (off-peak recommended):
   ```powershell
   python main.py matcher backfill --all --batch-size 1000
   ```
   Estimated wall-clock: ~5-15 min for ~3,500-5,000 platform_events with game_id. The CLI prints the receipt at end.

4. **Flip the YAML flag** in the active environment's `system.yaml` (intent-marker; see asymmetry note above):
   ```yaml
   features:
     canonical_event_matcher:
       enabled: true
   ```
   This change is informational — operators downstream of this runbook will see the flag and know the matcher is intended to be running. The supervisor does NOT auto-pickup this change.

5. **Start the supervisor with the matcher in `enabled_services`** (wrapper-script approach until a CLI flag ships):
   ```powershell
   python main.py scheduler stop
   # The scheduler `start` command currently exposes flags only for
   # --espn / --kalshi.  To run the matcher under supervision, either
   # patch `cli/scheduler.py` to add a --canonical-event-matcher flag,
   # OR invoke create_services() directly from a wrapper script:
   #
   #   from precog.config.runner_config import RunnerConfig
   #   from precog.schedulers.service_supervisor import create_services
   #   config = RunnerConfig.from_yaml("...")
   #   services = create_services(
   #       config,
   #       enabled_services={"canonical_event_matcher"},
   #       ...
   #   )
   ```

6. **Verify the matcher is healthy** within 2 minutes:
   ```sql
   SELECT * FROM system_health WHERE component = 'canonical_event_matcher';
   ```
   Expected: one row with `status='healthy'` and `last_check` within the past 2 minutes.

7. **Verify steady-state operation:**
   ```powershell
   python main.py matcher status
   ```
   Expected: pending queue at or near zero (backfill drained it); recent_creates_24h ≥ backfill count.

---

## 5. Troubleshooting

### 5.1 Matcher paused / not heartbeating

```sql
SELECT * FROM system_health WHERE component = 'canonical_event_matcher';
SELECT * FROM scheduler_status WHERE service_name = 'canonical_event_matcher';
```

If `system_health` row is `down` or `last_check` is stale (> 5x interval): supervisor restart loop is failing. Check supervisor logs (`logs/precog_*.log`) for the matcher's `_poll_once` exception traceback. Common causes:

- DB connection pool exhausted (high concurrent load).
- Migration 0091 not applied (alembic head lower than 0091).
- `cohort5_event_matcher_v1` seed row missing from `match_algorithm` (Migration 0091 downgrade was run without re-upgrade).

### 5.2 Pending queue growing unbounded

```powershell
python main.py matcher status
```

If `unlinked_platform_events` is climbing despite the matcher running:

- Individual candidates may be failing repeatedly (SAVEPOINT isolates per-candidate failures from siblings post-PR-C; the batch as a whole continues but specific candidates never commit). Inspect `canonical_event_match_log` for missing `action='create'` rows in the expected time window AND check `receipt.errors` / `receipt.error_excerpts` in scheduler stats for per-candidate failure patterns.
- Investigate per-candidate exceptions:
  ```sql
  -- Recent matcher errors in supervisor log via stats
  SELECT host_id, stats FROM scheduler_status WHERE service_name = 'canonical_event_matcher' ORDER BY last_heartbeat DESC LIMIT 1;
  ```
- Check for `ExclusionViolation` (existing active link conflicts) — these surface as `conflict` outcomes in the matcher, NOT errors; query the recent log for context:
  ```sql
  SELECT id, platform_event_id, canonical_event_id, note, decided_at
  FROM canonical_event_match_log
  WHERE note LIKE '%conflict%'
  ORDER BY decided_at DESC LIMIT 20;
  ```

### 5.3 Specific platform_event not matching

```sql
-- Investigate a specific platform_event's match history
SELECT pe.id, pe.title, pe.game_id, g.sport, g.game_date, g.home_team_code, g.away_team_code
FROM platform_events pe
JOIN games g ON g.id = pe.game_id
WHERE pe.id = <id>;

-- Recent log rows for this platform_event
SELECT id, action, canonical_event_id, link_id, decided_by, decided_at, note
FROM canonical_event_match_log
WHERE platform_event_id = <id>
ORDER BY decided_at DESC;

-- Does an active link exist?
SELECT * FROM canonical_event_links
WHERE platform_event_id = <id>
  AND link_state = 'active';
```

If no link exists and no log row exists: matcher hasn't processed it yet. Check the matcher's pending queue depth + cycle through `precog matcher status`.

If `link_state='retired'` exists with no successor `link_state='active'`: a prior operator retired the link manually; the matcher won't auto-recreate (re-link flow is a separate operator-driven path).

### 5.4 Circuit-breaker trip suspected

Slot B's matcher logs at WARN-level when consecutive logical-failures accumulate (parent spec Q5 hybrid policy). The supervisor's existing circuit-breaker integration handles the pause; for now Slot B observes the condition without auto-pausing.

```sql
SELECT * FROM circuit_breaker_events
WHERE breaker_type = 'data_stale'
  AND triggered_at > NOW() - INTERVAL '1 hour'
ORDER BY triggered_at DESC;
```

---

## 6. Investigation queries

### 6.1 Recently created canonical_events

```sql
SELECT ce.id, ce.title, ce.lifecycle_phase, ce.created_by, ce.created_at,
       cel.id AS link_id, cel.platform_event_id, cel.confidence
FROM canonical_events ce
LEFT JOIN canonical_event_links cel ON cel.canonical_event_id = ce.id AND cel.link_state = 'active'
WHERE ce.created_at > NOW() - INTERVAL '24 hours'
ORDER BY ce.created_at DESC
LIMIT 50;
```

### 6.2 Recent matcher audit log entries

```sql
SELECT id, action, canonical_event_id, link_id, platform_event_id,
       confidence, decided_by, decided_at, note
FROM canonical_event_match_log
WHERE decided_at > NOW() - INTERVAL '1 hour'
ORDER BY decided_at DESC
LIMIT 100;
```

### 6.3 Matcher activity per algorithm

```sql
SELECT ma.name, ma.version, cmd.action, COUNT(*) AS n
FROM canonical_event_match_log cmd
JOIN match_algorithm ma ON ma.id = cmd.algorithm_id
WHERE cmd.decided_at > NOW() - INTERVAL '24 hours'
GROUP BY ma.name, ma.version, cmd.action
ORDER BY ma.name, cmd.action;
```

### 6.4 Cross-platform coverage check

```sql
-- How many canonical_events have ≥2 platform links? (cross-platform coverage)
SELECT
  canonical_event_id,
  COUNT(*) AS n_active_links,
  array_agg(DISTINCT pe.platform_id) AS platforms
FROM canonical_event_links cel
JOIN platform_events pe ON pe.id = cel.platform_event_id
WHERE cel.link_state = 'active'
GROUP BY canonical_event_id
HAVING COUNT(*) > 1
ORDER BY n_active_links DESC
LIMIT 50;
```

(Slot B activation will produce mostly 1-platform links because only Kalshi currently produces `platform_events`. Multi-platform coverage materializes when Polymarket onboarding ships.)

### 6.5 Pending queue depth + age

```sql
SELECT
  COUNT(*) AS pending_total,
  MIN(g.game_date) AS oldest_game_date,
  MAX(g.game_date) AS newest_game_date
FROM platform_events pe
JOIN games g ON g.id = pe.game_id
WHERE pe.game_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM canonical_event_links cel
      WHERE cel.platform_event_id = pe.id
        AND cel.link_state = 'active'
  );
```

---

## 7. Forward pointers (Cohort 5+ Slot C onward)

Per `memory/build_spec_slot_b_matcher_addendum_session_107.md` § Deferred:

- **Slot C (planned):** `get_active_canonical_event` 2-pass CTE single-query refactor (#1168). Slot B's hot-path does 2x DB round-trips for cycle detection through `superseded_by` chains; Slot C optimizes after matcher proves out in dev.
- **Slot D (planned):** lifecycle phase advancement (`proposed → listed → pre_event → live → completed`) writer. Reads from event start_time/end_time + platform_event status + game_state.
- **Slot E (planned):** ESPN→platform_events writer (currently ESPN populates `games` only; Polymarket / other future platforms will populate platform_events directly).
- **Slot F (planned):** reconciler — joins canonical_events against canonical_observations to surface mismatched / missing / drifted facts.

---

## 8. References

- **Build spec:** `memory/build_spec_slot_a_matcher_pm_memo.md` (binding session-92)
- **Addendum:** `memory/build_spec_slot_b_matcher_addendum_session_107.md` (rebase delta)
- **ADR:** ADR-118 V2.44 (atomicity contract) + V2.46 (canonical layer relationships) + V2.49 (OQ-H1 NORMALIZE)
- **Migration:** `src/precog/database/alembic/versions/0091_canonical_event_match_log_and_matcher_provenance.py`
- **Module:** `src/precog/matching/canonical_event_matcher.py`
- **CRUD:** `src/precog/database/crud_canonical_event_match_log.py`, `crud_canonical_event_links.py` (`create_link` helper)
- **CLI:** `src/precog/cli/matcher.py`
- **Issue:** [#1184](https://github.com/mutantamoeba/precog/issues/1184) Item 4 (Cohort 5+ Slot B matcher dispatch)
