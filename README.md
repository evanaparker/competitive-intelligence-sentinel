# Competitive Intelligence Sentinel

Tracks material changes in your competitor's online presence.

PoC for the PM Agentic AI capstone (Cohort 10). Monitors public competitor sources (pricing, changelog, G2, job boards) and generates materiality-scored insights citing supporting evidence.

## Components

1. Source Ingestion
2. Change Detection
3. Signal Classification
4. Cross-Source Correlation
5. Materiality Scoring & Rationale Generation
6. Human Feedback Loop
7. Delivery & Distribution

## Database

Schema lives in `supabase/migrations/`, applied in order (`0001` through `0008`). Project: `competitive-intelligence-sentinel` (Supabase, `us-east-1`). All migrations were applied directly to the live project (there is no local Supabase CLI/Docker setup in this environment) — the files in this repo are the versioned record of what's live, not a queue waiting to be run.

**These migrations are not idempotent and are already applied.** `create type`, `create table`, and most `create index` statements have no `if not exists` guard (Postgres doesn't support one for `create type`, and the others intentionally match). Do not re-run them against this project — re-running will fail on `0002` with `type "source_type" already exists`. Re-run only against a fresh, empty database. If you later run `supabase link` against this project, the CLI's local migration history will not know about `0001`–`0008` (they were applied outside the CLI) — run `supabase migration repair --status applied <each version>` before `supabase db push`, or it will try to re-apply them and fail the same way.

Connect with the `service_role` key — never the anon/publishable key, and never ship `service_role` to a browser/client context (it bypasses RLS entirely; only server-side code such as the ingestion pipeline or the Streamlit app should hold it). Every table has RLS enabled with no policies, so the anon/authenticated roles are blocked from all of them by design, but the failure mode differs by operation: a `select`/`update`/`delete` with the anon key silently affects 0 rows, while an `insert` raises a visible `new row violates row-level security policy` error. Copy `.env.example` to `.env` and fill in `SUPABASE_URL` (not secret) and `SUPABASE_SERVICE_ROLE_KEY` (from the Supabase dashboard → Project Settings → API; never commit this file).

## Running ingestion

```bash
pip3 install --target=.deps -r requirements.txt
cp .env.example .env  # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
PYTHONPATH=.deps python3 ingest.py
```

This environment has no `python3-venv` and no sudo access to install it, and `pip install --user` is blocked by PEP 668's externally-managed-environment guard — `--target=.deps` keeps everything local to the project with no system Python changes. If your environment has a working `python3 -m venv`, that's fine too — use `.venv/bin/pip install -r requirements.txt` and `.venv/bin/python ingest.py` instead. Either way, exit code is `1` if any source errored, `0` otherwise.

Fetches every active source in `sources`, stores a new `snapshots` row every run (whether or not the content changed), and prints one line per source: `SAME`, `CHANGED`, or `ERROR`.

Currently tracks one source, seeded directly via SQL (no watchlist UI yet):

```sql
insert into competitors (name, website) values ('Sonar', 'https://sonar.software') returning id;
insert into sources (competitor_id, source_type, url)
values ('<id-from-above>', 'pricing_page', 'https://sonar.software/pricing');
```

### Running tests

```bash
PYTHONPATH=.deps python3 -m pytest tests/ -v
```

`hashing.py`, `extract.py`, and `fetch.py` are covered by local unit tests (no network or database). `ingest.py`'s orchestration logic (`tests/test_ingest.py`) is covered with the DB/network calls monkeypatched. `db.py`'s three data-access functions have no local test — they're verified by actually running against the live Supabase project (see the implementation plan's Task 5 for how).
