-- supabase/migrations/0005_indexes.sql

create index snapshots_source_id_fetched_at_idx on snapshots (source_id, fetched_at desc);
create index snapshots_content_hash_idx on snapshots (content_hash);
create index signals_classification_idx on signals (classification);
create index signals_embedding_idx on signals using ivfflat (embedding vector_cosine_ops);
create index insights_status_idx on insights (status);
