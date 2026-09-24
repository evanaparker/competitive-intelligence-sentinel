# Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Supabase Postgres schema for Competitive Intelligence Sentinel — every table, enum, constraint, and index defined in the design spec — on a real project, verified end-to-end.

**Architecture:** A new Supabase project holds the schema. Each migration is written as a local SQL file under `supabase/migrations/` (so the schema is versioned in git) and then applied to the live project via the Supabase MCP `apply_migration` tool. Tables are created in dependency order (parents before children with FKs), followed by indexes, then RLS is enabled on every table with no policies — since the PoC's only client is server-side code using the `service_role` key, which bypasses RLS regardless, enabling RLS with zero policies is a free safety net against an anon-key client ever being pointed at these tables by accident.

**Tech Stack:** Supabase (Postgres 17, pgvector extension), SQL migrations, Supabase MCP tools (`apply_migration`, `execute_sql`, `list_tables`, `get_advisors`).

**Spec:** [docs/superpowers/specs/2026-09-23-data-model-design.md](../specs/2026-09-23-data-model-design.md)

## Global Constraints

- Postgres via Supabase; `pgvector` extension required for `signals.embedding`.
- `signals.embedding` is `vector(1024)` — dimension fixed by Voyage-3.5.
- Single-user PoC: no reviewer/user identity columns anywhere in the schema.
- Snapshot content is stored directly in a Postgres `text` column (`snapshots.content`), not in object storage.
- All artifacts (migration files, docs, commit messages) are written in English.
- `materiality_score` is constrained to the range 1–10 via a `check` constraint.

## Review Focus

- Cascading a `competitors` delete must remove every dependent row all the way down to `feedback` — a missing `on delete cascade` on any one FK leaves orphaned rows silently. → tested in Task 8.
- Duplicate inserts must be rejected by the database, not silently accepted — `sources (competitor_id, url)` and `signals.snapshot_id` uniqueness. → tested in Task 8.
- `materiality_score` outside 1–10 (0, 11, negative) must be rejected by the check constraint, not stored. → tested in Task 8.
- The migration files are not idempotent (`create type` has no `if not exists` guard in Postgres) — re-running Task 3's SQL against an already-migrated database will error. This is expected; do not "fix" by silently swallowing the error. → documented in Task 3, not silently retried anywhere.
- RLS is enabled with zero policies, which makes every table invisible to the `anon`/`authenticated` roles by design (PostgREST returns an empty result, not an error). Any future code must connect with the `service_role` key — using the publishable/anon key will look like "no rows" bugs, not permission errors. → documented in Task 7 and the repo README.

---

## File Structure

- Create: `supabase/migrations/0001_extensions.sql` — enables `pgvector`.
- Create: `supabase/migrations/0002_enums.sql` — the five enum types.
- Create: `supabase/migrations/0003_core_tables.sql` — `competitors`, `sources`, `snapshots`.
- Create: `supabase/migrations/0004_signal_insight_tables.sql` — `signals`, `insights`, `insight_signals`, `feedback`.
- Create: `supabase/migrations/0005_indexes.sql` — all performance indexes.
- Create: `supabase/migrations/0006_rls.sql` — enables RLS on all seven tables.
- Create: `.env.example` — placeholder names for `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (no real values ever committed).
- Modify: `README.md` — add a "Database" section documenting the project, how to connect, and the RLS/service_role note from Review Focus.

Each migration file is applied to the live Supabase project with the `apply_migration` MCP tool (not the Supabase CLI, since this environment has no local Supabase CLI/Docker setup) immediately after being written, so git history and live schema never drift apart.

---

### Task 1: Create the Supabase project

**Files:**
- Modify: `.env.example` (create with placeholders)

**Interfaces:**
- Produces: `project_id` (string, e.g. `abcdefghijklmnop`) — every later task's `apply_migration`/`execute_sql` calls take this as `project_id`. Record it at the top of your working notes; it is not guessable from the project name.

- [ ] **Step 1: Create the project**

Call the Supabase MCP `create_project` tool with:
- `name`: `competitive-intelligence-sentinel`
- `organization_id`: `dzlldkdvtryasexsdnpy` (Evan's Org — confirmed via `list_organizations`, this is the only org on the account)
- `region`: `us-east-1`

If the tool asks for cost confirmation, confirm — this is a free-tier project, same as the existing `contract-review-feedback` project on this account.

- [ ] **Step 2: Wait for the project to become healthy**

Call `get_project` with the returned `project_id` every ~10 seconds until `status` is `ACTIVE_HEALTHY`. New projects typically take 1-3 minutes to provision.

- [ ] **Step 3: Verify connectivity**

Call `list_tables` with `project_id` and `schemas: ["public"]`. Expected: an empty list (no tables yet) — this confirms the project is reachable and the `public` schema exists.

- [ ] **Step 4: Write `.env.example`**

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Call `get_project_url` with `project_id` and tell the user what the real `SUPABASE_URL` value is (it's not a secret). Do **not** attempt to fill in `SUPABASE_SERVICE_ROLE_KEY` — that key is never exposed through the MCP tools (`get_publishable_keys` only returns the anon/publishable key, deliberately). Tell the user to copy the `service_role` key themselves from the Supabase dashboard (Project Settings → API) into a local `.env` file (already covered by `.gitignore`).

- [ ] **Step 5: Commit**

```bash
cd /home/sdn/Desktop/competitive-intelligence-sentinel
git add .env.example
git commit -m "Add Supabase project env template"
```

---

### Task 2: Enable the pgvector extension

**Files:**
- Create: `supabase/migrations/0001_extensions.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1.
- Produces: the `vector` extension, required by Task 4's `signals.embedding` column.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0001_extensions.sql
create extension if not exists vector;
```

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "enable_pgvector"`, and the SQL above.

