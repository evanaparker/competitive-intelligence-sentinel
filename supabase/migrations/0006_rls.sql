-- supabase/migrations/0006_rls.sql

alter table competitors enable row level security;
alter table sources enable row level security;
alter table snapshots enable row level security;
alter table signals enable row level security;
alter table insights enable row level security;
alter table insight_signals enable row level security;
alter table feedback enable row level security;
