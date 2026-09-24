# Ingestion + Change Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch the real Sonar pricing page, detect whether it changed since the last check, and record the result in Supabase — end to end, provably working against the live project.

**Architecture:** Five small Python modules at the repo root (`hashing.py`, `extract.py`, `fetch.py`, `db.py`, `ingest.py`), each with one responsibility. The three pure/mockable modules (`hashing`, `extract`, `fetch`) get real TDD with `pytest`. `db.py` and the `ingest.py` orchestrator have no local test harness for a live Postgres dependency (same situation as sub-project 1), so they are verified by running against the real, already-live Supabase project (`wbjptxjrujyzmsjldwwo`) — except `db.get_client()`'s fail-fast precondition check, which needs no network and gets a real pytest test.

**Tech Stack:** Python 3, `httpx`, `beautifulsoup4`, `supabase` (official client), `python-dotenv`, `pytest`. Supabase MCP tools for the one-time data seed.

**Spec:** [docs/superpowers/specs/2026-09-24-ingestion-change-detection-design.md](../specs/2026-09-24-ingestion-change-detection-design.md)

## Global Constraints

- `fetch()` uses `httpx`, a 15s timeout, and calls `response.raise_for_status()` so non-2xx responses raise.
- `extract_text()` uses BeautifulSoup, strips `<script>` and `<style>` tags entirely, collapses whitespace to single spaces, and strips the result.
- `compute_hash()` returns the sha256 hex digest of the UTF-8-encoded text.
- `db.py` uses the official `supabase` Python client with the `service_role` key (never anon/publishable).
- A `snapshots` row is inserted on every successful fetch, whether or not the content changed — a repeated `content_hash` is expected, not an error.
- `get_latest_snapshot_hash` returning `None` (no prior snapshot for this source) is always treated as "changed."
- `ingest.run()` catches errors per-source so one bad fetch doesn't stop the others; missing `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` fails immediately, before any source is attempted — that is a precondition of the whole run, not a per-source error.
- All five modules live at the repo root (not a package) and the script runs as `python ingest.py`, per the spec.
- Seed data: competitor **Sonar** (`https://sonar.software`), source `pricing_page` at `https://sonar.software/pricing`, in Supabase project `wbjptxjrujyzmsjldwwo`.

## Review Focus

- A connection-level failure (DNS failure, connection refused — not just an HTTP error status) for one source must not crash `run()` for the others. → tested in Task 6 with a real unreachable URL.
- Running `ingest.py` twice against an unchanged live page must report `changed: False` both times; a genuine difference must be reported as `changed: True`. → tested in Task 6 by injecting a deliberately different stored hash to force a detectable "change," then re-running to confirm it settles back to unchanged.
- Missing `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` must fail immediately with a clear message before any source is attempted, not surface as a confusing per-source DB error. → tested in Task 5.
- Script/style-embedded JSON (Next.js `__NEXT_DATA__`-style blobs, common on modern marketing sites) must never leak into the extracted text — tested against the real Sonar HTML, not only a synthetic fixture, since a synthetic fixture can't prove real-world script tags are actually being stripped. → tested in Task 3.
- The `content_hash` stored for a snapshot must always equal `compute_hash()` of that same snapshot's stored `content` — the structural assumption every future comparison depends on. → tested in Task 6 by reading a real inserted row back from the database.

---

## File Structure

