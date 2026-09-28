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

## Running classification

```bash
PYTHONPATH=.deps python3 classify.py
```

For each active source, classifies the diff between its newest snapshot and the last snapshot that was already classified (not just the immediately-prior one — it walks back through any snapshots `ingest.py` inserted since, so a change is never lost just because this script didn't run between two `ingest.py` runs) as `cosmetic` or `material` (via GPT-5.4 Mini) and writes it to `signals`. Prints one line per source: `CLASSIFIED <url>: <classification>`, `SKIPPED <url> (<reason>)` where reason is `insufficient_history`, `already_classified`, or `unchanged`, or `ERROR <url>: <message>`. Exit code is `1` if any source errored, `0` otherwise. Needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` in `.env` alongside the Supabase credentials.

## Running correlation

```bash
PYTHONPATH=.deps python3 correlate.py
```

For each competitor, groups `material` signals with no insight yet into candidate clusters using a fixed 7-day time window from the earliest ungrouped signal, then — for any cluster of 2+ — asks `gpt-5.4-mini` a binary question: do these signals describe the same underlying business story? If yes, they share a `correlation_group_id` so `score.py` creates one insight for the group instead of one per signal. If no, each signal is marked decided with its own id (so it is never reconsidered) and gets scored on its own. A signal alone in its cluster is *not* decided immediately — if fewer than 7 days have passed since it was detected, a same-source or cross-source companion could still show up, so it's left undecided and picked up again on a later run; only once the window has fully closed with no companion does it get self-assigned and scored alone. Prints one line per cluster: `GROUPED <competitor>: N signals (<group_id>)`, `UNGROUPED <competitor>: N signals (not correlated)`, `SINGLETON <competitor>: 1 signal`, `PENDING <competitor>: 1 signal (window still open)`, or `ERROR <competitor>: <message>`. Exit code is `1` if any cluster errored, `0` otherwise. Needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` in `.env` (same as `classify.py`/`score.py`).

Run this before `score.py` — it's what lets `score.py` bundle related signals into one insight instead of scoring each in isolation.

## Running materiality scoring

```bash
PYTHONPATH=.deps python3 score.py
```

Groups `material` signals with no insight yet by `correlation_group_id` (set by `correlate.py` — a signal with no group of its own, e.g. because `correlate.py` was never run, is scored alone), and for each group scores it 1-10 for materiality (via `gpt-5.1`), assigns a confidence label (`high`/`medium`/`low`/`needs_review`), writes a citation-grounded rationale covering every signal in the group, and creates one `insights` row (`status` stays at its default `pending` — nothing here approves or publishes) linked to every signal in the group via `insight_signals`. Prints one line per scored signal: `SCORED <signal-id>: <score>`, or `ERROR <signal-id>: <message>`. Prints nothing and exits 0 if there's nothing pending. Needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` in `.env` (same as `classify.py`).

`insight_signals.signal_id` is `unique` — each signal can only ever be linked to one insight. If a run creates an `insights` row but then fails to link it (a real but rare race), the error names the orphaned insight's id; it is not deleted automatically (see the spec's Known Limitations).

## Running the review app

```bash
PYTHONPATH=.deps python3 -m streamlit run review.py --server.port 8501 --server.headless true --global.developmentMode false
```

Shows every `pending` insight (highest materiality first) with its competitor, score, confidence, rationale, and the raw evidence (the underlying signal's diff) it's based on. Approve or Reject moves the insight out of the queue; an optional feedback rating (`useful`/`not_useful`/`incorrect`) and comment can be left alongside either decision — feedback is not required and a failure saving it is reported separately from the approve/reject decision itself, which still stands.

`--global.developmentMode false` is required with the `--target=.deps` install described above: without a `site-packages` directory in its module path, Streamlit assumes it's running from its own source checkout and switches into a mode that refuses a fixed `--server.port`. If your environment installs Streamlit normally (e.g. into a venv), this flag is unnecessary but harmless.

## Deployment

The pipeline (`ingest.py` → `classify.py` → `correlate.py` → `score.py`) runs automatically once daily (06:00 UTC) on an Azure Function App (`cisentinel-pipeline`, resource group `cisentinel-deploy-rg`), via `function_app.py`'s `daily_pipeline` — the exact same `run()`/`format_line()` functions each script's own `__main__` block calls, wrapped in a Timer Trigger that stops at the first stage reporting an error rather than continuing to the next. A failed run triggers an Azure Monitor alert (`cisentinel-pipeline-failure-alert`) linked to an Action Group with an email notification (delivery of that email was not confirmed live — see the plan's ledger — the alert firing itself was confirmed via both the Azure Portal and the Alerts Management API).

The review app is hosted on **Streamlit Community Cloud** (deployed from this repo's `main` branch, `review.py`) at [`competitive-intelligence-sentinel-ub9p9fhuskw6pkmtdgjb4a.streamlit.app`](https://competitive-intelligence-sentinel-ub9p9fhuskw6pkmtdgjb4a.streamlit.app), set to private ("Only specific people can view this app") — only explicitly invited viewers can reach it. Chosen over Azure App Service because this subscription's App Service F1 (Free) tier has a quota of 0, and the paid B1 tier was declined to avoid ongoing cost.

Redeploying after a code change:

```bash
# Pipeline
func azure functionapp publish cisentinel-pipeline
```

The review app redeploys itself automatically on every push to `main` (Streamlit Community Cloud watches the connected GitHub repo) — no manual redeploy command for it. The pipeline's redeploy command runs from the repo root and requires the Azure CLI and Azure Functions Core Tools already authenticated via `az login`. Neither this nor the review app's auto-redeploy is CI/CD in the tested/gated sense — there's no test run before either goes live, matching this PoC's single-operator, infrequent-deploy scale.

Not deployed anywhere yet — runs locally, same as every other script in this repo so far.

### Running tests

```bash
PYTHONPATH=.deps python3 -m pytest tests/ -v
```

Pure-logic modules (`hashing.py`, `extract.py`, `fetch.py`, `diffing.py`, `llm.py`, `classifier.py`, `scorer.py`) and the orchestrators' own decision logic (`ingest.py`, `classify.py`, `score.py`, each in their own `tests/test_*.py`) are covered by local unit tests with the DB/LLM calls monkeypatched — no network or database needed to run the suite. `db.py`'s data-access functions (other than `get_client`) have no local test — they're verified by actually running against the live Supabase project as part of each sub-project's implementation plan.
