-- supabase/migrations/0009_insight_signals_unique_signal.sql
-- Review finding: get_material_signals and get_signal_ids_with_insight are
-- both unbounded queries; if either is ever truncated by PostgREST's row
-- cap, the dedup check in score.py could silently miss an already-scored
-- signal and re-score it, creating a duplicate insights row with no error.
-- This constraint turns that into a loud, debuggable failure instead of a
-- silent duplicate. Sub-project 4's design has every insight link to
-- exactly one signal (correlation across multiple signals is a later
-- sub-project's job) — this constraint matches that current design and
-- would need to be dropped if a future sub-project makes one signal
-- legitimately belong to more than one insight.

alter table insight_signals add constraint insight_signals_signal_id_key unique (signal_id);