- Create: `requirements.txt` — pinned dependencies.
- Create: `pytest.ini` — `pythonpath = .` so root-level modules import cleanly from `tests/`.
- Create: `hashing.py` / `tests/test_hashing.py`
- Create: `extract.py` / `tests/test_extract.py` / `tests/fixtures/sonar_pricing.html` (real captured HTML, used as a test fixture)
- Create: `fetch.py` / `tests/test_fetch.py`
- Create: `db.py` / `tests/test_db.py` (only `get_client()`'s precondition check is unit-tested; the rest is live-verified in Task 5)
- Create: `ingest.py` (live-verified in Task 6, no local test)
- Modify: `README.md` — add a "Running ingestion" section.

---

### Task 1: Environment setup and data seed

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`

**Interfaces:**
- Produces: a working `.venv` with all dependencies installed; the seeded `sources` row every later task's live verification reads (`source_type = 'pricing_page'`, `url = 'https://sonar.software/pricing'`).

- [ ] **Step 1: Write `requirements.txt`**

```
httpx==0.27.2
beautifulsoup4==4.12.3
supabase==2.9.0
python-dotenv==1.0.1
pytest==8.3.3
```

- [ ] **Step 2: Write `pytest.ini`**

```ini
[pytest]
pythonpath = .
```

- [ ] **Step 3: Create the virtualenv and install dependencies**

```bash
cd /home/sdn/Desktop/competitive-intelligence-sentinel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Expected: installs without error. Verify with `.venv/bin/pip freeze | grep -i httpx` — expect a line starting `httpx==0.27.2`.

- [ ] **Step 4: Seed the Sonar competitor and source**

Call the Supabase MCP `execute_sql` tool with `project_id: "wbjptxjrujyzmsjldwwo"`:

```sql
insert into competitors (name, website) values ('Sonar', 'https://sonar.software') returning id;
```

Using the returned `id`:

```sql
insert into sources (competitor_id, source_type, url)
values ('<id-from-above>', 'pricing_page', 'https://sonar.software/pricing')
returning id;
```

Note this second returned `id` too — it's the `source_id` every later live-verification task queries against.

- [ ] **Step 5: Verify the seed**

Call `execute_sql`:

```sql
select c.name, s.source_type, s.url, s.is_active
from sources s join competitors c on c.id = s.competitor_id
where c.name = 'Sonar';
```

Expected: one row — `Sonar`, `pricing_page`, `https://sonar.software/pricing`, `true`.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini
git commit -m "Add Python dependencies and pytest config"
```

(`.venv/` is already covered by `.gitignore` from sub-project 1 — do not add it.)

---

### Task 2: `hashing.py`

**Files:**
- Create: `hashing.py`
- Test: `tests/test_hashing.py`

**Interfaces:**
- Produces: `compute_hash(text: str) -> str` — used by `extract`'s consumers in Task 6 (`ingest.py`) and directly by Task 5/6 live verification.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hashing.py
import hashlib

from hashing import compute_hash


def test_same_input_produces_same_hash():
    assert compute_hash("hello world") == compute_hash("hello world")


def test_different_input_produces_different_hash():
    assert compute_hash("hello world") != compute_hash("hello there")


def test_returns_sha256_hex_digest():
    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert compute_hash("hello world") == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_hashing.py -v`
Expected: `ModuleNotFoundError: No module named 'hashing'` (or collection error) — `hashing.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# hashing.py
import hashlib


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_hashing.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add hashing.py tests/test_hashing.py
git commit -m "Add hashing module"
```

---

### Task 3: `extract.py`

**Files:**
- Create: `extract.py`
- Test: `tests/test_extract.py`
- Create: `tests/fixtures/sonar_pricing.html` (real captured HTML)

**Interfaces:**
- Produces: `extract_text(html: str) -> str` — used by `ingest.py` in Task 6.

- [ ] **Step 1: Capture the real Sonar HTML fixture**

```bash
mkdir -p tests/fixtures
curl -s -A "Mozilla/5.0" https://sonar.software/pricing -o tests/fixtures/sonar_pricing.html
```

Verify: `grep -c "__NEXT_DATA__\|1.25" tests/fixtures/sonar_pricing.html` — expect a nonzero count (confirms the fixture actually contains both a script blob and the real pricing figure, which Step 1's test below depends on).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_extract.py
from pathlib import Path

from extract import extract_text

FIXTURE = Path(__file__).parent / "fixtures" / "sonar_pricing.html"


def test_strips_script_and_style_tags():
    html = """
    <html><head><style>body { color: red; }</style></head>
    <body>
      <script>window.__NEXT_DATA__ = {"price": 999};</script>
      <p>Hello   world</p>
    </body></html>
    """
    result = extract_text(html)
    assert "999" not in result
    assert "color: red" not in result
    assert "Hello world" in result


def test_collapses_whitespace():
    html = "<p>Line one</p>\n\n<p>   Line   two   </p>"
    assert extract_text(html) == "Line one Line two"


def test_strips_leading_and_trailing_whitespace():
    html = "  <p>  padded  </p>  "
    result = extract_text(html)
    assert result == result.strip()


def test_real_sonar_pricing_page_has_no_script_json_leakage():
    html = FIXTURE.read_text()
    result = extract_text(html)
    assert "__NEXT_DATA__" not in result
    assert '"props":' not in result
    assert "1.25" in result
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_extract.py -v`
Expected: collection error — `extract.py` doesn't exist yet (and `bs4` isn't imported anywhere yet either, though it's already installed from Task 1).

- [ ] **Step 4: Write the implementation**

```python
# extract.py
import re

from bs4 import BeautifulSoup


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_extract.py -v`
Expected: `4 passed`. If `test_real_sonar_pricing_page_has_no_script_json_leakage` fails on `"1.25" not in result`, Sonar's pricing may have changed since Step 1's fixture was captured, or the page structure differs from what this plan assumed — re-check the fixture content before changing the extraction logic (per Review Focus, this test exists specifically to catch script-leakage; don't weaken the assertion to make it pass).

- [ ] **Step 6: Commit**

```bash
git add extract.py tests/test_extract.py tests/fixtures/sonar_pricing.html
git commit -m "Add HTML text extraction module"
```

---

### Task 4: `fetch.py`

**Files:**
- Create: `fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Produces: `fetch(url: str, client: httpx.Client | None = None) -> str` — the optional `client` parameter exists purely for test injection (via `httpx.MockTransport`); normal callers (`ingest.py` in Task 6) call it as `fetch(url)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fetch.py
import httpx
import pytest

from fetch import fetch


def test_fetch_returns_response_text_on_200():
    def handler(request):
        return httpx.Response(200, text="hello world")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch("https://example.test/page", client=client) == "hello world"


def test_fetch_raises_on_500():
    def handler(request):
        return httpx.Response(500, text="server error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://example.test/page", client=client)


def test_fetch_raises_on_404():
    def handler(request):
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://example.test/page", client=client)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_fetch.py -v`
Expected: collection error — `fetch.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# fetch.py
import httpx


def fetch(url: str, client: httpx.Client | None = None) -> str:
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=15.0)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.text
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_fetch.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add fetch.py tests/test_fetch.py
git commit -m "Add HTTP fetch module"
```

---

### Task 5: `db.py`

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py` (only `get_client()`)

**Interfaces:**
- Consumes: `project_id` `wbjptxjrujyzmsjldwwo` (live verification only, not code); `extract_text` (Task 3), `fetch` (Task 4), `compute_hash` (Task 2) for the live verification's manual pipeline run.
- Produces: `get_client() -> Client`, `get_active_sources(client) -> list[dict]`, `get_latest_snapshot_hash(client, source_id) -> str | None`, `insert_snapshot(client, source_id, content, content_hash) -> dict` — all four consumed by `ingest.py` in Task 6.

- [ ] **Step 1: Write the failing test for `get_client()`**

```python
# tests/test_db.py
import pytest

from db import get_client


def test_get_client_raises_when_url_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-key")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        get_client()


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        get_client()


def test_get_client_returns_client_when_both_set(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-key")
    assert get_client() is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: collection error — `db.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# db.py
import os

from supabase import Client, create_client


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example)"
        )
    return create_client(url, key)


def get_active_sources(client: Client) -> list[dict]:
    response = (
        client.table("sources")
        .select("id, url, competitor_id, source_type")
        .eq("is_active", True)
        .execute()
    )
    return response.data


def get_latest_snapshot_hash(client: Client, source_id: str) -> str | None:
    response = (
        client.table("snapshots")
        .select("content_hash")
        .eq("source_id", source_id)
        .order("fetched_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]["content_hash"]


def insert_snapshot(client: Client, source_id: str, content: str, content_hash: str) -> dict:
    response = (
        client.table("snapshots")
        .insert({"source_id": source_id, "content": content, "content_hash": content_hash})
        .execute()
    )
    return response.data[0]
```

- [ ] **Step 4: Run to verify `get_client()` tests pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: `3 passed`. This directly proves the Review Focus item on failing fast before any source is attempted.

- [ ] **Step 5: Live-verify the remaining three functions against the real project**

Populate `.env` first if not already done (copy `.env.example` to `.env`, fill in `SUPABASE_URL` from Task 1's project and `SUPABASE_SERVICE_ROLE_KEY` from the Supabase dashboard — this is the one step in the whole plan that needs a value only the user has).

Write a scratch file `verify_db.py` at the repo root (not committed — delete it at the end of this step):

```python
# verify_db.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import get_client, get_active_sources, get_latest_snapshot_hash, insert_snapshot
from fetch import fetch
from extract import extract_text
from hashing import compute_hash

client = get_client()

sources = get_active_sources(client)
assert len(sources) == 1
assert sources[0]["url"] == "https://sonar.software/pricing"
source_id = sources[0]["id"]

assert get_latest_snapshot_hash(client, source_id) is None

html = fetch(sources[0]["url"])
text = extract_text(html)
content_hash = compute_hash(text)
row = insert_snapshot(client, source_id, text, content_hash)
assert row["content_hash"] == content_hash

assert get_latest_snapshot_hash(client, source_id) == content_hash
print("db.py live verification passed")
```

Run: `.venv/bin/python verify_db.py`
Expected: prints `db.py live verification passed` with no assertion errors. This inserts Sonar's real first snapshot — not throwaway data, leave it in place. This is also the first proof of Review Focus item 5 (stored `content_hash` matches `compute_hash()` of the stored `content`) for this sub-project; Task 6 re-confirms it via a fresh DB read-back.

Then delete the scratch file: `rm verify_db.py`.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "Add Supabase client module"
```

---

### Task 6: `ingest.py`

**Files:**
- Create: `ingest.py`

**Interfaces:**
- Consumes: `get_client`, `get_active_sources`, `get_latest_snapshot_hash`, `insert_snapshot` (Task 5); `extract_text` (Task 3); `fetch` (Task 4); `compute_hash` (Task 2).
- Produces: `run(client) -> list[dict]` and the `python ingest.py` CLI entry point — nothing downstream in this sub-project consumes it; it's the deliverable.

- [ ] **Step 1: Write the implementation**

```python
# ingest.py
import sys

from dotenv import load_dotenv

from db import get_client, get_active_sources, get_latest_snapshot_hash, insert_snapshot
from extract import extract_text
from fetch import fetch
from hashing import compute_hash


def run(client) -> list[dict]:
    results = []
    for source in get_active_sources(client):
        url = source["url"]
        try:
            html = fetch(url)
            text = extract_text(html)
            content_hash = compute_hash(text)
            prior_hash = get_latest_snapshot_hash(client, source["id"])
            changed = prior_hash is None or prior_hash != content_hash
            insert_snapshot(client, source["id"], text, content_hash)
            results.append({"url": url, "changed": changed, "error": None})
        except Exception as e:
            results.append({"url": url, "changed": False, "error": str(e)})
    return results


if __name__ == "__main__":
    load_dotenv()
    client = get_client()
    results = run(client)
    for r in results:
        if r["error"]:
            print(f"ERROR   {r['url']}: {r['error']}")
        elif r["changed"]:
            print(f"CHANGED {r['url']}")
        else:
            print(f"SAME    {r['url']}")
    sys.exit(0)
```

- [ ] **Step 2: Run against the live Sonar source — expect no change**

Run: `.venv/bin/python ingest.py`
Expected: `SAME    https://sonar.software/pricing` — Task 5 already inserted the real current snapshot, so this run's freshly-fetched hash matches it.

- [ ] **Step 3: Verify the stored hash matches a fresh re-hash of the stored content (Review Focus)**

Write a scratch file `verify_hash_matches.py` at the repo root (not committed — delete it at the end of this step):

```python
# verify_hash_matches.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import get_client
from hashing import compute_hash

client = get_client()
response = (
    client.table("snapshots")
    .select("content, content_hash")
    .eq("source_id", client.table("sources").select("id").eq("url", "https://sonar.software/pricing").execute().data[0]["id"])
    .order("fetched_at", desc=True)
    .limit(1)
    .execute()
)
row = response.data[0]
assert compute_hash(row["content"]) == row["content_hash"]
print("stored content_hash matches a fresh re-hash of stored content")
```

Run: `.venv/bin/python verify_hash_matches.py`
Expected: prints `stored content_hash matches a fresh re-hash of stored content` with no assertion error.

Then delete the scratch file: `rm verify_hash_matches.py`.

- [ ] **Step 4: Force a detectable change (Review Focus) — inject a different prior hash**

Call `execute_sql`:

```sql
insert into snapshots (source_id, content, content_hash)
select id, 'deliberately different content for testing', 'deliberately-different-hash-for-testing'
from sources where url = 'https://sonar.software/pricing';
```

This becomes the new "latest" snapshot (most recent `fetched_at`). Run: `.venv/bin/python ingest.py`
Expected: `CHANGED https://sonar.software/pricing` — the freshly fetched real hash doesn't match `deliberately-different-hash-for-testing`.

- [ ] **Step 5: Verify it settles back to unchanged**

Run: `.venv/bin/python ingest.py` again.
Expected: `SAME    https://sonar.software/pricing` — Step 4's run just inserted a snapshot with the real current hash, so this run matches it.

- [ ] **Step 6: Verify a connection-level failure doesn't stop other sources (Review Focus)**

Call `execute_sql` to add a temporary, deliberately-unreachable second source:

```sql
insert into sources (competitor_id, source_type, url)
select competitor_id, 'other', 'https://this-domain-does-not-exist-cis-poc-test.invalid/page'
from sources where url = 'https://sonar.software/pricing';
```

Run: `.venv/bin/python ingest.py`
Expected: two lines — `SAME    https://sonar.software/pricing` (or `CHANGED`, matching whatever Step 5 left it at — either is fine, the point is it still gets processed) and `ERROR   https://this-domain-does-not-exist-cis-poc-test.invalid/page: <some connection error message>`. Both sources appear; the broken one does not stop the working one.

- [ ] **Step 7: Clean up the temporary broken source**

Call `execute_sql`:

```sql
delete from sources where url = 'https://this-domain-does-not-exist-cis-poc-test.invalid/page';
```

The extra snapshot rows from Steps 4-6 (real Sonar content, real timestamps) are left in place — they're genuine history, not throwaway test data; only the fake unreachable source is removed.

- [ ] **Step 8: Commit**

```bash
git add ingest.py
git commit -m "Add ingestion orchestrator"
```

---

### Task 7: Document usage in README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing further downstream.

- [ ] **Step 1: Add a "Running ingestion" section**

```markdown
## Running ingestion

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
.venv/bin/python ingest.py
```

Fetches every active source in `sources`, stores a new `snapshots` row every run (whether or not the content changed), and prints one line per source: `SAME`, `CHANGED`, or `ERROR`. Currently tracks one source: Sonar's pricing page.
```

- [ ] **Step 2: Verify**

Run: `grep -q "Running ingestion" README.md && echo "README documents ingestion usage"`
Expected: `README documents ingestion usage`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document ingestion usage"
```
