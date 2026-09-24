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
