# Database Schema Summary

<!-- FRESHNESS: alembic_head=0086, verified=2026-05-07, tables=62, migrations=82, last_changelog_migration=0086 -->
<!--
Changelog from FRESHNESS marker bump alembic_head 0085 -> 0086 (V2.4 amended-in-place, 2026-05-07):

Migration 0086 (cleanup epic Slot 2, session 96) -- canonical FK direction
flip + denorm collapse.  Realizes CL-1 + CL-2 from session 94 cleanup-epic
council synthesis; ADR-118 V2.47 amendment codifies the rule (lands at
Slot 5 / session 99).

Nine schema-mutation surfaces in single transaction (hard ordering
invariant: trigger before column):

  - ADD teams.canonical_entity_id BIGINT NULL (1,034 existing rows start
    NULL; populated by Cohort 5+ matcher slot ~session 101).
  - ADD CONSTRAINT FK NOT VALID teams_canonical_entity_id_fkey ->
    canonical_entities(id) ON DELETE SET NULL (Pattern 84 by-analogy 3rd
    use; precedent-consistency style not safety -- empty target table
    means VALIDATE is no-op).
  - VALIDATE CONSTRAINT teams_canonical_entity_id_fkey (no-op against
    empty target).
  - DROP TRIGGER trg_canonical_entity_team_backref ON canonical_entities.
  - DROP FUNCTION enforce_canonical_entity_team_backref() (sentinel
    cleanup -- DROP TRIGGER does not auto-DROP the underlying function).
  - DROP CONSTRAINT canonical_entity_ref_team_id_fkey ON canonical_entities.
  - DROP COLUMN canonical_entities.ref_team_id (partial index
    idx_canonical_entity_ref_team_id auto-drops with the column).
  - DROP COLUMN canonical_events.game_id (FK constraint
    canonical_events_game_id_fkey auto-drops with the column).
  - DROP COLUMN canonical_events.series_id (FK constraint
    canonical_events_series_id_fkey auto-drops with the column).

Pattern 87 reaffirmed: zero edits to migrations 0001-0085.  Migration 0068
remains canonical for the trigger DDL; slot 0086's downgrade() reproduces
the trigger function body verbatim from 0068 lines 273-290.

Pattern 91 V1.44 self-discipline: all build-time premises MCP-verified
before DDL composition (alembic_head=0085, teams=1,034, canonical_entities
+ canonical_events both at 0 rows, FK constraints + trigger + function +
columns confirmed present pre-migration).

Pattern 82 V2 retirement (canonical_entities-team variant): the
load-bearing polymorphic-invariant test
tests/database/test_canonical_entity_polymorphic_invariants.py is DELETED
at Slot 2 per Path A user adjudication.  ADR-118 V2.40 Item 4 + Pattern 82
V2 carry inline forward-pointer notes to V2.47 (Slot 5 / session 99) for
formal pin retirement + Pattern 82 V2 scope-narrowing (rule retains for
canonical_markets only post-Slot-2).  CRUD module crud_canonical_entity.py:
create_canonical_entity() loses the ref_team_id parameter; module file
name unchanged.

Migration count 81 -> 82; table count unchanged at 62 (no table changes;
column changes only).

