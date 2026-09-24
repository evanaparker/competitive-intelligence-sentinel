-- supabase/migrations/0007_prior_snapshot_set_null.sql
-- signals.prior_snapshot_id had no ON DELETE action, which blocked deleting
-- individual snapshots (e.g. a retention/pruning job) once any signal
-- referenced them as their prior snapshot. It is nullable and only used to
-- show what changed against, so losing that reference on delete is correct.

alter table signals drop constraint signals_prior_snapshot_id_fkey;
alter table signals add constraint signals_prior_snapshot_id_fkey
  foreign key (prior_snapshot_id) references snapshots(id) on delete set null;
