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