Cleanup epic forward plan: Slot 3 (CL-7 retirement cascade; ~session 97)
-- canonical_events.superseded_by FK + SSOT helper.  Slot 4 (CL-3
invasiveness narrow; ~session 98).  Slot 5 (V2.47 ADR amendment + Pattern
82 V2 scope-narrowing + #1163 cosmetic-cleanup folding; ~session 99).

-->
<!--
Changelog from FRESHNESS marker bump alembic_head 0084 -> 0085 (V2.4 amended-in-place, 2026-05-05):

Migration 0085 (cleanup epic Slot 1, session 95) -- canonical naming
bundle.  Pure naming hygiene against 0-row tables.  Four ALTER renames +
1 FK constraint rename; no behavioural change.  Lands the operations
codified by ADR-118 V2.47 amendment text (which lands at Slot 5 / session
99 per the 5-slot cleanup epic plan).

  - canonical_entity TABLE -> canonical_entities (Surface 1; pluralizes the
    canonical-tier collection table for parity with canonical_events,
    canonical_event_participants, canonical_markets, canonical_entity_kinds,
    etc.).  PG auto-rewrites the inbound FK target from
    canonical_event_participants on RENAME TABLE.
  - canonical_events.domain_id -> event_domain_id (Surface 2; FK column
    naming rule -- column name now identifies the referenced table
    canonical_event_domains).  Index idx_canonical_events_domain_id name
    unchanged in this slot (index-name cleanup deferred to a future
    cosmetic-cleanup slot).
  - canonical_events.entities_sorted -> participants_sorted (Surface 3;
    participants vocabulary alignment with canonical_event_participants).
  - canonical_event_participants.entity_id -> canonical_entity_id +
    FK constraint canonical_event_participants_entity_id_fkey ->
    canonical_event_participants_canonical_entity_id_fkey (Surfaces 4a +
    4b; FK column naming rule application matching the renamed target
    table).

Migration count 80 -> 81; table count unchanged at 62 (renames only).
CRUD module crud_canonical_entity.py: signature unchanged but module
file name not renamed (PM Picard adjudication; file rename deferred to
future cosmetic-cleanup slot).  CRUD module crud_canonical_events
function create_canonical_event signature: kwargs domain_id ->
event_domain_id, entities_sorted -> participants_sorted.  CRUD module
crud_canonical_event_participants function create_canonical_event_participant
signature: kwarg entity_id -> canonical_entity_id.

Pattern 87 reaffirmed: zero edits to migrations 0001-0084.  Pattern 91
V1.44 self-discipline: all 12 build-time premises MCP-verified before
DDL composition.

Cleanup epic forward plan: Slot 2 (FK direction; CL-1 + CL-2; ~session
96) -- teams.canonical_entity_id FK + DROP canonical_entities.ref_team_id
+ DROP canonical_events.game_id + series_id.

-->
<!--
Changelog from prior FRESHNESS marker (V2.3: alembic_head=0077, verified=2026-04-29, tables=58, migrations=76):

Cohort 4 (sessions 86-90) — ADR-118 V2.43 + V2.45 amendments — Cohort 4
informally CLOSED in session 90 with V2.45 redesign (4 slots shipped:
0078 + 0079 + 0080 + 0082; slot 0083 deferred to Cohort 5+ via #1141;
V2.45 / Migration 0084 ratifies the redesign):

- Migration 0078 (Cohort 4 slot 0078, session 86 PR #1120): canonical_observations
  partitioned parent table -- THE canonical-tier observation parent.  Composite
  PK (id, ingested_at), RANGE-partitioned monthly on ingested_at; 4 monthly
  partitions pre-created (2026-05/06/07/08); 5 indexes; 1 dedup UNIQUE
  (source_id, payload_hash, ingested_at) per V2.43 Item 3 composite-FK
  invariant; 3 CHECKs (observation_kind 6-value vocab + clock-skew 5-min
  guard + bitemporal validity); BEFORE UPDATE trigger (uses generic
  set_updated_at()); 5-timestamp ML causal-correctness shape; 3 SSOT vocab
  tuples in constants.py; restricted-API CRUD (append_observation_row only,
  no UPDATE/DELETE/UPSERT); ServiceSupervisor canonical_observations_writer
  registration + feature flag (default false until session 87+ soak).
- Migration 0079 (Cohort 4 slot 0079, session 87 PR #1124): canonical_event_phase_log
  audit ledger + auto-population trigger.  Append-only via application
  discipline (mirrors slot 0073).  Auto-populates from canonical_events.lifecycle_phase
  changes via AFTER INSERT/UPDATE trigger.
- Migration 0080 (Cohort 4 slot 0080, session 88 PR #1130): combined
  game_states.canonical_event_id + games.canonical_event_id nullable BIGINT
  FKs to canonical_events ON DELETE SET NULL (Pattern 84 NOT VALID + VALIDATE
  by analogy on populated tables; first-ever FK by-analogy use).  Slot 0081
  retired into 0080 per V2.43 Item 2 + Q5 user adjudication.  Permanent slot
  0081 hole in alembic chain.
- Migration 0082 (Cohort 4 slot 0082, session 88 PR #1134): temporal_alignment.canonical_event_id
  nullable BIGINT FK to canonical_events ON DELETE SET NULL (Pattern 84 by-
  analogy 2nd use; N=2 threshold -> Pattern 84 promotion to V1.42 in PR #1138).
- Migration 0084 (Cohort 4 close-out slot 0084, session 90):
  CANONICAL LAYER REDESIGN per ADR-118 V2.45 Items 1/4/6/8.  Four user-
  adjudicated decisions:
    Item 1 -- Cohort 4 informal close + slot 0083 deferral to Cohort 5+ (#1141).
    Item 4 -- DROP COLUMN canonical_observations.payload.  Per-kind projection
              tables (game_states, market_snapshots, future weather_observations
              / poll_releases / econ_prints / news_events) carry typed source
              data + JSONB residue where needed; the payload column was
              redundant.  NEW Pattern 73 SSOT discipline rule: adding a new
              observation_kind value requires its projection table to exist
              BEFORE the vocabulary expansion.
    Item 6 -- temporal_alignment redesigned as PURE LINKAGE TABLE.  20-column
              denormalized fact-table shape (slot 0035-era; sport-specific
              home_score / away_score / period / clock / yes_ask_price etc.)
              REPLACED with ~9-column shape: observation_a_id + observation_b_id
              FK INTO canonical_observations + denormalized canonical_event_id
              hot-path + time_delta_seconds + alignment_quality + aligned_at +
              created_at + PK.  Per-domain typed views (v_temporal_alignment_sports,
              v_temporal_alignment_weather, etc.) deferred to Cohort 5+ (#1141).
              Operationally: temporal_alignment was 0 rows at slot 0084 ship
              time; DROP TABLE + CREATE TABLE is zero-data-loss.
    Item 8 -- RENAME canonical_observations.canonical_event_id ->
              canonical_primary_event_id (denormalized-as-primary-tag semantic
              made explicit) + ADD canonical_observation_event_links many-to-
              many table for multi-event tagging.  Link table has 6 columns,
              composite PK, 2 CHECKs (link_kind 4-value vocab + confidence
              0-1 range), 3 indexes including partial unique
              idx_link_one_primary_per_observation enforcing "at most one
              primary link per observation".  Slot 0083 PERMANENT HOLE in
              the alembic chain (down_revision=0082 on slot 0084; matches
              slot 0081 hole shape from V2.43 Item 2).
  Migration count 76 -> 80 (slots 0078 + 0079 + 0080 + 0082 + 0084; slot
  0081 + slot 0083 holes intentional).  Net new tables: canonical_observations
  + 4 partitions + canonical_event_phase_log + canonical_observation_event_links;
  temporal_alignment redesigned (counted as net-zero in table count).
  CRUD module crud_canonical_observations.append_observation_row() signature
  changed: parameter canonical_event_id -> canonical_primary_event_id;
  payload parameter retained for hash derivation but no longer stored.

Forward-pointer: V2.46 (session 91+, issue #1143) will add the new doc
docs/database/CANONICAL_LAYER_RELATIONSHIPS.md (4-layer relationship graph
+ traversal examples + verb-table catalog) per the V2.46 design-review pipeline.
-->

<!--
Prior changelog from V2.3 (alembic_head=0077, verified=2026-04-29, tables=58, migrations=76):

Cohort 3 close-out post-retrofits — ADR-118 V2.42 sub-amendments A + B
(session 83 design council adjudication into SPLIT 0076 + 0077; both
shipped same-arc per Holden silent-fail vs loud-fail asymmetry):

- Migration 0076 (slot 0076, session 83): generic ``set_updated_at()``
  PL/pgSQL function + 4-table BEFORE UPDATE trigger retrofit.  Closes the
  Migration 0067 orphan-trigger gap on canonical_events (column declared
  with DEFAULT now() since 0067:249, no BEFORE UPDATE trigger ever shipped
  to maintain it -- 10 migrations of silent staleness).  Pattern 73 SSOT:
  ONE chokepoint for the ``updated_at`` maintenance contract; future
  cohorts adding canonical-tier tables with ``updated_at`` columns just
  ``CREATE TRIGGER ... EXECUTE FUNCTION set_updated_at()`` -- zero new
  function DDL.  Drops 3 per-table maintenance functions
  (``update_canonical_markets_updated_at()`` etc.) post-rewire.  PR #1098.
- Migration 0077 (slot 0077, session 83): ``canonical_events.{game_id,
  series_id}`` FK polarity flip from default ``ON DELETE NO ACTION`` to
  ``ON DELETE SET NULL``.  Realizes ADR-118 V2.42 sub-amendment B's
  canonical-outlives-platform contract: ``DELETE FROM games`` /
  ``DELETE FROM series`` cascades to canonical_events SET NULL with the
  canonical row preserved (matcher can re-bind).  Constraint NAMES
  preserved across the DROP CONSTRAINT + ADD CONSTRAINT round-trip
  (Holden-FK -- PG default-generated names would break the pinned-by-
  name test assertion in test_migration_0067 lines 400-450).  Single-
  phase ALTER per Pattern 84 carve-out (canonical_events is empty;
  no NOT VALID + VALIDATE phase needed).  No audit-trail breadcrumb in
  scope (Uhura-3) -- forward-pointer to future-cohort
  ``canonical_event_log`` parallel.

76 migration files cover slots 0001-0077 (slot 0029 still intentionally
skipped); +2 migrations vs V2.2 baseline.  Chain head advances 0074 ->
0077 via the monotonic 0074 -> 0076 -> 0077 sequence.

SSOT Python vocabulary expansion (constants.py): unchanged at 8.  Neither
slot 0076 nor slot 0077 introduce new closed-enum vocabularies.

Section J updates: trigger function ``set_updated_at()`` enumerated;
canonical_events column-level FK semantics annotated (game_id / series_id
now carry the SET NULL caveat).
-->

<!--
Prior changelog from V2.1 (alembic_head=0070, verified=2026-04-26, tables=51):

Cohort 3 — Phase B.5 ADR-118 canonical matching infrastructure
(session 78 user adjudication of 5-slot LOCK; ADR-118 v2.41 amendment;
all 5 slots SHIPPED across sessions 79-82):

- Migration 0071 (Cohort 3 slot 1, session 79): match_algorithm — Pattern 81
  lookup table, 1 seed row (manual_v1, 1.0.0).  The precursor that justifies
  Pattern 81's existence (ADR-118 line ~17475).  No CHECK on name; new
  algorithms (keyword_jaccard_v1, ml_v3, etc.) extend via INSERT seeds in
  cohort-of-origin migrations, never ALTER TABLE.  10-edit slot displacement
  bump on prior migrations' references to "Migration 0071 = canonical_market
  _links" -> "Migration 0072".  ADR-118 V2.41 amendment ratified the 5-slot
  plan; PR #1063.
- Migration 0072 (Cohort 3 slot 2, session 80-81): canonical_market_links +
  canonical_event_links — two parallel-shape link tables bridging canonical-
  identity tier to platform tier.  Single load-bearing schema invariant of
  Cohort 3: partial EXCLUDE-USING-btree constraint (one active link per
  platform-row id; retired/quarantined rows coexist freely).  link_state
  IN ('active', 'retired', 'quarantined') closed enum with Pattern 73 SSOT
  pointer to constants.py:LINK_STATE_VALUES.  FIRST-EVER S82 design-stage
  P41 FIRE outcome (Miles + Uhura, 5 + 4-warn + 0-block joint verdict).
  PR #1084.
- Migration 0073 (Cohort 3 slot 3, session 81): canonical_match_log — append-
  only audit ledger for every match decision.  Append-only enforced by
  application discipline (restricted CRUD; no UPDATE/DELETE/UPSERT API);
  trigger-enforced version queued for slot 0090 after 30-day soak.  Pattern
  73 SSOT pointers: ACTION_VALUES (7-value action vocab), DECIDED_BY_
  PREFIXES (3-prefix actor taxonomy).  Holden re-engagement caught the
  v2.42-trap-repeated silent-NO-ACTION default on canonical_market_id;
  fix: explicit ON DELETE SET NULL.  Holden P3 deliberate spec-strengthening
  on prior_link_id (ADR DDL was FK-less; v2.42 sub-amendment B audit-survival
  semantics applied parallel-by-design).  PR #1090.
- Migration 0075 (Cohort 3 slot 4, session 81): observation_source — Pattern
  81 lookup-table sibling to match_algorithm.  3 seed rows (espn, kalshi,
  manual).  Open enums for source_key + source_kind (no CHECK constraints
  by Pattern 81 design); future sources extend via INSERT seeds in cohort-
  of-origin migrations.  Documentation-not-enforcement framing in
  PHASE_1_SOURCE_KEYS + SOURCE_KIND_VALUES constants (the lookup table itself
  is the canonical authoritative store).  PR #1093.
- Migration 0074 (Cohort 3 slot 5, session 82, **CLOSES Cohort 3**):
  canonical_match_overrides + canonical_match_reviews — operator-asserted
  policies + state machine.  Polarity-pairing CHECK is the load-bearing
  invariant: polarity='MUST_NOT_MATCH' <-> canonical_market_id IS NULL;
  polarity='MUST_MATCH' <-> canonical_market_id IS NOT NULL.  Three-valued-
  logic-safe form (IS NULL / IS NOT NULL).  Pattern 73 SSOT pointers:
  REVIEW_STATE_VALUES (4-state machine), POLARITY_VALUES (pre-positioned in
  slot 0072 per S82 council, finally USED in slot 0074).  manual_v1-on-human-
  decided-actions convention extended from slot 0073.  Convergent two-
  reviewer P1 verdict (Glokta + Ripley independently surfaced atomicity gap
  from different frames); single-cursor _append_match_log_row_in_cursor()
  helper closed it.  PR #1094.

Non-monotonic alembic chain (Cohort 3): the runtime authoritative ordering
is 0070 -> 0071 -> 0072 -> 0073 -> 0075 -> 0074.  Slot 0075 ships ahead of
slot 0074 because the two are independent (no FK dependencies between
observation_source and the matching ledger); slot 0075 closed a small backlog
item while slot 0074 received fresh-dispatch full-attention design-verification.
The head is 0074 (no migration depends on it); a reader scanning the versions
directory by lexical filename order will see 0075 numerically last but should
trust down_revision pointers, not filenames.  Documented loudly in slot 0075's
docstring + slot 0074's docstring + this comment.

74 migration files cover slots 0001-0075 (slot 0029 still intentionally skipped);
+5 migrations vs V2.1 baseline.

SSOT Python vocabulary expansion (constants.py): 1 -> 8.  Cohort-1 carry-
forward seeded the canonical home with CANONICAL_EVENT_LIFECYCLE_PHASES
(Migration 0070, V2.40); Cohort 3 added 7: LINK_STATE_VALUES (slot 0072),
ACTION_VALUES + DECIDED_BY_PREFIXES (slot 0073), REVIEW_STATE_VALUES +
POLARITY_VALUES (slot 0074), PHASE_1_SOURCE_KEYS + SOURCE_KIND_VALUES
(slot 0075).  Pattern 73 SSOT discipline: every CHECK constraint backed by
a vocabulary cites the constant by name in a SQL comment; CRUD modules
import + USE the constants in real-guard ValueError-raising validation
(NOT side-effect-only `# noqa: F401` — slot 0072's side-effect convention
was intentionally upgraded for slot 0073+ per #1085 finding #2 strengthening).
-->


---
**Version:** 2.3
**Last Updated:** 2026-04-29
**Status:** Current — Alembic head: migration 0077 (chain: 0070 -> 0071 -> 0072 -> 0073 -> 0075 -> 0074 -> 0076 -> 0077)
**Replaces:** V2.2 (retained in same directory for changelog history; V2.1 + V2.0 + V1.16 also retained per Pattern 86 freshness-marker discipline)

> **V2.3 Increment:** Closes ADR-118 V2.42 sub-amendments A + B with two close-out
> post-retrofits (Migrations 0076 + 0077, session 83).  Adds the canonical
> ``set_updated_at()`` trigger function to § J's trigger inventory, annotates
> the post-Migration-0077 SET NULL caveat on `canonical_events.game_id` /
> `canonical_events.series_id` column entries, refreshes the freshness marker
> to `alembic_head=0077`, and bumps the Migration count to 76 (74 -> 76).  The
> SSOT Python vocabulary count in `constants.py` is UNCHANGED at 8 — neither
> close-out retrofit introduces new closed-enum vocabularies.  Same structural
> shape as V2.2 — V2.3 is a faithful revision, not a re-architecture.

---

## Quick Reference

| Metric | Count |
|--------|-------|
| Core Tables | 58 |
| Views | 19 |
| Migrations | 76 files (slots 0001-0077, slot 0029 skipped); chain head 0077 (non-monotonic 0073 -> 0075 -> 0074, then monotonic 0074 -> 0076 -> 0077) |
| SCD Type 2 Tables | 11 |
| JSONB Columns | 54 |
| Decimal Precision | DECIMAL(10,4) for monetary/probability values; NUMERIC(4,3) for matching-layer confidence (slots 0072+0073); DECIMAL(10,2) for Elo; numeric (no scale) for temporal_alignment |
| Canonical Identity Tier (Cohorts 1A + 1B + 2) | 8 tables (Migrations 0067-0069) |
| Canonical Matching Layer (Cohort 3) | 7 tables (Migrations 0071-0075: 5 canonical_* + 2 Pattern 81 lookups) |
| Pattern 81 Lookup Instances | 8 (2 pre-codification: `sports` + `leagues` from Migration 0060; 4 from Cohorts 1A+1B; 2 from Cohort 3) |
| SSOT Python Vocabularies (`constants.py`) | 8 (1 from Cohort 1 carry-forward + 7 from Cohort 3) |

### Key Design Patterns

- **SCD Type 2:** `row_current_ind`, `row_start_ts`, `row_end_ts` on volatile data
  (prices, game states, positions, balances, edges, strategies, models) — 11 tables.
- **Immutable Versioning:** strategies + probability_models use `(name, version)` UNIQUE,
  never overwritten. Migration 0064 layered SCD-2 on top so version rows are also
  temporally addressable (Pattern 80 / V1.35 codifies the rule). `match_algorithm`
  (Migration 0071) carries the same `(name, version)` UNIQUE shape; `observation_source`
  (Migration 0075) uses single-column `UNIQUE (source_key)` because sources have no
  version concept.
- **Fact / Dimension Split:** markets (dimension) + market_snapshots (fact) since
  Migration 0021. Same shape applied to game_odds (Migration 0048).
- **DECIMAL(10,4):** ALL financial / probability values in main tables. Never float.
  (ADR-002.) Cohort 3 matching layer uses `NUMERIC(4,3)` for confidence to support
  3-decimal precision on [0,1]-bounded probability scores.
- **Platform FK Hierarchy:** `platforms.platform_id` is root FK for multi-platform support.
- **Three-Tier Identity Model (Pattern 79 / ADR-117):** Tier 1 surrogate PK,
  Tier 2 SCD-2 version-stable business keys (`*_key` columns), Tier 3 external IDs
  (NOT unique by design — Migration 0066 demoted `idx_games_espn_event` accordingly).
- **Canonical Identity Layer (ADR-118):** Cohorts 1+2 land the eight canonical_*
  tables that abstract events, entities, and markets across platforms. Cohort 3
  (SHIPPED session 82) adds the matching infrastructure on top: link tables,
  audit ledger, operator overrides + reviews, observation-source registry.
  Cohorts 4-9 (NOT YET STARTED) will land dim/fact split refinement, event-state
  machine, views/CRUD/resolver, seeders, business-key cleanup, and weather Phase 1
  per ADR-119.
- **Open-Canonical-Enum -> Lookup Table (Pattern 81 / V1.36):** Open enums likely to
  grow are encoded as lookup tables with FK, not CHECK constraints. Eight instances:
  `sports` + `leagues` (Migration 0060 — the pre-codification precedents that established
  the lookup-table-via-FK shape replacing inline CHECK; classified as Pattern 81 instances
  retroactively by V1.36 codification),
  `canonical_event_domains`, `canonical_event_types` (Cohort 1A, Migration 0067),
  `canonical_entity_kinds`, `canonical_participant_roles` (Cohort 1B, Migration 0068),
  `match_algorithm` (Migration 0071 — the canonical precursor that V1.36 cites as
  justifying the pattern's existence),
  `observation_source` (Migration 0075).
- **Closed Enum -> Inline CHECK + Pattern 73 SSOT (carve-out from Pattern 81):**
  When a value set IS closed (every value binds to code branches), DO NOT use a
  lookup table. Encode as inline CHECK + Pattern 73 SSOT pointer to a Python
  constant in `src/precog/database/constants.py`. Cohort 3 instances:
  `link_state` (slot 0072 — `LINK_STATE_VALUES`), `action` (slot 0073 —
  `ACTION_VALUES`), `review_state` + `polarity` (slot 0074 — `REVIEW_STATE_VALUES`
  + `POLARITY_VALUES`). Adding a new value requires lockstep update of constant
  AND DDL CHECK; CRUD modules use the constant in real-guard validation.
- **CONSTRAINT TRIGGER for Polymorphic Typed Back-Reference (Pattern 82 / V1.36):**
  When a typed back-ref column's nullability depends on a discriminator that lives in
  a lookup table, the integrity rule cannot be encoded as a CHECK constraint
  (PG CHECK cannot subquery). The canonical_entity_kinds + canonical_entities pair
  in Migration 0068 (table renamed by Migration 0085) is the canonical instance —
  see `enforce_canonical_entity_team_backref`.
- **Append-Only Migration Files (Pattern 87 / V1.40):** Once an Alembic migration
  is merged to main, its file contents are immutable. Corrections to shipped
  migrations route to ADR amendments or subsequent-migration docstrings, never to
  edits of the shipped file. Reaffirmed clean across Cohort 3 (zero edits to
  migrations 0001-0075 within their own slot PRs).
- **Living-Doc Freshness Markers (Pattern 86 / V1.39):** Canonical living docs carry
  an HTML-comment freshness marker declaring source-of-truth state at last
  verification. This document demonstrates the pattern; ADR-118 + MASTER_REQUIREMENTS
  do likewise.
- **Polarity-Pairing CHECK (slot 0074 — three-valued-logic safety):** When a CHECK
  constraint encodes a NULL/NOT-NULL pairing rule based on a discriminator column,
  use `IS NULL` / `IS NOT NULL` (not `= NULL` / `!= NULL`). In SQL `x = NULL`
  evaluates to NULL (not TRUE) and silently admits any row; `IS NULL` is the
  three-valued-logic-safe form. The slot-0074 polarity-pairing CHECK is the
  canonical instance.

---

## Table Groups

### A. Platform & Market Hierarchy

#### platforms (Dimension)

Static platform reference. Root of FK hierarchy. Seeded with `'kalshi'`.

| Column | Type | Notes |
|--------|------|-------|
| platform_id (PK) | VARCHAR(50) | |
| platform_type | VARCHAR(50) | |
| display_name | VARCHAR(100) | |
| base_url | VARCHAR(255) | |
| websocket_url | VARCHAR(255) | |
| auth_method | VARCHAR(20) | CHECK: rsa_pss, api_key, oauth2, metamask |
| status | VARCHAR(20) | CHECK: active, inactive, maintenance |
| fees_structure | JSONB | |

#### series (Dimension - SCD Type 2)

Grouping for related events / markets (e.g. "NFL Week 12"). SCD-2 since Migration 0057.

| Column | Type | Notes |
|--------|------|-------|
| series_id (PK / business key) | VARCHAR(100) | Natural key |
| id | SERIAL | Surrogate (Migration 0019) |
| platform_id (FK) | VARCHAR(50) | |
| category, subcategory | VARCHAR | |
| title | VARCHAR(200) | |
| tags | JSONB | Added Migration 0010 |
| row_current_ind | BOOLEAN | SCD-2 (added Migration 0057) |
| row_start_ts, row_end_ts | TIMESTAMPTZ | SCD-2 (added Migration 0057) |

#### events (Dimension)

Specific outcome events within a series.

| Column | Type | Notes |
|--------|------|-------|
| event_id (PK / business key) | VARCHAR(100) | Natural key |
| id | SERIAL | Surrogate (Migration 0020) |
| platform_id (FK) | VARCHAR(50) | |
| series_id (FK) | VARCHAR(100) | |
| game_id (FK) | INTEGER | Links to games dimension (Migration 0038) |
| status | VARCHAR(20) | |
| result | JSONB | |

#### markets (Dimension)

Market identity and metadata. Prices live in market_snapshots (the fact).

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | SERIAL | |
| platform_id (FK) | VARCHAR(50) | |
| event_internal_id (FK) | INTEGER | Added Migration 0021 |
| ticker | VARCHAR(100) | UNIQUE |
| title | VARCHAR(500) | |
| market_type | VARCHAR(20) | binary, categorical, scalar |
| status | VARCHAR(20) | open, closed, settled, halted |
| subcategory | VARCHAR(100) | Renamed from `league` (Migration 0037) |
| subtitle | VARCHAR(500) | Added Migration 0033 |
| open_time, close_time, expiration_time | TIMESTAMPTZ | Added Migration 0033 |
| settlement_value | DECIMAL(10,4) | |
| expiration_value | VARCHAR(100) | Added Migration 0046 |

UNIQUE(platform_id, external_id), UNIQUE(ticker).

#### market_snapshots (Fact - SCD Type 2)

Versioned price / depth snapshots. ~100 versions / market / day during live trading.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | SERIAL | |
| market_id (FK) | INTEGER | -> markets(id) |
| yes_ask_price | DECIMAL(10,4) | |
| no_ask_price | DECIMAL(10,4) | |
| yes_bid_price | DECIMAL(10,4) | |
| no_bid_price | DECIMAL(10,4) | |
| last_price, spread | DECIMAL(10,4) | |
| volume, open_interest | INTEGER | |
| volume_24h | INTEGER | Added Migration 0046 |
| previous_yes_bid, previous_yes_ask, previous_price | DECIMAL(10,4) | Added Migration 0046 |
| yes_bid_size, yes_ask_size | INTEGER | Added Migration 0046 |
| row_current_ind | BOOLEAN | SCD-2 |
| row_start_ts, row_end_ts | TIMESTAMPTZ | SCD-2 |

Partial unique index: 1 current row per `market_id`.

---

### B. Teams & Sports Data

#### teams (Dimension - SCD Type 2)

Core sports team reference. Supports 9 sports. SCD-2 since Migration 0057.
Cleanup epic Slot 2 (Migration 0086, session 96) flipped the FK direction
between `teams` and `canonical_entities`: `teams.canonical_entity_id`
(BIGINT NULL FK to `canonical_entities(id)` ON DELETE SET NULL) replaces
the pre-Slot-2 typed back-ref `canonical_entities.ref_team_id`. The
polymorphic enforcement trigger `trg_canonical_entity_team_backref` is
also dropped at Slot 2 (Pattern 82 V2 scope-narrowing in V2.47 — applies
to `canonical_markets` only post-cleanup-epic Slot 2). All 1,034 existing
rows start NULL on `canonical_entity_id`; the Cohort 5+ matcher slot
(~session 101) populates the column when canonical_entities rows are
first seeded.

| Column | Type | Notes |
|--------|------|-------|
| team_id (PK) | SERIAL | |
| team_code | VARCHAR(20) | |
| team_name, display_name | VARCHAR | |
| sport | VARCHAR(20) | nfl, ncaaf, nba, ncaab, nhl, wnba, mlb, soccer, ncaaw |
| league | VARCHAR(20) | |
| espn_team_id | VARCHAR(20) | |
| kalshi_team_code | VARCHAR(50) | Added Migration 0041 |
| classification | VARCHAR(20) | Added Migration 0042 (d1, fbs, fcs, etc.) |
| current_elo_rating | DECIMAL(10,2) | |
| canonical_entity_id | BIGINT | NULLABLE FK -> `canonical_entities(id)` ON DELETE SET NULL; **added Migration 0086** (cleanup epic Slot 2 / session 96 — FK direction flip from pre-Slot-2 `canonical_entities.ref_team_id`; all 1,034 rows start NULL; populated by Cohort 5+ matcher slot ~session 101) |
| row_current_ind | BOOLEAN | SCD-2 (added Migration 0057) |
| row_start_ts, row_end_ts | TIMESTAMPTZ | SCD-2 (added Migration 0057) |

#### venues / external_team_codes / games / game_states / game_odds

(Unchanged from V2.1 — see V2.1 § B for full column lists.)

---

### C. Orders, Trades & Execution

(Unchanged from V2.1 — orders / trades / positions / account_balance / account_ledger /
settlements / position_exits / exit_attempts. Full column lists in V2.1 § C.)

---

### D. Market Data & Trading Signals

(Unchanged from V2.1 — market_trades / orderbook_snapshots.  Full column
lists in V2.1 § D.)

> **temporal_alignment REDESIGNED in V2.4 (Migration 0084 / V2.45 Item 6).**
> The slot-0035-era denormalized fact-table shape (20 columns: market_id,
> market_snapshot_id, game_state_id, home_score, away_score, period, clock,
> game_status, yes_ask_price, no_ask_price, spread, volume, etc.) has been
> REPLACED with a pure-linkage shape (~9 columns).  See § K below for the
> canonical post-V2.45 description; the V2.1 § D entry is superseded.

---

### E. Analytics & Evaluation

(Unchanged from V2.1 — edges / evaluation_runs / backtesting_runs / predictions /
performance_metrics / elo_calculation_log / settlements. Full column lists in V2.1 § E.)

---

### F. Strategy & Model Definitions

(Unchanged from V2.1 — strategies / probability_models / strategy_types / model_classes.
Full column lists in V2.1 § F.)

---

### G. Canonical Identity Layer (Cohorts 1A + 1B + 2 — ADR-118)

The eight canonical_* tables in Migrations 0067-0069
(`canonical_event_domains`, `canonical_event_types`, `canonical_events`,
`canonical_entity_kinds`, `canonical_entities` (renamed from
`canonical_entity` by Migration 0085 / cleanup epic Slot 1),
`canonical_participant_roles`, `canonical_event_participants`,
`canonical_markets`) establish the "Level B" canonical identity layer per
ADR-118 V2.38 + V2.39 + V2.40 + V2.41 + V2.42.
Full column lists, CONSTRAINT TRIGGER details, and forward-pointer comments
are in V2.1 § G; this V2.3 revision adds the post-Migration-0076 + 0077
column-level updates noted below.

The tables Cohort 3 builds atop (and the close-out retrofits applied to them):

- `canonical_events` — referenced by `canonical_event_links.canonical_event_id`
  (slot 0072) with `ON DELETE RESTRICT`.
  - **Trigger update (Migration 0076 / V2.42 sub-amendment A):**
    `trg_canonical_events_updated_at` BEFORE UPDATE trigger NET-NEW INSTALLED;
    closes the orphan-trigger gap from Migration 0067 (column existed since
    0067:249, no trigger ever shipped to maintain it).  Trigger executes
    the generic `set_updated_at()` function (§ J).
  - **Column-level update (Migration 0077 / V2.42 sub-amendment B) — pre-Slot-2 state:**
    - `game_id` (FK -> `games.id`): nullable; **may be NULLed by upstream
      `DELETE FROM games`** per `ON DELETE SET NULL` (was `ON DELETE NO
      ACTION` pre-Migration-0077).  Realizes ADR-118 V2.42 sub-amendment B
      canonical-outlives-platform contract.  Code that branches on `game_id
      IS NOT NULL` MUST tolerate the column transitioning to NULL
      asynchronously.
    - `series_id` (FK -> `series.id`): nullable; **may be NULLed by upstream
      `DELETE FROM series`** per `ON DELETE SET NULL` (same shape as
      `game_id`, same semantic, same caveat).
    - **NO audit-trail breadcrumb** on the SET NULL transition (out of
      V2.42 sub-amendment B scope; forward-pointer to future-cohort
      `canonical_event_log` parallel).  Operators reading `updated_at`
      post-Migration-0076 see SOMETHING changed; reading `game_id IS NULL
      AND updated_at >= now() - INTERVAL 'N days'` is a noisy proxy for
      "FK was NULLed recently" — see Migration 0077 docstring for the
      canonical query template + LIMITATION note.
  - **Column-level update (Migration 0086 / cleanup epic Slot 2 — denorm collapse):**
    Both `canonical_events.game_id` and `canonical_events.series_id` are
    **DROPPED** by Migration 0086 (CL-2 denorm collapse from session 94
    cleanup-epic council synthesis).  Rationale: canonical_events is the
    platform-agnostic identity layer; carrying platform-side dim FKs on the
    canonical row was a denormalization shortcut from Cohort 1A.  Post-
    Slot-2 the platform→canonical direction is the only direction (games
    references canonical_events via `games.canonical_event_id` per slot
    0080; canonical_events does not reference games / series at all).  The
    associated FK constraints `canonical_events_game_id_fkey` +
    `canonical_events_series_id_fkey` auto-drop with their columns. The
    Migration 0077 SET NULL polarity work above is preserved historically
    (Pattern 87 immutable) but no longer applies post-Slot-2.
- `canonical_markets` — referenced by `canonical_market_links.canonical_market_id`
  (slot 0072) with `ON DELETE RESTRICT`; by `canonical_match_log.canonical_market_id`
  (slot 0073) with `ON DELETE SET NULL` (Holden P1 catch — explicit closure of the
  v2.42-trap-repeated silent-NO-ACTION default); by `canonical_match_overrides.
  canonical_market_id` (slot 0074) with `ON DELETE RESTRICT`.
  - **Trigger update (Migration 0076):** `trg_canonical_markets_updated_at`
    REWIRED from per-table function `update_canonical_markets_updated_at()`
    to the generic `set_updated_at()` (the per-table function is DROPped
    by 0076).  Trigger NAME unchanged — only the function reference in the
    CREATE TRIGGER body differs.

Other canonical-tier tables also receive trigger rewires in Migration 0076
to use the generic `set_updated_at()` function: `canonical_market_links`
(slot 0072 trigger, rewired) and `canonical_event_links` (slot 0072 trigger,
rewired).  See § J for the trigger-function inventory.

---

### H. System & Operations

(Unchanged from V2.1 — system_health / scheduler_status / circuit_breaker_events /
alerts / config_overrides.)

---

### I. Lookup & Reference Tables

#### sports / leagues / strategy_types / model_classes / historical_*

(Unchanged from V2.1 — sport-level + league-level Pattern 81 lookups from
Migration 0060; legacy strategy_types + model_classes; historical sports-data
reference tables. Full description in V2.1 § I.)

---

### J. Canonical Matching Layer (Cohort 3 — ADR-118 V2.41 + V2.42)

The seven Cohort-3 tables shipped in Migrations 0071-0075 establish the
matching infrastructure that Cohort 5+ resolver code will use to bind
canonical-tier rows to platform-tier rows.  This tier sits atop the canonical
identity layer (§ G) and uses the platform-tier dimensions (`markets` / `events`
in § A) as link targets.  Cohort 3 closed in session 82 (PR #1094); Cohort 3
close-out post-retrofits shipped in session 83 (PR #1098 / Migration 0076 +
slot 0077 / Migration 0077).  Cohorts 4-9 will build the dim/fact split,
event-state machine, views/CRUD/resolver, seeders, business-key cleanup, and
weather Phase 1 atop this foundation.

#### Canonical-tier trigger function inventory (Migrations 0076 + 0077)

Migration 0076 (V2.42 sub-amendment A) installed the generic
`set_updated_at()` PL/pgSQL function as the Pattern 73 SSOT chokepoint for
`updated_at` maintenance across the canonical tier.  Four BEFORE UPDATE
triggers EXECUTE this function:

| Trigger name | Table | Source | Notes |
|---|---|---|---|
| `trg_canonical_events_updated_at` | `canonical_events` | Migration 0076 (NET-NEW; closes 0067 orphan-trigger gap) | Net-new install — closes the gap from Migration 0067 where the column was declared with `DEFAULT now()` but no BEFORE UPDATE trigger ever shipped. |
| `trg_canonical_markets_updated_at` | `canonical_markets` | Migration 0069 (rewired in 0076) | Rewired from per-table function `update_canonical_markets_updated_at()` to generic `set_updated_at()`.  Trigger NAME unchanged. |
| `trg_canonical_market_links_updated_at` | `canonical_market_links` | Migration 0072 (rewired in 0076) | Rewired from per-table function `update_canonical_market_links_updated_at()` to generic `set_updated_at()`.  Trigger NAME unchanged. |
| `trg_canonical_event_links_updated_at` | `canonical_event_links` | Migration 0072 (rewired in 0076) | Rewired from per-table function `update_canonical_event_links_updated_at()` to generic `set_updated_at()`.  Trigger NAME unchanged. |

Function semantics (from `COMMENT ON FUNCTION set_updated_at()`): updates
`updated_at` to `now()` on every BEFORE UPDATE; reflects any DB-side
modification including FK-NULL cascades from upstream DELETEs (per ADR-118
V2.42 sub-amendment B / Migration 0077).  NOT a "last canonical content
change" timestamp — operators reading `updated_at` must know it advances on
unrelated UPDATEs too.

Operator runbook (per Migration 0076 docstring M-6):
- `\df set_updated_at` — display the canonical maintenance function.  Should
  return EXACTLY ONE row.
- `SELECT trigger_name, event_object_table FROM information_schema.triggers
  WHERE action_statement LIKE '%set_updated_at%'` — enumerate the trigger
  instances.  Should return EXACTLY FOUR rows post-Migration-0076.

The 3 per-table maintenance functions
(`update_canonical_markets_updated_at()`,
`update_canonical_market_links_updated_at()`,
`update_canonical_event_links_updated_at()`) were DROPped by Migration 0076
after the trigger rewires.  They no longer exist in `pg_proc`
post-Migration-0076.

Out-of-scope tables (Migration 0076 docstring "Migration 0056 RAISE
EXCEPTION carve-out"): the 7 immutable tables (`strategies`,
`probability_models`, `trades`, `settlements`, `account_ledger`,
`position_exits`, `exit_attempts`) carry BEFORE UPDATE RAISE EXCEPTION
write-protection triggers from Migration 0056 — these are NOT
`updated_at`-maintenance triggers and MUST NOT be retrofitted under
`set_updated_at()`.

---

#### match_algorithm (Lookup, Pattern 81 instance) — Migration 0071

Open-enum lookup table for matching algorithms.  The precursor that justifies
Pattern 81's existence (ADR-118 line ~17475 cites it).  1 Phase 1 seed row:
`(manual_v1, 1.0.0, precog.matching.manual_v1, ...)`.  No CHECK on `name`;
new algorithms (e.g. `keyword_jaccard_v1`, `ml_v3`) ship via INSERT seeds in
their cohort-of-origin migrations.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| name | VARCHAR(64) | NOT NULL. Seed value `'manual_v1'`. |
| version | VARCHAR(16) | NOT NULL. Semver-shaped. Seed value `'1.0.0'`. |
| code_ref | VARCHAR(255) | NOT NULL. Pattern 73 SSOT pointer to the matcher implementation; reserved for Cohort 5+ resolver work (Migration 0085 territory). Phase 1 seed = `'precog.matching.manual_v1'` (the path does not yet exist in the repo). |
| description | TEXT | NULLABLE. Operator commentary. |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |
| retired_at | TIMESTAMPTZ | NULLABLE. NULL = active. |

`CONSTRAINT uq_match_algorithm UNIQUE (name, version)`. No `updated_at` column —
algorithms are immutable per Critical Pattern #6 (re-tune by inserting a new
row with a new version).

#### canonical_market_links (Link Table) — Migration 0072

Bridges canonical_markets to platform-tier `markets`.  The single load-bearing
schema invariant of Cohort 3 lives here as a partial EXCLUDE constraint.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| canonical_market_id (FK) | BIGINT | NOT NULL. -> canonical_markets(id). ON DELETE RESTRICT (settlement-bearing markets must not silently lose link audit trail). |
| platform_market_id (FK) | INTEGER | NOT NULL. -> markets(id). ON DELETE CASCADE (platform deletion = link is meaningless). |
| link_state | VARCHAR(16) | NOT NULL. CHECK IN ('active', 'retired', 'quarantined'). Pattern 73 SSOT pointer: `constants.py:LINK_STATE_VALUES`. |
| confidence | NUMERIC(4,3) | NOT NULL. CHECK 0 <= confidence <= 1. Phase 1 manual rows = 1.0. |
| algorithm_id (FK) | BIGINT | NOT NULL. -> match_algorithm(id). |
| decided_by | VARCHAR(64) | NOT NULL. Free-text actor. Pattern 73 SSOT pointer: `constants.py:DECIDED_BY_PREFIXES`. |
| decided_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |
| retired_at | TIMESTAMPTZ | NULLABLE. NULL = active. |
| retire_reason | VARCHAR(64) | NULLABLE. Operator-readable reason. PM-adjudicated Open item A from S82 council. |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). Maintained by per-table BEFORE UPDATE trigger `trg_canonical_market_links_updated_at`. |

> **Load-bearing invariant — `uq_canonical_market_links_active`:** partial
> EXCLUDE-USING-btree constraint `EXCLUDE (platform_market_id WITH =) WHERE
> (link_state = 'active')`.  At most ONE active link per platform_market_id.
> Retired/quarantined rows coexist freely.  Holden H:18-21: "the single
> biggest schema-safety risk in the entire canonical layer is link uniqueness
> leakage" — the canonical layer's core contract is "what is the canonical
> identity of this platform row?", and two active links per platform row
> would resolve that question to arbitrary results depending on JOIN ordering.

Indexes: PK + EXCLUDE-partial-active (covers `link_state = 'active'` queries) +
`idx_canonical_market_links_canonical_market_id` + `idx_canonical_market_links_algorithm_id`.

#### canonical_event_links (Link Table) — Migration 0072

Parallel structural mirror of `canonical_market_links` with column substitutions:
`canonical_market_id` -> `canonical_event_id`, `platform_market_id` -> `platform_event_id`,
`markets(id)` -> `events(id)`, `canonical_markets(id)` -> `canonical_events(id)`.  Same
EXCLUDE invariant shape with `platform_event_id` discriminator.  Same trigger pattern
(`trg_canonical_event_links_updated_at`).  Parallelism IS the contract per session-78
council L12-L13.

#### canonical_match_log (Audit Ledger, Append-Only) — Migration 0073

Forever-record of every match decision: `link` / `unlink` / `relink` / `quarantine`
on the link tables, plus `override` / `review_approve` / `review_reject` on slot
0074's review/override tables.  Append-only by application discipline — the
restricted CRUD module exposes EXACTLY ONE write function (`append_match_log_row()`);
trigger-enforcement queued for slot 0090 after a 30-day production soak.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| link_id (FK) | BIGINT | NULLABLE. -> canonical_market_links(id). **ON DELETE SET NULL** per ADR-118 V2.42 sub-amendment B — audit history outlives link deletion. |
| platform_market_id | INTEGER | NOT NULL. **DELIBERATELY NO FK** (L9). Log outlives the platform row; the (platform_market_id, canonical_market_id, decided_at, decided_by, algorithm_id) tuple anchors attribution after platform CASCADE-deletes. |
| canonical_market_id (FK) | BIGINT | NULLABLE. -> canonical_markets(id). **ON DELETE SET NULL** per Holden re-engagement P1 catch — ADR DDL was silent here; PostgreSQL's NO ACTION default would silently block DELETE FROM canonical_markets while audit history exists. Symmetric with link_id audit-survival. |
| action | VARCHAR(16) | NOT NULL. CHECK IN 7-value set ('link', 'unlink', 'relink', 'quarantine', 'override', 'review_approve', 'review_reject'). Pattern 73 SSOT pointer: `constants.py:ACTION_VALUES`. |
| confidence | NUMERIC(4,3) | NULLABLE (human overrides have no algorithmic confidence). NULL-tolerant CHECK. |
| algorithm_id (FK) | BIGINT | NOT NULL. -> match_algorithm(id). For human-decided rows, `algorithm_id = manual_v1.id` per the **manual_v1-on-human-decided-actions** category-fit convention (decided_by carries actual actor identity). |
| features | JSONB | NULLABLE. Free-form input snapshot at decision time. Schema deferred to Cohort 5+. |
| prior_link_id (FK) | BIGINT | NULLABLE. -> canonical_market_links(id). **ON DELETE SET NULL** per Holden P3 deliberate spec-strengthening (ADR DDL was FK-less; v2.42 sub-amendment B's audit-survival semantics applied parallel-by-design). |
| decided_by | VARCHAR(64) | NOT NULL. Free-text actor. Pattern 73 SSOT pointer: `constants.py:DECIDED_BY_PREFIXES`. |
| decided_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). Drives operator audit hot-path `ORDER BY decided_at DESC`. |
| note | TEXT | NULLABLE. Free-text operator-readable explanation. |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). By convention `created_at == decided_at` (within microseconds for the standard `append_match_log_row()` write path). |

No `updated_at` column — append-only by definition.  Indexes: PK +
`idx_canonical_match_log_decided_at` (DESC ordering) + `idx_canonical_match_log_platform_market_id`
(L9 query target) + `idx_canonical_match_log_link_id` (partial WHERE link_id IS NOT NULL) +
`idx_canonical_match_log_algorithm_id` (Miles' operator-alert "group by algorithm_id" queries).

> **manual_v1-on-human-decided-actions convention (slot 0073 + extended in slot 0074):**
> Override + review-state-transition rows in `canonical_match_log` set `algorithm_id =
> manual_v1.id`.  This is a category-fit placeholder, not a fact — `decided_by` carries
> the actual human actor identity (`'human:<username>'`).  Future log-readers MUST NOT
> mistake `algorithm_id = manual_v1.id` on `action='override'` rows for "manual_v1
> decided this."  Enforced by the slot-0074 CRUD modules (which resolve `manual_v1.id`
> via the public `crud_canonical_match_log.get_manual_v1_algorithm_id()` helper and
> pass it explicitly to `append_match_log_row()`); the schema cannot enforce the
> convention.

#### observation_source (Lookup, Pattern 81 instance) — Migration 0075

Open-enum lookup table for ingestion sources.  Pattern 81 sibling to `match_algorithm`.
3 Phase 1 seed rows: `(espn, api, NULL)`, `(kalshi, api, NULL)`, `(manual, manual, NULL)`.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| source_key | VARCHAR(64) | NOT NULL. Operator-readable identifier (e.g. `'espn'`, `'kalshi'`, `'manual'`, future `'noaa'`/`'bls'`/`'fivethirtyeight'`). Pattern 73 SSOT pointer (documentation-not-enforcement): `constants.py:PHASE_1_SOURCE_KEYS`. |
| source_kind | VARCHAR(32) | NOT NULL. Ingestion mechanism (`'api'`, `'scrape'`, `'manual'`, `'derived'`). Pattern 73 SSOT pointer (documentation-not-enforcement): `constants.py:SOURCE_KIND_VALUES`. |
| authoritative_for | JSONB | NULLABLE. Array of `canonical_observations.observation_kind` values this source is authoritative for. Phase 1 seeds set NULL until canonical observation kinds land in slot 0076+. |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |
| retired_at | TIMESTAMPTZ | NULLABLE. NULL = active. |

`CONSTRAINT uq_observation_source_key UNIQUE (source_key)`.  **Zero CHECK constraints**
by Pattern 81 design — `source_key` and `source_kind` are open enums encoded as
lookup-table rows; the table itself is the canonical authoritative store.  No `updated_at`
— sources are immutable per Critical Pattern #6 (re-categorize by inserting a new row
+ retiring the old).  FK-target preparedness: slot 0076+ `canonical_observations` and
`canonical_event_phase_log` will FK INTO `observation_source.id`.

> **Documentation-not-enforcement framing:** unlike `LINK_STATE_VALUES` /
> `ACTION_VALUES` (which back DDL CHECKs with closed-enum semantics), the
> `PHASE_1_SOURCE_KEYS` and `SOURCE_KIND_VALUES` Python constants are NOT
> closed enforcement sets — they document the Phase 1 baseline at code level.
> CRUD code (when it ships in Cohort 5+) will treat `source_key` as opaque
> text and validate against the lookup table itself, not against the constant.

#### canonical_match_reviews (Operational State, State Machine) — Migration 0074

Operator review workflow for matching decisions.  4-state machine; NOT append-only
(`transition_review` UPDATEs the row; audit trail goes to `canonical_match_log`).

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| link_id (FK) | BIGINT | NOT NULL. -> canonical_market_links(id). **ON DELETE CASCADE** — reviews die with their link (review row meaningless without the link); audit trail survives via slot 0073's SET NULL. |
| review_state | VARCHAR(16) | NOT NULL. CHECK IN ('pending', 'approved', 'rejected', 'needs_info'). Pattern 73 SSOT pointer: `constants.py:REVIEW_STATE_VALUES`. |
| reviewer | VARCHAR(64) | NULLABLE (pending rows have no reviewer). DECIDED_BY_PREFIXES convention. |
| reviewed_at | TIMESTAMPTZ | NULLABLE until state transitions out of 'pending'. |
| flagged_reason | VARCHAR(256) | NULLABLE. Free-text operator note. CRUD-boundary length validation enforces the 256 cap. |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |

No `updated_at` — `reviewed_at` is the state-transition timestamp; created_at +
reviewed_at fully describe the row's lifecycle.  Indexes: PK +
`idx_canonical_match_reviews_link_id` (FK target) +
`idx_canonical_match_reviews_review_state` (partial WHERE review_state = 'pending'
— operator alert hot path) + `idx_canonical_match_reviews_reviewed_at`
(partial WHERE reviewed_at IS NOT NULL, DESC ordering).

State machine (10 allowed forward transitions; self-transitions rejected uniformly):
`pending -> approved | rejected | needs_info`; `needs_info -> approved | rejected | pending`;
`approved -> needs_info`; `rejected -> needs_info`.  Transitions to `approved` /
`rejected` write a `canonical_match_log` row with `action='review_approve'` /
`'review_reject'`.

#### canonical_match_overrides (Operator-Asserted Policy) — Migration 0074

Operator overrides of algorithmic matching decisions.  INSERT-then-DELETE lifecycle
(no UPDATE path); `delete_override()` is the sanctioned removal API.

| Column | Type | Notes |
|--------|------|-------|
| id (PK) | BIGSERIAL | |
| platform_market_id (FK) | INTEGER | NOT NULL. -> markets(id). ON DELETE CASCADE. |
| canonical_market_id (FK) | BIGINT | NULLABLE. -> canonical_markets(id). **ON DELETE RESTRICT** (operationally load-bearing — MUST_MATCH overrides assert "this canonical IS the right one"; canonical row cannot silently disappear). NULLABLE per polarity discriminator. |
| polarity | VARCHAR(16) | NOT NULL. CHECK IN ('MUST_MATCH', 'MUST_NOT_MATCH'). Pattern 73 SSOT pointer: `constants.py:POLARITY_VALUES` (pre-positioned in slot 0072 per S82 council; finally USED in slot 0074). |
| reason | TEXT | NOT NULL. Free-text operator-readable explanation. CHECK length(trim(reason)) > 0. |
| created_by | VARCHAR(64) | NOT NULL. DECIDED_BY_PREFIXES convention. Always `'human:<username>'` for overrides (overrides are human-decided by definition). |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now(). |

`UNIQUE (platform_market_id)` — at most ONE override per platform market.  To
replace, operator must DELETE + INSERT (audit trail goes to `canonical_match_log`
with `action='override'` on each operation).

> **Load-bearing invariant — polarity-pairing CHECK:**
>
> ```
> CONSTRAINT ck_canonical_match_overrides_polarity_pairing CHECK (
>     (polarity = 'MUST_NOT_MATCH' AND canonical_market_id IS NULL)
>     OR (polarity = 'MUST_MATCH' AND canonical_market_id IS NOT NULL)
> )
> ```
>
> Enforces `MUST_NOT_MATCH <-> canonical_market_id IS NULL` and
> `MUST_MATCH <-> canonical_market_id IS NOT NULL`.  Three-valued-logic-safe
> form (`IS NULL` / `IS NOT NULL`, NOT `= NULL` / `!= NULL` — `x = NULL`
> evaluates to NULL not TRUE in SQL and would silently admit any row).
> CRUD-layer validation in `create_override()` raises `ValueError` BEFORE
> INSERT; the DDL CHECK is defense-in-depth against direct-SQL bypass.

Indexes: PK + UNIQUE (covers `platform_market_id`) +
`idx_canonical_match_overrides_canonical_market_id` (partial WHERE
canonical_market_id IS NOT NULL — excludes MUST_NOT_MATCH NULL rows) +
`idx_canonical_match_overrides_polarity` + `idx_canonical_match_overrides_created_at`
(DESC ordering for recent-activity hot path).

---

### K. Canonical Observation Layer (Cohort 4 — ADR-118 V2.43 + V2.45)

The 4 Cohort-4 slots (0078 + 0079 + 0080 + 0082) plus the Cohort 4 close-out
slot 0084 (V2.45 redesign) establish the canonical-tier observation pipeline.
Cohort 4 informally CLOSED in session 90 with V2.45; slot 0083 (originally
the `v_temporal_alignment` view rewire) was deferred to Cohort 5+ per
ADR-118 V2.45 Item 1 + issue #1141.

#### canonical_observations (Partitioned Parent — Migration 0078, V2.45-redesigned in 0084)

THE canonical-tier observation parent — every cross-domain observation
(game state, weather, poll, econ print, news event, market snapshot — full
kind set per `OBSERVATION_KIND_VALUES`) writes one row here through the
restricted CRUD function `crud_canonical_observations.append_observation_row()`.
Per-kind projection tables (`weather_observations`, `poll_releases`, etc.)
materialize in Cohort 9+; Cohort 4 ships the parent + writer skeleton.

Post-V2.45 column shape (12 columns; payload dropped + canonical_event_id
renamed):

| Column | Type | Constraint | Notes |
|--------|------|------------|-------|
| `id` | BIGSERIAL | PK part 1 | Composite PK (id, ingested_at) per partition semantics |
| `observation_kind` | VARCHAR(32) | NOT NULL + CHECK 6-value | Pattern 73 SSOT: `OBSERVATION_KIND_VALUES` |
| `source_id` | BIGINT | NOT NULL FK observation_source ON DELETE RESTRICT | Source registry authoritative |
| `canonical_primary_event_id` | BIGINT | NULL FK canonical_events ON DELETE SET NULL | **V2.45 RENAMED** from canonical_event_id; denormalized "primary tag" mirroring `link_kind='primary'` row in canonical_observation_event_links |
| `payload_hash` | BYTEA | NOT NULL | SHA-256 of canonicalized JSON; dedup key |
| `event_occurred_at` | TIMESTAMPTZ | NULL | Real-world event time (NULL for econ/news) |
| `source_published_at` | TIMESTAMPTZ | NOT NULL | When source emitted |
| `ingested_at` | TIMESTAMPTZ | NOT NULL DEFAULT now(), PK part 2 | Partition key |
| `valid_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | Bitemporal "valid from" |
| `valid_until` | TIMESTAMPTZ | NULL | NULL = currently-valid |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | Audit-column convention |
| `updated_at` | TIMESTAMPTZ | NULL | Maintained by trg_canonical_observations_updated_at |

**V2.45 Item 4 — payload column DROPPED.** Per-kind projection tables hold
the typed source data (game_states.situation + game_states.linescores for
ESPN's untyped fields; market_snapshots has no JSONB residue because
Kalshi is fully typed). The new Pattern 73 SSOT discipline rule: adding a
new `observation_kind` value requires its projection table to exist
BEFORE the vocabulary expansion.

5 indexes (slot 0078 baseline; rename in V2.45 preserves index definitions —
PG semantic auto-follows RENAME COLUMN):
- `idx_canonical_observations_event_id` partial WHERE canonical_primary_event_id IS NOT NULL
- `idx_canonical_observations_kind_ingested` (observation_kind, ingested_at DESC)
- `idx_canonical_observations_source_published` (source_id, source_published_at DESC)
- `idx_canonical_observations_event_occurred` partial WHERE event_occurred_at IS NOT NULL
- `idx_canonical_observations_currently_valid` (canonical_primary_event_id, ingested_at DESC) WHERE valid_until IS NULL

3 CHECK constraints (slot 0078): observation_kind 6-value vocab; clock-skew
5-min guard; bitemporal validity. 1 dedup UNIQUE (source_id, payload_hash,
ingested_at). 4 monthly partitions pre-created (2026-05/06/07/08).

**Composite-FK invariant (V2.43 Item 3):** future per-kind projection
tables FK INTO canonical_observations MUST use composite-FK shape
`(observation_id, observation_ingested_at) REFERENCES canonical_observations(id,
ingested_at)`. Surrogate `id` alone is insufficient (PG cannot route the
FK lookup to the correct partition without the partition key).

#### canonical_event_phase_log (Audit Ledger — Migration 0079)

Append-only audit ledger for canonical_events.lifecycle_phase transitions.
Auto-populated via AFTER INSERT / AFTER UPDATE OF lifecycle_phase trigger
on canonical_events (function `log_canonical_event_phase_transition`):
- INSERT: previous_phase=NULL, new_phase=NEW.lifecycle_phase, changed_by='system'
- UPDATE (when phase changes per IS DISTINCT FROM): previous_phase=OLD,
  new_phase=NEW, changed_by='system'

Mirrors slot 0073 `canonical_match_log` discipline. Append-only via
application discipline; trigger-enforced version queued for slot 0090
post-soak. Pattern 73 SSOT: 8-value lifecycle_phase vocabulary mirrored
verbatim from `CANONICAL_EVENT_LIFECYCLE_PHASES` (constants.py); CHECK on
both `previous_phase` (NULL OK) and `new_phase` (NOT NULL).

#### canonical_observation_event_links (Multi-Event Tagging Link Table — Migration 0084 / V2.45 Item 8)

Many-to-many link table for multi-event tagging. News observations fan out
across multiple canonical_events; econ prints affect dozens of econ markets.
The denormalized `canonical_observations.canonical_primary_event_id` column
is the hot-path read mirroring the `link_kind='primary'` row in this table;
this table carries the full multi-event semantic.

**FK shape: composite per V2.43 Item 3 invariant.** All FKs into
`canonical_observations` use composite shape `(observation_id,
observation_ingested_at) REFERENCES canonical_observations(id, ingested_at)`
because the canonical_observations PK is composite (id, ingested_at) per
its partitioning. The Builder discovered this at upgrade time via PG
rejection of single-column FK; the V2.43 Item 3 invariant carries
forward to slot 0084 (the build spec's single-column `REFERENCES
canonical_observations(id)` was superseded mid-flight).

| Column | Type | Constraint | Notes |
|--------|------|------------|-------|
| `observation_id` | BIGINT | NOT NULL, PK part 1 | Composite FK part with observation_ingested_at |
| `observation_ingested_at` | TIMESTAMPTZ | NOT NULL, PK part 2 | Partition-key companion (V2.43 Item 3) |
| `canonical_event_id` | BIGINT | NOT NULL FK canonical_events ON DELETE RESTRICT, PK part 3 | Cannot delete event while linked observations exist |
| `link_kind` | VARCHAR(16) | NOT NULL + CHECK 4-value | Pattern 73 SSOT canonical home (vocabulary lives in CHECK until Cohort 5+ CRUD ships) |
| `confidence` | NUMERIC(5,4) | NULL + CHECK 0-1 range | NULL for manual operator tags |
| `linked_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | Audit |
| `linked_by` | VARCHAR(64) | NOT NULL | Free-form attribution: 'matcher_v1' / 'operator_xyz' / 'manual_seed' / etc. |

Composite FK constraint: `fk_link_observation FOREIGN KEY (observation_id,
observation_ingested_at) REFERENCES canonical_observations(id, ingested_at)
ON DELETE CASCADE`. Link rows die with their observation.

`link_kind` 4-value vocabulary: `primary` / `secondary` / `derived` / `speculative`.

3 indexes:
- `idx_link_observation` (observation_id, observation_ingested_at) — observation-
  side reverse lookup (composite to match the FK shape)
- `idx_link_canonical_event` (canonical_event_id, link_kind) — event-side
  reverse lookup with kind filter
- `idx_link_one_primary_per_observation` UNIQUE (observation_id,
  observation_ingested_at) WHERE link_kind = 'primary' — enforces "at most
  one primary link per observation (full composite identifier)" invariant;
  secondary / derived / speculative may be many-per-observation

#### temporal_alignment (REDESIGNED — Migration 0084 / V2.45 Item 6)

**V2.45 redesign supersedes the slot-0035-era 20-column denormalized
fact-table shape.** New pure-linkage shape with composite FKs into the
partitioned canonical_observations parent (V2.43 Item 3 invariant):

| Column | Type | Constraint | Notes |
|--------|------|------------|-------|
| `id` | BIGSERIAL | PK | Surrogate; not partitioned |
| `observation_a_id` | BIGINT | NOT NULL | Composite FK part with observation_a_ingested_at |
| `observation_a_ingested_at` | TIMESTAMPTZ | NOT NULL | Partition-key companion |
| `observation_b_id` | BIGINT | NOT NULL | Composite FK part with observation_b_ingested_at |
| `observation_b_ingested_at` | TIMESTAMPTZ | NOT NULL | Partition-key companion |
| `canonical_event_id` | BIGINT | NULL FK canonical_events ON DELETE SET NULL | Denormalized hot-path; matcher writes |
| `time_delta_seconds` | NUMERIC(10,3) | NOT NULL | Pairing time gap; ms precision |
| `alignment_quality` | VARCHAR(8) | NOT NULL + CHECK 5-value | Pattern 73 SSOT canonical home in CHECK (until Cohort 5+ CRUD) |
| `aligned_at` | TIMESTAMPTZ | NOT NULL | When matcher created the pair |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | Audit |

Composite FK constraints (NO ACTION on delete — matcher owns lifecycle):
- `fk_temporal_alignment_observation_a FOREIGN KEY (observation_a_id,
  observation_a_ingested_at) REFERENCES canonical_observations(id, ingested_at)`
- `fk_temporal_alignment_observation_b FOREIGN KEY (observation_b_id,
  observation_b_ingested_at) REFERENCES canonical_observations(id, ingested_at)`

`alignment_quality` 5-value vocabulary: `exact` / `good` / `fair` / `poor` / `stale`.

3 constraints:
- `uq_alignment_pair` UNIQUE (observation_a_id, observation_a_ingested_at,
  observation_b_id, observation_b_ingested_at) — matcher idempotence
- `ck_distinct_observations` CHECK NOT (observation_a_id =
  observation_b_id AND observation_a_ingested_at = observation_b_ingested_at) —
  self-pair (full composite match) meaningless
- `ck_alignment_quality` CHECK (alignment_quality IN (...)) — vocabulary

3 indexes:
- `idx_alignment_canonical_event` (canonical_event_id, aligned_at DESC)
- `idx_alignment_observation_a` (observation_a_id, observation_a_ingested_at)
- `idx_alignment_observation_b` (observation_b_id, observation_b_ingested_at)

**Per-domain typed views deferred to Cohort 5+ (#1141):**
- `v_temporal_alignment_sports` — JOINs to market_snapshots + game_states
- `v_temporal_alignment_weather` — when ADR-119 Phase 1 weather lands
- `v_temporal_alignment_election` — Phase 3+

**Operational note:** temporal_alignment was 0 rows at slot 0084 ship time
(temporal_alignment_writer existed in code but was not registered in
scheduler_status — discovered via session 90 MCP-grounded premise check).
DROP TABLE + CREATE TABLE is zero-data-loss; Cohort 5+ writer rewrite picks
up the new shape.

---

## Views

(Unchanged from V2.1 — 19 views.  No new views ship in Cohort 3 or 4.
Cohort 5+ will introduce trust-tier views like `v_canonical_market_llm`
per the matching-layer surface; per-domain `v_temporal_alignment_*`
views per V2.45 Item 6 + #1141.  Full table at V2.1 § Views.)

---

## SSOT Python Vocabularies (`src/precog/database/constants.py`)

Per CLAUDE.md Critical Pattern #8 (Pattern 73 SSOT): closed-enum vocabularies
that appear in both DDL CHECK constraints AND Python code (CRUD validation,
state-machine transitions, projection writers, tests) live as `Final[tuple[str, ...]]`
constants in `src/precog/database/constants.py`.  DDL CHECKs cite the constant
by name in a SQL comment; CRUD modules import + USE the constants in real-guard
`ValueError`-raising validation (NOT side-effect-only `# noqa: F401`).

8 vocabularies total — 1 from Cohort 1 carry-forward + 7 from Cohort 3:

| Constant | Values | Source migration | DDL consumers |
|---|---|---|---|
| `CANONICAL_EVENT_LIFECYCLE_PHASES` | 8 (`proposed`, `listed`, `pre_event`, `live`, `suspended`, `settling`, `resolved`, `voided`) | 0070 (V2.40 carry-forward) | `canonical_events.lifecycle_phase` CHECK; future `canonical_event_phase_log.phase` CHECK (future cohort) |
| `LINK_STATE_VALUES` | 3 (`active`, `retired`, `quarantined`) | 0072 (slot 2) | `canonical_market_links.link_state` CHECK; `canonical_event_links.link_state` CHECK |
| `ACTION_VALUES` | 7 (`link`, `unlink`, `relink`, `quarantine`, `override`, `review_approve`, `review_reject`) | 0073 (slot 3) | `canonical_match_log.action` CHECK |
| `DECIDED_BY_PREFIXES` | 3 prefixes (`human:`, `service:`, `system:`) | 0073 (slot 3) | `canonical_match_log.decided_by` (free-text; CRUD-layer real-guard); `canonical_market_links.decided_by`; `canonical_event_links.decided_by`; `canonical_match_reviews.reviewer`; `canonical_match_overrides.created_by` |
| `REVIEW_STATE_VALUES` | 4 (`pending`, `approved`, `rejected`, `needs_info`) | 0074 (slot 5) | `canonical_match_reviews.review_state` CHECK |
| `POLARITY_VALUES` | 2 (`MUST_MATCH`, `MUST_NOT_MATCH`) | 0074 (slot 5; pre-positioned 0072) | `canonical_match_overrides.polarity` CHECK |
| `PHASE_1_SOURCE_KEYS` | 3 (`espn`, `kalshi`, `manual`) | 0075 (slot 4) | `observation_source` (lookup-table store IS canonical; constant is documentation-not-enforcement) |
| `SOURCE_KIND_VALUES` | 4 (`api`, `scrape`, `manual`, `derived`) | 0075 (slot 4) | `observation_source` (lookup-table store IS canonical; constant is documentation-not-enforcement) |

> **Documentation-not-enforcement vs closed-enum carve-out:** the first 6
> constants are closed enforcement sets — adding a value requires lockstep
> update of constant AND DDL CHECK.  The last 2 (`PHASE_1_SOURCE_KEYS`,
> `SOURCE_KIND_VALUES`) are open-enum lookup tables (Pattern 81); the constants
> document the Phase 1 baseline only — the lookup table itself is the
> authoritative store, and CRUD code treats `source_key` as opaque text.

> **Real-guard vs side-effect-only convention:** slot 0072's `LINK_STATE_VALUES`
> shipped with side-effect-only `# noqa: F401` import in test files.  Per #1085
> finding #2 strengthening (slot 0073+ inheritance), the convention upgraded to
> real-guard validation: CRUD modules raise `ValueError` if the input value isn't
> in the constant.  Slot 0072's side-effect-only shape did not survive into slot
> 0073/0074/0075.

---

## Migration Catalog

80 migration files cover slots 0001-0084 (slots 0029, 0081, 0083 intentionally
skipped — `portfolio_fills` design abandoned pre-baseline for 0029; slot 0081
retired into 0080 per V2.43 Item 2 / Q5 user adjudication; slot 0083 deferred
to Cohort 5+ per V2.45 Item 1 / issue #1141).  Chain head is 0084 via the
sequence `0073 -> 0075 -> 0074 -> 0076 -> 0077 -> 0078 -> 0079 -> 0080 ->
0082 -> 0084` — non-monotonic on the 0073-0074 hop AND on the 0080-0082 hop
(0081 hole) AND on the 0082-0084 hop (0083 hole); monotonic on 0074 -> 0080
and 0082 -> 0084.  Cohort 4 additions this revision (full pre-Cohort-4
catalog at V2.3 § Migration Catalog):

| Migration | Description | Impact |
|-----------|-------------|--------|
| **0071** | **Cohort 3 slot 1 — `match_algorithm` lookup (Pattern 81 precursor)** | New table + 1 seed row; +10-edit slot displacement bump on prior migrations' references |
| **0072** | **Cohort 3 slot 2 — `canonical_market_links` + `canonical_event_links` link tables** | 2 new tables + EXCLUDE-USING-btree partial-active invariant + 2 BEFORE UPDATE triggers |
| **0073** | **Cohort 3 slot 3 — `canonical_match_log` audit ledger (append-only via application discipline)** | 1 new table + 4 indexes + 7-value action CHECK + 4 FK polarity decisions (link_id SET NULL, platform_market_id no-FK, canonical_market_id SET NULL Holden P1, prior_link_id SET NULL Holden P3) |
| **0075** | **Cohort 3 slot 4 — `observation_source` lookup (Pattern 81 sibling)** | New table + 3 seed rows + zero CHECK constraints (open-enum design); ships AHEAD of 0074 in the chain |
| **0074** | **Cohort 3 slot 5 — `canonical_match_overrides` + `canonical_match_reviews` (CLOSES Cohort 3 main arc)** | 2 new tables + 6 indexes + 5 CHECK constraints (incl. polarity-pairing three-valued-logic-safe form) + 1 UNIQUE constraint |
| **0076** | **Cohort 3 close-out slot 0076 — generic `set_updated_at()` retrofit + canonical_events orphan-trigger gap closure (V2.42 sub-amendment A)** | 1 new function + 1 net-new trigger (canonical_events) + 3 trigger rewires (canonical_markets / canonical_market_links / canonical_event_links) + 3 per-table function DROPs |
| **0077** | **Cohort 3 close-out slot 0077 — `canonical_events.{game_id, series_id}` FK ON DELETE SET NULL retrofit (V2.42 sub-amendment B)** | 2 ALTER CONSTRAINT round-trips on canonical_events; constraint NAMES preserved for pinned-by-name test compatibility |
| **0078** | **Cohort 4 slot 0078 — `canonical_observations` partitioned parent + 4 monthly partitions + writer skeleton** | New parent table + composite PK (id, ingested_at) + RANGE-monthly partitioning + 5 indexes + 1 dedup UNIQUE + 3 CHECK constraints + BEFORE UPDATE trigger + 3 SSOT vocab tuples + restricted-API CRUD + ServiceSupervisor registration + feature flag (default false until session 87+ soak) |
| **0079** | **Cohort 4 slot 0079 — `canonical_event_phase_log` audit ledger + auto-population trigger** | New table + 3 indexes + 2 CHECKs + 1 trigger function + 1 trigger; append-only via application discipline (mirrors slot 0073) |
| **0080** | **Cohort 4 slot 0080 — combined `game_states` + `games` `canonical_event_id` nullable FKs (Pattern 84 by analogy)** | 2 ALTER TABLE rounds (each: ADD COLUMN + NOT VALID + VALIDATE + INDEX) + 2 indexes; first-ever Pattern 84 by-analogy use for FK on populated tables; slot 0081 retired into 0080 per V2.43 Item 2 (permanent slot 0081 hole) |
| **0082** | **Cohort 4 slot 0082 — `temporal_alignment.canonical_event_id` nullable FK (Pattern 84 2nd by-analogy use)** | 1 ALTER TABLE round + 1 index; N=2 threshold reached → Pattern 84 promoted to V1.42 in PR #1138 |
| **0084** | **Cohort 4 close-out slot 0084 — CANONICAL LAYER REDESIGN (V2.45 Items 1/4/6/8); chain head** | DROP COLUMN canonical_observations.payload (Item 4); RENAME canonical_observations.canonical_event_id → canonical_primary_event_id (Item 8); DROP TABLE temporal_alignment + CREATE TABLE with new pure-linkage shape (Item 6); CREATE TABLE canonical_observation_event_links (Item 8); slot 0083 deferred to Cohort 5+ (Item 1, permanent slot 0083 hole) |

All migrations have `downgrade()` functions.  Slot 0029 + slot 0081 + slot
0083 intentionally skipped (portfolio_fills eliminated for 0029; slot 0081
retired per V2.43 Item 2; slot 0083 deferred per V2.45 Item 1).  **Non-
monotonic chain segments:** slot 0074's `down_revision` is 0075 (not 0073)
because slot 0075 ships ahead of slot 0074 in the chain.  Slot 0082's
`down_revision` is 0080 (skipping retired 0081 hole).  Slot 0084's
`down_revision` is 0082 (skipping deferred 0083 hole).  From 0074 onward
the chain is logically monotonic at the `down_revision` graph level:
0074 -> 0076 -> 0077 -> 0078 -> 0079 -> 0080 -> 0082 -> 0084.  A reader
scanning by lexical filename order will see 0075 appearing earlier than
0074 AND will not find 0081 / 0083 files at all — trust the `down_revision`
chain, not the filenames.

---

## Cohort Roadmap (Forward-Pointer)

Cohort 3 closed in session 82 (PR #1094); Cohorts 4-9 are NOT YET STARTED per
ADR-118 + ADR-119 (in `docs/foundation/ARCHITECTURE_DECISIONS.md`), which is
the canonical SSOT for cohort definitions.

| Cohort | Slots (planned/shipped) | Focus |
|--------|-------------------------|-------|
| 1A + 1B | 0067-0068 (SHIPPED s71-72) | canonical_event_* + canonical_entity_* + canonical_participant_roles |
| 2 | 0069 (SHIPPED s72) | canonical_markets |
| 1 carry-forward | 0070 (SHIPPED s75) | additive integrity fences (lifecycle_phase CHECK + cross-domain partial UNIQUE) |
| **3** | **0071-0075 (SHIPPED s79-82) + close-out 0076-0077 (SHIPPED s83)** | **matching infrastructure: `match_algorithm` (lookup) + `canonical_market_links` + `canonical_event_links` (link tables) + `canonical_match_log` (audit ledger) + `observation_source` (lookup) + `canonical_match_overrides` + `canonical_match_reviews` (operator state machine). Cohort 3 main arc: 5-slot LOCK per session-78 council adjudication; CLOSED with slot 0074 in session 82. Close-out post-retrofits (slots 0076 + 0077, session 83): generic `set_updated_at()` retrofit + canonical_events orphan-trigger gap closure (V2.42 sub-amendment A) + canonical_events FK ON DELETE SET NULL polarity flip (V2.42 sub-amendment B).** |
| 4 | 0078-0081 (queued) | dim/fact split refinement (Cohort 4 main scope; 0076 + 0077 close-out post-retrofits previously planned for Cohort 4 ship instead in Cohort 3 close-out per session-83 design council adjudication) |
| 5 | 0082-0085 (queued) | event-state machine + canonical_event_phase_log + matcher resolver code |
| 6 | 0086-0087 (queued) | views / CRUD / resolver |
| 7 | 0088-0093 (queued) | seeding (canonical_events 1:1 from games) + #937 |
| 8 | 0094-0097 (queued) | business-key cleanup (per ADR-119) |
| 9 | 0098-0100 (queued) | weather Phase 1 (per ADR-119) |

This roadmap is forward-looking — slot numbers are planning estimates, not
commitments.  Cohort 3's 5-slot lock displaces all downstream cohorts by +1
nominally vs ADR-118 V2.39's original numbering (Migration 0070 was consumed
by the Cohort 1 carry-forward in session 75; Cohort 3's expansion to 5 slots
in session 78 cascades the rest).

---

## Freshness Marker Discipline (Pattern 86)

This document and other living canonical docs (ARCHITECTURE_DECISIONS.md,
MASTER_REQUIREMENTS_V2.26.md) carry a freshness marker as an HTML comment
near the top of the file.  V2.3 example:

```markdown
<!-- FRESHNESS: alembic_head=0077, verified=2026-04-29, tables=58, migrations=76, last_changelog_migration=0077 -->
```

For schema docs the fields are: `alembic_head`, `verified`, `tables`,
`migrations`, `last_changelog_migration`.  Other living-doc types use different
field sets (ADR docs use `adr_count` + `last_adr`; requirements use `req_count`
+ `last_review_session`).

The discipline is codified in **Pattern 86: Living-Doc Freshness Markers**
(see `docs/guides/DEVELOPMENT_PATTERNS.md` V1.39).  Pattern 86 is Pattern 73
(Single Source of Truth) applied to time: the canonical source-of-truth for
a doc's freshness is the doc itself, and the marker is how the doc declares
what version of truth it claims to represent.  V2.0 was the cautionary tale —
its marker decayed 19 migrations behind its body without surfacing because no
concrete marker means "is this doc fresh?" has no answer.  V2.1 demonstrated
the canonical pattern shape; V2.2 maintains it.

> **alembic_head subtlety (Cohort 3 origin):** when migrations ship in non-
> monotonic chain order (slot N+1 lands BEFORE slot N because they're
> independent), the freshness marker's `alembic_head` field MUST track the
> chain head (no successor in `down_revision` graph), NOT the lexically-
> largest filename.  Cohort 3's `0073 -> 0075 -> 0074` chain makes 0074 the
> head despite 0075's higher filename.  Verify via `alembic current` (or MCP
> `query_database "SELECT version_num FROM alembic_version"`); never infer
> the head by sorting filenames.

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| 2.4 | 2026-05-02 | Re-sync with alembic_head=0084 after Cohort 4 ship (slots 0078 + 0079 + 0080 + 0082) + Cohort 4 close-out V2.45 redesign (slot 0084).  ADR-118 V2.45 Items 1/4/6/8 codified.  Migration count 76 → 80 (slots 0081 + 0083 holes intentional).  Net new tables: canonical_observations + 4 monthly partitions + canonical_event_phase_log + canonical_observation_event_links; temporal_alignment redesigned as pure-linkage shape (net-zero in table count).  Total: 58 → 62.  New § K (Canonical Observation Layer — Cohort 4) with full column-level details for canonical_observations / canonical_event_phase_log / canonical_observation_event_links / new temporal_alignment.  § D temporal_alignment entry redirected to § K post-V2.45.  Forward-pointer to V2.46 + new doc CANONICAL_LAYER_RELATIONSHIPS.md (issue #1143). |
| 2.3 | 2026-04-29 | Re-sync with alembic_head=0077 after Cohort 3 close-out post-retrofits (slots 0076 + 0077, session 83).  V2.42 sub-amendments A (generic `set_updated_at()` retrofit + canonical_events orphan-trigger gap closure) + B (canonical_events FK ON DELETE SET NULL) closed.  Migration count 74 -> 76; chain head 0074 -> 0077 via monotonic 0074 -> 0076 -> 0077.  § G annotated with post-Migration-0077 SET NULL caveat on `canonical_events.game_id` / `canonical_events.series_id` and post-Migration-0076 trigger updates.  § J adds canonical-tier trigger function inventory (4 BEFORE UPDATE triggers all EXECUTE generic `set_updated_at()`).  Migration catalog adds rows 0076 + 0077.  Cohort roadmap refresh: Cohort 3 close-out marked SHIPPED; Cohort 4+ slot numbers adjusted (0076 + 0077 previously projected as Cohort 4 ship in Cohort 3 close-out instead). SSOT Python vocabulary count UNCHANGED at 8 (no new constants this revision).  Same structural shape as V2.2. |
| 2.2 | 2026-04-29 | Re-sync with alembic_head=0074 (chain head; non-monotonic 0073->0075->0074). +7 Cohort-3 tables across Migrations 0071-0075 (5 canonical_* + 2 Pattern 81 lookups). New § J (Canonical Matching Layer). New § SSOT Python Vocabularies (8 constants in constants.py: 1 from cohort-1 carry-forward + 7 from cohort 3). Quick Reference counts updated (51->58 tables, 69->74 migrations, 52->54 JSONB columns, 4->6 Pattern 81 instances). Pattern 86 freshness-marker doc adds non-monotonic-chain subtlety note. |
| 2.1 | 2026-04-26 | Re-sync with alembic_head=0070. +8 canonical_* tables (Cohorts 1A+1B+2). Migrations 0049-0070 catalogued. Pattern 86 freshness-marker demonstration. View count corrected (8 -> 19). SCD-2 count corrected (6 -> 11). JSONB column count refresh (25+ -> 52). |
| 2.0 | 2026-04-05 | Complete rewrite. All 42 tables, 8 views, 47 migrations documented. (Marker drifted 19 migrations behind during the gap to V2.1.) |
| 1.16 | 2026-04-03 | Migrations 0044-0048 documented. Gaps for 0014-0043. |
| 1.0 | 2025-10-28 | Initial schema summary. |
