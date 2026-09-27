-- supabase/migrations/0010_signal_correlation.sql
-- Cross-source correlation (sub-project 6): signals that describe the same
-- underlying business story share a correlation_group_id, so score.py can
-- create one insight per group instead of one per signal. NULL means
-- correlate.py has not yet decided this signal's correlation status — see
-- the design spec's Error Handling for why NULL must mean exactly that and
-- nothing else (idempotency depends on it).

alter table signals add column correlation_group_id uuid;
