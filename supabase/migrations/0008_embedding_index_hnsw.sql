-- supabase/migrations/0008_embedding_index_hnsw.sql
-- ivfflat trains its centroids from the rows present at build time; built
-- against an empty signals table (as this PoC always will be, on first
-- deploy) it has no meaningful centroids and silently returns low-recall
-- results once data arrives. HNSW needs no training data and is correct
-- to build on an empty table.

drop index signals_embedding_idx;
create index signals_embedding_idx on signals using hnsw (embedding vector_cosine_ops);
