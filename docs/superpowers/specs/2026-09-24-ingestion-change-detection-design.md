# Ingestion + Change Detection Design (PoC)

Status: Approved
Date: 2026-09-24
Scope: Sub-project 2 of the Competitive Intelligence Sentinel PoC — fetches a competitor source, detects whether it changed since the last check, and records the result. Covers PRD pipeline stages 1 (Source Ingestion) and 2 (Change Detection) only.

## Context

This builds directly on the data model from sub-project 1 ([2026-09-23-data-model-design.md](2026-09-23-data-model-design.md)): `competitors`, `sources`, `snapshots`. It does not touch `signals`, `insights`, `insight_signals`, or `feedback` — those belong to the next sub-project (Signal Classification), which will read snapshot pairs whose `content_hash` differs and do the LLM classification + diffing that produces a `signals` row.

## Goals

- Fetch one real, live competitor source and store what changed, end to end, provably working — not a stub.
- Seed the one competitor/source this PoC increment tracks: **Test Competitor** (`https://test-competitor.example/pricing`, `source_type = pricing_page`) — confirmed to be server-rendered (pricing figures present in the raw HTML with no JavaScript execution needed), so a plain HTTP GET is sufficient; no headless browser required.
- Every successful fetch inserts a `snapshots` row, whether or not the content changed (per the data model spec, a repeated `content_hash` is expected and is the "still being watched, nothing new" signal, not an error).
- One failing source must not stop the others from being checked (forward-looking: this PoC will eventually track more than one source).
- Small, independently testable units: pure functions get real unit tests; the two units that must talk to the network or the database (`fetch`, `db`) are the only ones that don't.

## Non-goals (explicitly out of scope for this sub-project)

- Signal classification, diffing into `diff_text`, or anything writing to `signals` — next sub-project.
- A general "add a competitor/source" UI or CLI — the one Test Competitor source is seeded directly via SQL, the same way sub-project 1 seeded test fixtures.
- Scheduling (GitHub Actions cron) — this runs as a local script for now, per this session's decision to prove the logic before wiring automation around it.
- Headless-browser fetching (Playwright) — not needed for this source; if a future source turns out to be client-rendered, that's a new `fetch` implementation to add then, not now.
- Retry/backoff logic on fetch failure — a failed fetch is recorded and skipped this run; it gets picked up again on the next manual run.

## Architecture

```
sources (Supabase, seeded with 1 row: Test Competitor / pricing_page)
   │
   ▼
fetch.py     — GET the source URL (httpx, 15s timeout), return raw HTML
   │
   ▼
extract.py   — raw HTML → normalized visible text (BeautifulSoup, script/style stripped, whitespace collapsed)
   │
   ▼
hashing.py   — sha256 hex digest of the normalized text
   │
   ▼
db.py        — read the active sources; read the latest snapshot's hash for a source; insert a new snapshot row
   │
   ▼
ingest.py    — orchestrates the above per active source, catching errors per-source, and prints a summary
```

Run with `python ingest.py` from the repo root (after `pip install -r requirements.txt` and populating `.env` from `.env.example`, per the existing README).

## Module Interfaces

```python
# fetch.py
def fetch(url: str) -> str:
    """GET url with a 15s timeout. Returns the raw response text.
    Raises httpx.HTTPError (network failure) or httpx.HTTPStatusError
    (non-2xx status, via response.raise_for_status())."""

# extract.py
def extract_text(html: str) -> str:
    """Parse html with BeautifulSoup. Remove <script> and <style>
    elements. Return get_text() with whitespace collapsed to single
    spaces and the result stripped."""

# hashing.py
def compute_hash(text: str) -> str:
    """Return the sha256 hex digest of text (UTF-8 encoded)."""

# db.py
def get_active_sources(client) -> list[dict]:
    """Return rows from `sources` where is_active is true: id, url,
    competitor_id, source_type."""

def get_latest_snapshot_hash(client, source_id: str) -> str | None:
    """Return content_hash of the most recent snapshot (by fetched_at)
    for source_id, or None if this source has no snapshots yet."""

def insert_snapshot(client, source_id: str, content: str, content_hash: str) -> dict:
    """Insert a row into `snapshots` and return it. Always called after
    a successful fetch, regardless of whether the hash changed."""

# ingest.py
def run(client) -> list[dict]:
    """For each active source: fetch -> extract_text -> compute_hash ->
    compare against get_latest_snapshot_hash -> insert_snapshot.
    Catches any exception per-source (does not let one bad fetch stop
    the rest) and returns a list of
    {"url": str, "changed": bool, "error": str | None} — exactly one
    entry per active source, in the order returned by
    get_active_sources. "changed" is True when there was no prior
    snapshot (first check) or the hash differs from it; False when it
    matches. When "error" is set, "changed" is False and no snapshot
    was inserted for that source.

if __name__ == "__main__":
    ...  # load .env, build the Supabase client, call run(), print one
         # line per result
"""
```

## Error Handling

- **Fetch fails** (timeout, network error, non-2xx status): caught per-source inside `run()`. Recorded as `{"url": ..., "changed": False, "error": "<message>"}`. No snapshot is inserted for that source. Other sources still get checked.
- **Fetch succeeds, content unchanged**: a snapshot row is still inserted (the audit-trail behavior the data model spec calls out — a repeated hash is expected, not skipped). Recorded as `changed: False`, `error: None`.
- **Fetch succeeds, first time this source has ever been checked**: `get_latest_snapshot_hash` returns `None`, which is always treated as "changed" (there is nothing to compare against).
- **Missing `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`**: this is a precondition failure for the whole script, not a per-source error — raise immediately with a clear message before attempting any source, rather than letting every source fail individually with a confusing DB connection error.

## Data Seeding

Before `ingest.py` can do anything, `sources` needs the one row it will check:

```sql
insert into competitors (name, website) values ('Test Competitor', 'https://test-competitor.example') returning id;
insert into sources (competitor_id, source_type, url) values ('<id-from-above>', 'pricing_page', 'https://test-competitor.example/pricing');
```

This is a one-time setup step (applied directly via the Supabase MCP tools, the same way sub-project 1 seeded its test fixtures), not a feature of the ingestion code itself.

## Dependencies

New: `httpx` (fetch), `beautifulsoup4` (extraction), `supabase` (the official Python client, using the `service_role` key), `python-dotenv` (load `.env`), `pytest` (dev dependency, for `extract.py`/`hashing.py`/`fetch.py` unit tests). Recorded in a new `requirements.txt` at the repo root.

## Testing

- `extract.py`: unit tests with fixed HTML fixtures — confirms script/style stripped, whitespace collapsed, plain text returned.
- `hashing.py`: unit tests — same input always produces the same hash; different input produces a different hash.
- `fetch.py`: unit tests using `httpx.MockTransport` — no real network calls; covers a 200 response and a non-2xx response (expect `HTTPStatusError`).
- `db.py` and `ingest.py`: no local test harness exists in this repo for a live Postgres/Supabase dependency (consistent with sub-project 1); verified by running against the real, already-live Supabase project (`wbjptxjrujyzmsjldwwo`) as part of the implementation plan's own task verification — first run against the freshly-seeded Test Competitor source (expect `changed: True`, one new snapshot), second run immediately after (expect `changed: False`, a second snapshot with the same hash).