- [ ] **Step 3: Verify**

Call `list_extensions` with `project_id`. Expected: an entry with `name: "vector"` in the result.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0001_extensions.sql
git commit -m "Enable pgvector extension"
```

---

### Task 3: Create enum types

**Files:**
- Create: `supabase/migrations/0002_enums.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1.
- Produces: `source_type`, `signal_classification`, `insight_confidence`, `insight_status`, `feedback_rating` — every later table's column types.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0002_enums.sql
create type source_type as enum ('pricing_page', 'changelog', 'g2', 'app_store', 'job_board', 'filing', 'other');
create type signal_classification as enum ('cosmetic', 'material');
create type insight_confidence as enum ('high', 'medium', 'low', 'needs_review');
create type insight_status as enum ('pending', 'approved', 'rejected', 'published');
create type feedback_rating as enum ('useful', 'not_useful', 'incorrect');
```

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "create_enums"`, and the SQL above.

Note: this statement has no `if not exists` guard (Postgres doesn't support one for `create type`). If this task is ever re-run against a database where it already succeeded, it will error with `type "source_type" already exists` — that's expected; don't add a guard or silently catch it, per Review Focus.

- [ ] **Step 3: Verify**

Call `execute_sql` with `project_id` and:

```sql
select typname from pg_type where typname in
  ('source_type', 'signal_classification', 'insight_confidence', 'insight_status', 'feedback_rating')
order by typname;
```

Expected: all five names returned.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0002_enums.sql
git commit -m "Add enum types for schema"
```

---

### Task 4: Create core tables — competitors, sources, snapshots

**Files:**
- Create: `supabase/migrations/0003_core_tables.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1; `source_type` enum from Task 3.
- Produces: `competitors(id)`, `sources(id)`, `snapshots(id)` — referenced as foreign keys by `signals` and `insights` in Task 5.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0003_core_tables.sql

create table competitors (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  website text,
  created_at timestamptz not null default now()
);

create table sources (
  id uuid primary key default gen_random_uuid(),
  competitor_id uuid not null references competitors(id) on delete cascade,
  source_type source_type not null,
  url text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (competitor_id, url)
);

create table snapshots (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references sources(id) on delete cascade,
  content text not null,
  content_hash text not null,
  fetched_at timestamptz not null default now()
);
```

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "create_core_tables"`, and the SQL above.

- [ ] **Step 3: Verify structure**

Call `list_tables` with `project_id`, `schemas: ["public"]`, `verbose: true`. Expected: `competitors`, `sources`, `snapshots` present, with `sources.competitor_id` and `snapshots.source_id` shown as foreign keys.

- [ ] **Step 4: Verify the unique constraint**

Call `execute_sql` with `project_id`:

```sql
insert into competitors (name, website) values ('Acme Corp', 'https://acme.example') returning id;
```

Note the returned `id`, then:

```sql
insert into sources (competitor_id, source_type, url)
values ('<id-from-above>', 'pricing_page', 'https://acme.example/pricing');

insert into sources (competitor_id, source_type, url)
values ('<id-from-above>', 'pricing_page', 'https://acme.example/pricing');
```

Expected: the first insert succeeds, the second fails with a unique constraint violation on `sources_competitor_id_url_key`. Clean up afterward:

```sql
delete from competitors where name = 'Acme Corp';
```

(Cascades to the surviving `sources` row — cascade behavior gets its full test in Task 8, this is just confirming the constraint fires.)

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0003_core_tables.sql
git commit -m "Add competitors, sources, snapshots tables"
```

---

### Task 5: Create signal and insight tables

**Files:**
- Create: `supabase/migrations/0004_signal_insight_tables.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1; `snapshots(id)` and `competitors(id)` from Task 4; `signal_classification`, `insight_confidence`, `insight_status`, `feedback_rating` enums from Task 3.
- Produces: `signals(id)`, `insights(id)`, `insight_signals`, `feedback(id)` — the full chain the correlation and scoring sub-projects will write to.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0004_signal_insight_tables.sql

create table signals (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid not null unique references snapshots(id) on delete cascade,
  prior_snapshot_id uuid references snapshots(id),
  diff_text text,
  classification signal_classification not null,
  theme text,
  summary text,
  embedding vector(1024),
  created_at timestamptz not null default now()
);

create table insights (
  id uuid primary key default gen_random_uuid(),
  competitor_id uuid not null references competitors(id) on delete cascade,
  materiality_score int not null check (materiality_score between 1 and 10),
  confidence insight_confidence not null,
  rationale text not null,
  status insight_status not null default 'pending',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table insight_signals (
  insight_id uuid not null references insights(id) on delete cascade,
  signal_id uuid not null references signals(id) on delete cascade,
  primary key (insight_id, signal_id)
);

create table feedback (
  id uuid primary key default gen_random_uuid(),
  insight_id uuid not null references insights(id) on delete cascade,
  rating feedback_rating not null,
  comment text,
  created_at timestamptz not null default now()
);
```

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "create_signal_insight_tables"`, and the SQL above.

- [ ] **Step 3: Verify structure**

Call `list_tables` with `project_id`, `schemas: ["public"]`, `verbose: true`. Expected: `signals`, `insights`, `insight_signals`, `feedback` present; `insight_signals` shows a composite primary key on `(insight_id, signal_id)`.

- [ ] **Step 4: Verify the materiality_score check constraint**

Call `execute_sql` with `project_id`:

```sql
insert into competitors (name) values ('Check Constraint Test Co') returning id;
```

Then, using the returned id:

```sql
insert into insights (competitor_id, materiality_score, confidence, rationale)
values ('<id-from-above>', 11, 'high', 'test');
```

Expected: fails with a check constraint violation. Then:

```sql
insert into insights (competitor_id, materiality_score, confidence, rationale)
values ('<id-from-above>', 7, 'high', 'test');
```

Expected: succeeds. Clean up:

```sql
delete from competitors where name = 'Check Constraint Test Co';
```

- [ ] **Step 5: Verify the signals.snapshot_id uniqueness constraint**

Call `execute_sql` with `project_id`:

```sql
insert into competitors (name) values ('Unique Snapshot Test Co') returning id;
```

Using the returned id, create a source and one snapshot:

```sql
insert into sources (competitor_id, source_type, url)
values ('<id-from-above>', 'changelog', 'https://unique-test.example/changelog')
returning id;
```

Using that source id:

```sql
insert into snapshots (source_id, content, content_hash)
values ('<source-id-from-above>', 'v1.0 release notes', 'hash-unique-test')
returning id;
```

Using that snapshot id, insert two signals pointing at the same snapshot:

```sql
insert into signals (snapshot_id, classification) values ('<snapshot-id-from-above>', 'material');
insert into signals (snapshot_id, classification) values ('<snapshot-id-from-above>', 'cosmetic');
```

Expected: the first insert succeeds, the second fails with a unique constraint violation on `signals_snapshot_id_key`. Clean up:

```sql
delete from competitors where name = 'Unique Snapshot Test Co';
```

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0004_signal_insight_tables.sql
git commit -m "Add signals, insights, insight_signals, feedback tables"
```

---

### Task 6: Add indexes

**Files:**
- Create: `supabase/migrations/0005_indexes.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1; all seven tables from Tasks 4 and 5.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0005_indexes.sql

create index snapshots_source_id_fetched_at_idx on snapshots (source_id, fetched_at desc);
create index snapshots_content_hash_idx on snapshots (content_hash);
create index signals_classification_idx on signals (classification);
create index signals_embedding_idx on signals using ivfflat (embedding vector_cosine_ops);
create index insights_status_idx on insights (status);
```

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "add_indexes"`, and the SQL above.

Note: `ivfflat` builds fine on an empty table (no rows yet) — it just won't be a useful index until `signals` has data. That's expected; this task only verifies it was created, not that it's tuned.

- [ ] **Step 3: Verify**

Call `execute_sql` with `project_id`:

```sql
select indexname from pg_indexes where schemaname = 'public' order by indexname;
```

Expected: all five index names above are present (plus the automatic ones Postgres creates for primary keys and unique constraints).

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0005_indexes.sql
git commit -m "Add performance indexes"
```

---

### Task 7: Enable RLS on all tables

**Files:**
- Create: `supabase/migrations/0006_rls.sql`

**Interfaces:**
- Consumes: `project_id` from Task 1; all seven tables from Tasks 4 and 5.

- [ ] **Step 1: Write the migration file**

```sql
-- supabase/migrations/0006_rls.sql

alter table competitors enable row level security;
alter table sources enable row level security;
alter table snapshots enable row level security;
alter table signals enable row level security;
alter table insights enable row level security;
alter table insight_signals enable row level security;
alter table feedback enable row level security;
```

No policies are created. This is intentional: the PoC's only clients are server-side scripts and the Streamlit app, both using the `service_role` key, which bypasses RLS entirely regardless of policies. Enabling RLS with zero policies means the `anon`/`authenticated` roles get zero access to every table — a safety net in case any code is ever accidentally pointed at these tables with the publishable/anon key instead of `service_role`.

- [ ] **Step 2: Apply it**

Call `apply_migration` with `project_id`, `name: "enable_rls"`, and the SQL above.

- [ ] **Step 3: Verify**

Call `get_advisors` with `project_id` and `type: "security"`. Expected: no "RLS disabled" findings for any of the seven tables. Note any other findings in the plan's execution notes for the user to review — don't silently dismiss them.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0006_rls.sql
git commit -m "Enable RLS on all tables"
```

---

### Task 8: End-to-end verification and README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `project_id` from Task 1; the full schema from Tasks 2-7.
- Produces: nothing further downstream — this is the plan's final integration check.

- [ ] **Step 1: Seed one full chain of rows**

Call `execute_sql` with `project_id`:

```sql
with c as (
  insert into competitors (name, website) values ('E2E Test Co', 'https://e2e.example') returning id
), s as (
  insert into sources (competitor_id, source_type, url)
  select id, 'pricing_page', 'https://e2e.example/pricing' from c
  returning id, competitor_id
), snap as (
  insert into snapshots (source_id, content, content_hash)
  select id, 'Starter plan: $49/mo', 'hash1' from s
  returning id
), sig as (
  insert into signals (snapshot_id, classification, theme, summary)
  select id, 'material', 'pricing', 'Starter plan price point captured' from snap
  returning id
), ins as (
  insert into insights (competitor_id, materiality_score, confidence, rationale)
  select competitor_id, 6, 'medium', 'Test rationale citing the snapshot above' from s
  returning id
)
insert into insight_signals (insight_id, signal_id)
select ins.id, sig.id from ins, sig;

insert into feedback (insight_id, rating)
select id, 'useful' from insights where competitor_id = (select id from competitors where name = 'E2E Test Co');
```

Expected: all statements succeed, no errors.

- [ ] **Step 2: Verify cascade delete removes the full chain**

```sql
select
  (select count(*) from competitors where name = 'E2E Test Co') as competitors,
  (select count(*) from sources where competitor_id = (select id from competitors where name = 'E2E Test Co')) as sources;
```

Note the row counts (should be 1 competitor, 1 source), then:

```sql
delete from competitors where name = 'E2E Test Co';
```

Then verify every descendant table is empty:

```sql
select
  (select count(*) from sources where url = 'https://e2e.example/pricing') as sources,
  (select count(*) from snapshots where content_hash = 'hash1') as snapshots,
  (select count(*) from signals where theme = 'pricing' and summary = 'Starter plan price point captured') as signals,
  (select count(*) from insights where rationale = 'Test rationale citing the snapshot above') as insights,
  (select count(*) from feedback where rating = 'useful' and comment is null) as feedback_rows_matching;
```

Expected: every count is `0` — confirms the `on delete cascade` chain reaches all five descendant tables (this is the Review Focus item on cascade integrity). If any count is nonzero, find the table whose foreign key is missing `on delete cascade` and fix the corresponding migration file, then re-apply as a new migration (don't edit an already-applied migration file).

- [ ] **Step 3: Update README**

Add a "Database" section to `README.md`:

```markdown
## Database

Schema lives in `supabase/migrations/`, applied in order (`0001` through `0006`). Project: `competitive-intelligence-sentinel` (Supabase, `us-east-1`).

Connect with the `service_role` key (never the anon/publishable key — every table has RLS enabled with no policies, so the anon/authenticated roles see zero rows by design). Copy `.env.example` to `.env` and fill in `SUPABASE_URL` (not secret) and `SUPABASE_SERVICE_ROLE_KEY` (from the Supabase dashboard → Project Settings → API; never commit this file).
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document database setup"
git push
```
