# Data Model Design (PoC)

Status: Approved
Date: 2026-09-23
Scope: Sub-project 1 of the Competitive Intelligence Sentinel PoC — the Supabase (Postgres) schema that every other sub-project (ingestion, classification, correlation, scoring, feedback UI, delivery) reads and writes.

## Context

Competitive Intelligence Sentinel is a proof-of-concept for the PM Agentic AI capstone (Cohort 10): an agent that monitors public competitor sources (pricing pages, changelogs, G2, App Store, job boards, filings), detects material changes, classifies and correlates them into insights, scores materiality with a cited rationale, and routes them through a human approval step before delivery.

This spec covers only the data model. It is the foundation the rest of the PoC builds on.

## Goals

- Support the full pipeline end-to-end: ingestion → change detection → classification → cross-source correlation → materiality scoring → human feedback → delivery status tracking.
- Keep the schema simple enough to implement and query by hand for a PoC — no premature normalization or fields that no planned sub-project will use.
- Every insight must be traceable back to the exact evidence (snapshot content + timestamp) it was derived from.

## Non-goals (explicitly out of scope for this PoC)

- Multi-user auth / reviewer identity — single-user (Evan) only. `feedback` rows are not attributed to a specific reviewer.
- Raw snapshot storage in object storage (e.g., Supabase Storage) — content is stored directly in a Postgres `text` column given the PoC's small data volume.
- Scheduling metadata (cron expressions, retry state) — that belongs to the GitHub Actions workflow, not the data model.
- Versioning/audit history of insight edits beyond `status` and `updated_at`.

## Entities & Relationships

```
competitors ──< sources ──< snapshots ──< signals >──< insight_signals >── insights ──< feedback
```

- `competitors` — a tracked competitor.
- `sources` — a monitored source belonging to a competitor (pricing page, changelog, G2, job board, etc.).
- `snapshots` — a point-in-time capture of a source's content.
- `signals` — a non-trivial diff between two consecutive snapshots of the same source, classified and embedded.
- `insights` — a materiality-scored, cited insight, built from one or more correlated signals (many-to-many via `insight_signals`).
- `feedback` — useful / not useful / incorrect rating on an insight.

This maps directly to the first six pipeline stages from the PRD. The seventh stage (Delivery & Distribution) is tracked via status/timestamp columns on `insights` rather than new tables (see "Mapping to Pipeline Stages" below).

## Schema Detail

### Extension required

```sql
create extension if not exists vector; -- pgvector, for signals.embedding
```

### Enums

```sql
create type source_type as enum ('pricing_page', 'changelog', 'g2', 'app_store', 'job_board', 'filing', 'other');
create type signal_classification as enum ('cosmetic', 'material');
create type insight_confidence as enum ('high', 'medium', 'low', 'needs_review');
create type insight_status as enum ('pending', 'approved', 'rejected', 'published');
create type feedback_rating as enum ('useful', 'not_useful', 'incorrect');
```

### `competitors`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK, default `gen_random_uuid()` |
| name | text | unique, not null |
| website | text | |
| created_at | timestamptz | default `now()` |

### `sources`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| competitor_id | uuid | FK → competitors, `on delete cascade` |
| source_type | source_type | not null |
| url | text | not null |
| is_active | boolean | default true |
| created_at | timestamptz | default `now()` |

Constraint: `unique (competitor_id, url)`.

### `snapshots`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| source_id | uuid | FK → sources, `on delete cascade` |
| content | text | raw fetched content |
| content_hash | text | hash of normalized content, not null |
| fetched_at | timestamptz | default `now()` |

### `signals`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| snapshot_id | uuid | FK → snapshots, `on delete cascade`, **unique** (one signal per snapshot) |
| prior_snapshot_id | uuid | FK → snapshots, nullable, `on delete set null` (deleting the referenced snapshot clears this pointer rather than blocking the delete) |
| diff_text | text | the extracted diff |
| classification | signal_classification | not null |
| theme | text | e.g. "pricing", "roadmap", "hiring" |
| summary | text | LLM-generated summary of the change |
| embedding | vector(1024) | Voyage-3.5 embedding, nullable until computed |
| created_at | timestamptz | default `now()` |

### `insights`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| competitor_id | uuid | FK → competitors, `on delete cascade` |
| materiality_score | int | 1–10, `check (materiality_score between 1 and 10)` |
| confidence | insight_confidence | not null |
| rationale | text | includes citations to source evidence |
| status | insight_status | default `pending` |
| created_at | timestamptz | default `now()` |
| updated_at | timestamptz | default `now()` |

### `insight_signals` (junction table)

| Column | Type | Notes |
|---|---|---|
| insight_id | uuid | FK → insights, `on delete cascade` |
| signal_id | uuid | FK → signals, `on delete cascade` |

Composite PK: `(insight_id, signal_id)`.

### `feedback`

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| insight_id | uuid | FK → insights, `on delete cascade` |
| rating | feedback_rating | not null |
| comment | text | nullable |
| created_at | timestamptz | default `now()` |

## Indexes

```sql
create index on snapshots (source_id, fetched_at desc);
create index on snapshots (content_hash);
create index on signals (classification);
create index on signals using hnsw (embedding vector_cosine_ops);
create index on insights (status);
```

## Mapping to Pipeline Stages

| PRD stage | Data model support |
|---|---|
| 1. Source Ingestion | `sources`, `snapshots` |
| 2. Change Detection | `snapshots.content_hash` (skip diffing when hash is unchanged from the source's latest snapshot) |
| 3. Signal Classification | `signals.classification`, `signals.summary` |
| 4. Cross-Source Correlation | `signals.embedding` (similarity search) + `insight_signals` (grouping into one insight) |
| 5. Materiality Scoring & Rationale | `insights.materiality_score`, `insights.rationale`, `insights.confidence` |
| 6. Human Feedback Loop | `feedback`, `insights.status` |
| 7. Delivery & Distribution | `insights.status = 'published'` marks a battlecard/CRM push; no dedicated table — a later sub-project may add `insights.published_at` / `insights.alert_sent_at` if the delivery design needs it |

## Testing

Schema correctness will be validated by:
- Applying the migration to a fresh Supabase project and confirming it runs without error.
- Seeding one competitor/source/snapshot/signal/insight/feedback row manually and confirming all foreign keys and constraints hold (e.g., duplicate `(competitor_id, url)` on `sources` is rejected, duplicate `snapshot_id` on `signals` is rejected).
- No automated test suite is planned for the schema itself in this PoC; correctness is exercised indirectly by the ingestion and scoring sub-projects once implemented.
