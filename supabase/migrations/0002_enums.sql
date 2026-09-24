-- supabase/migrations/0002_enums.sql
create type source_type as enum ('pricing_page', 'changelog', 'g2', 'app_store', 'job_board', 'filing', 'other');
create type signal_classification as enum ('cosmetic', 'material');
create type insight_confidence as enum ('high', 'medium', 'low', 'needs_review');
create type insight_status as enum ('pending', 'approved', 'rejected', 'published');
create type feedback_rating as enum ('useful', 'not_useful', 'incorrect');
