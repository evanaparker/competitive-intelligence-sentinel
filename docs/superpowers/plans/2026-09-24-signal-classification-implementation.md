# Signal Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify the diff between a source's two most recent snapshots as `cosmetic` or `material` using GPT-5.4 Mini, and write the result to `signals` — end to end, provably working against the live Supabase project and the real OpenAI API.

**Architecture:** Three new modules at the repo root (`diffing.py`, `classifier.py`, `classify.py`) plus three new functions added to the existing `db.py` from sub-project 2. `diffing.py` is a pure function with full TDD. `classifier.py` gets TDD against a fake/mocked OpenAI client (no real API calls in the test suite) plus one live smoke-test call to confirm the real request shape works — this project has no bundled reference for the OpenAI SDK the way it does for Anthropic, so that first live call is also where the request shape gets verified. `classify.py` (the orchestrator) gets unit tests with the DB/LLM calls monkeypatched (the pattern sub-project 2's final review added after the fact — applied from the start here) plus live verification against the real Supabase project and real API calls, including two engineered cases (a price change and a wording-only change) to prove the classifier actually discriminates, not just that it returns valid JSON.

**Tech Stack:** Python 3, `openai` (new), `difflib` (stdlib), plus the existing `httpx`, `beautifulsoup4`, `supabase`, `python-dotenv`, `pytest` from sub-project 2.

**Spec:** [docs/superpowers/specs/2026-09-24-signal-classification-design.md](../specs/2026-09-24-signal-classification-design.md)

## Global Constraints

- `signals.diff_text` is computed by `diffing.compute_diff()` (stdlib `difflib`), never by the LLM — real evidence, not a paraphrase.
- `classify_diff()` forces structured output via OpenAI Structured Outputs (`response_format: {"type": "json_schema", "json_schema": {"strict": true, ...}}`), returning exactly `{"classification": "cosmetic"|"material", "theme": str, "summary": str}`.
- Only the two most recent snapshots per source are considered per run — no backfill of older unclassified pairs.
- A source is skipped (not an error) when it has fewer than 2 snapshots, its two latest snapshots have the same `content_hash`, or a `signals` row already exists for its newest snapshot.
- `classify.run()` catches errors per source so one failure doesn't stop the others; missing `OPENAI_API_KEY` fails immediately, before any source is attempted.
- Model: `gpt-5.4-mini`.
- All five modules (`diffing.py`, `classifier.py`, `classify.py`, plus the `db.py`/`fetch.py`/etc. from sub-project 2) live at the repo root, matching sub-project 2's convention — `python classify.py` runs it.

## Review Focus

- `run()` must not re-classify a snapshot that already has a `signals` row — a bug here silently burns API calls on every run and could eventually violate `signals.snapshot_id`'s uniqueness constraint. → tested in Task 5 (unit) and re-confirmed live (a second run right after classifying reports `skipped`, not `classified`).
- `run()` must correctly distinguish "hash unchanged, nothing to do" from "already classified" — both are `skipped`, but for different reasons, and conflating the checks could skip a genuinely new change. → tested in Task 5 (unit).
- A classification failure's error message must never be empty and must never register as `skipped` or a false success — the exact class of bug the sub-project 2 final review found in `ingest.py`, applied proactively here instead of fixed after the fact. → tested in Task 5 (unit, `format_line` + error-message tests).
- The live verification must prove the classifier actually discriminates between the two categories against the real model — not just that it returns schema-valid JSON. A diff engineered to look like a real price change must come back `material`; a diff that only rewords a sentence must come back `cosmetic`. → tested in Task 5 (live, two engineered cases).
- The stored `diff_text` must be exactly reproducible by calling `compute_diff()` again on the same two snapshots' stored `content`, read back from the database — mirrors sub-project 2's `content_hash` round-trip check, applied to evidence integrity here. → tested in Task 5 (live).

---

## File Structure

- Create: `diffing.py` / `tests/test_diffing.py`
- Create: `classifier.py` / `tests/test_classifier.py`
- Modify: `db.py` — add `get_two_latest_snapshots`, `has_signal_for_snapshot`, `insert_signal`
- Create: `classify.py` / `tests/test_classify.py`
- Modify: `requirements.txt` — add `openai`
- Modify: `README.md` — add a "Running classification" section

---

### Task 1: Add the `openai` dependency

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: the `openai` package available under `.deps/`, consumed by Task 3.

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
openai==1.59.6
```

- [ ] **Step 2: Reinstall**

```bash
pip3 install --target=.deps -r requirements.txt
```

(This environment has no working `python3 -m venv` and `pip install --user` is blocked by PEP 668 — `--target=.deps` is what sub-project 2 settled on; see its README section if this fails differently here.)

- [ ] **Step 3: Verify**

Run: `PYTHONPATH=.deps python3 -c "import openai; print(openai.__version__)"`
Expected: prints `1.59.6` with no error.

- [ ] **Step 4: Verify `OPENAI_API_KEY` is set**

Run: `test -f .env && grep -q '^OPENAI_API_KEY=sk-' .env && echo "OPENAI_API_KEY looks set"`
Expected: `OPENAI_API_KEY looks set`. If this fails, stop and ask the user to populate `.env` at the repo root before continuing — every later task's live verification depends on it.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "Add openai dependency"
```

---

### Task 2: `diffing.py`

**Files:**
- Create: `diffing.py`
- Test: `tests/test_diffing.py`

**Interfaces:**
- Produces: `compute_diff(old_text: str, new_text: str) -> str` — consumed by `classify.py` in Task 5.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diffing.py
from diffing import compute_diff


def test_no_diff_for_identical_text():
    assert compute_diff("hello world", "hello world") == ""


def test_detects_insertion():
    assert compute_diff("hello world", "hello brave world") == "{+brave+}"


def test_detects_deletion():
    assert compute_diff("hello brave world", "hello world") == "[-brave-]"


def test_detects_replacement():
    assert compute_diff("price is 1.25", "price is 2.50") == "[-1.25-]{+2.50+}"


def test_detects_multiple_changes():
    old = "Starting at $500 per month with contract"
    new = "Starting at $750 per month, no contract required"
    result = compute_diff(old, new)
    assert "[-500-]" in result
    assert "{+750+}" in result
    assert "[-with contract-]" in result or "{+no contract required+}" in result
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_diffing.py -v`
Expected: collection error — `diffing.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# diffing.py
import difflib


def compute_diff(old_text: str, new_text: str) -> str:
    old_words = old_text.split()
    new_words = new_text.split()
    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "delete":
            parts.append(f"[-{' '.join(old_words[i1:i2])}-]")
        elif tag == "insert":
            parts.append(f"{{+{' '.join(new_words[j1:j2])}+}}")
        elif tag == "replace":
            parts.append(f"[-{' '.join(old_words[i1:i2])}-]{{+{' '.join(new_words[j1:j2])}+}}")
    return " ".join(parts)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_diffing.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add diffing.py tests/test_diffing.py
git commit -m "Add word-level diff module"
```

---

### Task 3: `classifier.py`

**Files:**
- Create: `classifier.py`
- Test: `tests/test_classifier.py`

**Interfaces:**
- Produces: `get_client() -> openai.OpenAI` and `classify_diff(diff_text: str, source_type: str, client: openai.OpenAI | None = None) -> dict` — both consumed by `classify.py` in Task 5.

- [ ] **Step 1: Write the failing tests (fake client, no real API calls)**

```python
# tests/test_classifier.py
import json

import pytest

from classifier import classify_diff, get_client


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response_json):
        self._response_json = response_json
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(json.dumps(self._response_json))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, response_json):
        self.chat = _FakeChat(_FakeCompletions(response_json))


def test_classify_diff_returns_parsed_response():
    fake_client = _FakeOpenAIClient(
        {"classification": "material", "theme": "pricing", "summary": "Price increased"}
    )
    result = classify_diff("[-1.25-] {+2.50+}", "pricing_page", client=fake_client)
    assert result == {"classification": "material", "theme": "pricing", "summary": "Price increased"}


def test_classify_diff_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient(
        {"classification": "cosmetic", "theme": "other", "summary": "Wording changed"}
    )
    classify_diff("some diff", "pricing_page", client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["classification", "theme", "summary"]
    assert schema["additionalProperties"] is False


def test_classify_diff_includes_diff_text_in_prompt():
    fake_client = _FakeOpenAIClient(
        {"classification": "material", "theme": "pricing", "summary": "x"}
    )
    classify_diff("[-old-] {+new+}", "pricing_page", client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    user_message = kwargs["messages"][-1]["content"]
    assert "[-old-] {+new+}" in user_message


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    assert get_client() is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_classifier.py -v`
Expected: collection error — `classifier.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# classifier.py
import json
import os

from openai import OpenAI

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["cosmetic", "material"]},
        "theme": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["classification", "theme", "summary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst reviewing an automated diff "
    "of a competitor's public webpage. The diff marks removed text as "
    "[-removed-] and added text as {+added+}.\n\n"
    "Classify the change:\n"
    "- material: something a sales or product team would need to know — "
    "pricing or tier changes, new/removed features, roadmap signals, "
    "positioning/messaging shifts, hiring signals, partnership announcements.\n"
    "- cosmetic: everything else — rewording, dates, style fixes, changes "
    "with no business meaning.\n\n"
    'Give a short theme (e.g. "pricing", "features", "positioning", '
    '"hiring", "other") and a one-sentence summary in plain language.'
)


def get_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY must be set (see .env.example)")
    return OpenAI(api_key=key)


def classify_diff(diff_text: str, source_type: str, client: OpenAI | None = None) -> dict:
    if client is None:
        client = get_client()
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Source type: {source_type}\n\nDiff:\n{diff_text}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "signal_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            },
        },
    )
    return json.loads(response.choices[0].message.content)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_classifier.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Live smoke test against the real API**

This project has no bundled reference for the OpenAI SDK (unlike Anthropic's), so this is the one call in the whole plan not pre-verified against documentation — the mocked tests above prove our own code's behavior, not that the real API accepts this exact request shape.

Write a scratch file `verify_classifier.py` at the repo root (not committed — delete it at the end of this step):

```python
# verify_classifier.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from classifier import classify_diff

result = classify_diff(
    "[-Starting at $500-] {+Starting at $750+} per month with contract.",
    "pricing_page",
)
print(result)
assert set(result.keys()) == {"classification", "theme", "summary"}
assert result["classification"] in ("cosmetic", "material")
print("classifier.py live smoke test passed")
```

Run: `PYTHONPATH=.deps python3 verify_classifier.py`

Expected: prints the parsed dict, then `classifier.py live smoke test passed`. If this raises an error instead — about `response_format`, `json_schema`, the model name, or anything else — read the error message and fix `classifier.py`'s request shape accordingly (a different structured-output parameter, the Responses API instead of Chat Completions, whatever the error or OpenAI's own docs indicate). Do not weaken or drop the JSON Schema enforcement to work around it. Re-run this step until it passes, then re-run Step 4 to confirm the mocked tests still pass against whatever shape you landed on (update the mocked tests' assertions in Step 1 if the request shape genuinely changed, e.g. a different top-level parameter name than `response_format`).

Then delete the scratch file: `rm verify_classifier.py`.

- [ ] **Step 6: Commit**

```bash
git add classifier.py tests/test_classifier.py
git commit -m "Add OpenAI classification module"
```

---

### Task 4: `db.py` additions

**Files:**
- Modify: `db.py`

**Interfaces:**
- Consumes: the existing `signals` and `snapshots` tables from sub-project 1's schema.
- Produces: `get_two_latest_snapshots(client, source_id) -> list[dict]`, `has_signal_for_snapshot(client, snapshot_id) -> bool`, `insert_signal(client, snapshot_id, prior_snapshot_id, diff_text, classification, theme, summary) -> dict` — all three consumed by `classify.py` in Task 5.

- [ ] **Step 1: Add the three functions**

Append to `db.py`:

```python
def get_two_latest_snapshots(client: Client, source_id: str) -> list[dict]:
    response = (
        client.table("snapshots")
        .select("id, content, content_hash, fetched_at")
        .eq("source_id", source_id)
        .order("fetched_at", desc=True)
        .limit(2)
        .execute()
    )
    return response.data


def has_signal_for_snapshot(client: Client, snapshot_id: str) -> bool:
    response = (
        client.table("signals")
        .select("id")
        .eq("snapshot_id", snapshot_id)
        .limit(1)
        .execute()
    )
    return len(response.data) > 0


def insert_signal(
    client: Client,
    snapshot_id: str,
    prior_snapshot_id: str,
    diff_text: str,
    classification: str,
    theme: str,
    summary: str,
) -> dict:
    response = (
        client.table("signals")
        .insert(
            {
                "snapshot_id": snapshot_id,
                "prior_snapshot_id": prior_snapshot_id,
                "diff_text": diff_text,
                "classification": classification,
                "theme": theme,
                "summary": summary,
            }
        )
        .execute()
    )
    return response.data[0]
```

- [ ] **Step 2: Live-verify against the real project**

Write a scratch file `verify_db_signals.py` at the repo root (not committed — delete it at the end of this step). This uses the real active source (whatever it is — the plan doesn't assume its name) and the real two latest snapshots already sitting in the database from sub-project 2's testing:

```python
# verify_db_signals.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import get_client, get_active_sources, get_two_latest_snapshots, has_signal_for_snapshot, insert_signal

client = get_client()
source = get_active_sources(client)[0]

snaps = get_two_latest_snapshots(client, source["id"])
assert len(snaps) == 2, f"expected 2 snapshots, got {len(snaps)}"
newest, prior = snaps[0], snaps[1]
assert newest["fetched_at"] > prior["fetched_at"]

assert has_signal_for_snapshot(client, newest["id"]) is False

row = insert_signal(
    client,
    newest["id"],
    prior["id"],
    "[-test-] {+verification+}",
    "cosmetic",
    "other",
    "Scratch verification row for db.py Task 4",
)
assert row["classification"] == "cosmetic"

assert has_signal_for_snapshot(client, newest["id"]) is True
print("db.py signal functions live verification passed")
```

Run: `PYTHONPATH=.deps python3 verify_db_signals.py`
Expected: prints `db.py signal functions live verification passed`.

- [ ] **Step 3: Clean up the scratch verification row**

This row (`diff_text: "[-test-] {+verification+}"`) is throwaway, unlike sub-project 2's real snapshots — delete it so it doesn't interfere with Task 5's live verification, which needs `has_signal_for_snapshot` to be `False` again for the current newest snapshot:

Call the Supabase MCP `execute_sql` tool:

```sql
delete from signals where summary = 'Scratch verification row for db.py Task 4';
```

Then delete the scratch file: `rm verify_db_signals.py`.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "Add signal data-access functions to db.py"
```

---

### Task 5: `classify.py`

**Files:**
- Create: `classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `compute_diff` (Task 2); `classify_diff`, `get_client` as `get_openai_client` (Task 3); `get_client` as `get_supabase_client`, `get_active_sources`, `get_two_latest_snapshots`, `has_signal_for_snapshot`, `insert_signal` (Task 4, plus the existing `get_active_sources` from sub-project 2).
- Produces: `run(client) -> list[dict]`, `format_line(r: dict) -> str`, and the `python classify.py` CLI entry point.

- [ ] **Step 1: Write the failing unit tests (DB/LLM calls monkeypatched)**

```python
# tests/test_classify.py
import pytest

import classify


def _source(id_, url, source_type="pricing_page"):
    return {"id": id_, "url": url, "competitor_id": "c1", "source_type": source_type}


def _snapshot(id_, content, content_hash, fetched_at):
    return {"id": id_, "content": content, "content_hash": content_hash, "fetched_at": fetched_at}


def test_skips_when_fewer_than_two_snapshots(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify, "get_two_latest_snapshots", lambda c, sid: [_snapshot("sn1", "x", "h1", "2026-01-01T00:00:00Z")]
    )

    results = classify.run(client=None)

    assert results == [{"source_url": "https://a.test", "status": "skipped", "classification": None, "error": None}]


def test_skips_when_hash_unchanged(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_two_latest_snapshots",
        lambda c, sid: [
            _snapshot("sn2", "same", "h1", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "same", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    called = []
    monkeypatch.setattr(classify, "classify_diff", lambda *a, **k: called.append(1))

    results = classify.run(client=None)

    assert results[0]["status"] == "skipped"
    assert called == []  # no LLM call made


def test_skips_when_already_classified(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_two_latest_snapshots",
        lambda c, sid: [
            _snapshot("sn2", "new", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "old", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: True)
    called = []
    monkeypatch.setattr(classify, "classify_diff", lambda *a, **k: called.append(1))

    results = classify.run(client=None)

    assert results[0]["status"] == "skipped"
    assert called == []


def test_classifies_a_new_change(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_two_latest_snapshots",
        lambda c, sid: [
            _snapshot("sn2", "new content", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "old content", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)
    monkeypatch.setattr(
        classify,
        "classify_diff",
        lambda diff_text, source_type, **k: {"classification": "material", "theme": "pricing", "summary": "x"},
    )
    inserted = {}
    monkeypatch.setattr(
        classify,
        "insert_signal",
        lambda c, snap_id, prior_id, diff_text, classification, theme, summary: inserted.update(
            snapshot_id=snap_id, prior_snapshot_id=prior_id, classification=classification
        )
        or {"id": "sig1"},
    )

    results = classify.run(client=None)

    assert results[0] == {
        "source_url": "https://a.test",
        "status": "classified",
        "classification": "material",
        "error": None,
    }
    assert inserted == {"snapshot_id": "sn2", "prior_snapshot_id": "sn1", "classification": "material"}


def test_source_classification_error_does_not_stop_others(monkeypatch):
    monkeypatch.setattr(
        classify,
        "get_active_sources",
        lambda c: [_source("bad", "https://bad.test"), _source("good", "https://good.test")],
    )

    def fake_get_snaps(c, sid):
        # content embeds sid so fake_classify below can tell sources apart
        return [
            _snapshot("n", f"new-{sid}", f"h2-{sid}", "2026-01-02T00:00:00Z"),
            _snapshot("p", f"old-{sid}", f"h1-{sid}", "2026-01-01T00:00:00Z"),
        ]

    monkeypatch.setattr(classify, "get_two_latest_snapshots", fake_get_snaps)
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)

    def fake_classify(diff_text, source_type, **k):
        if "bad" in diff_text:
            raise RuntimeError("rate limited")
        return {"classification": "cosmetic", "theme": "other", "summary": "ok"}

    monkeypatch.setattr(classify, "classify_diff", fake_classify)
    inserted = []
    monkeypatch.setattr(
        classify,
        "insert_signal",
        lambda c, snap_id, prior_id, diff_text, classification, theme, summary: inserted.append(snap_id)
        or {"id": "sig1"},
    )

    results = classify.run(client=None)

    assert len(results) == 2
    bad = next(r for r in results if r["source_url"] == "https://bad.test")
    good = next(r for r in results if r["source_url"] == "https://good.test")
    assert bad["error"] is not None
    assert bad["status"] == "error"
    assert good["error"] is None
    assert good["status"] == "classified"
    assert inserted == ["n"]  # only the good source's snapshot got a signal inserted


def test_error_message_is_never_empty(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_two_latest_snapshots",
        lambda c, sid: [
            _snapshot("n", "new", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("p", "old", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)

    def fake_classify(diff_text, source_type, **k):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(classify, "classify_diff", fake_classify)

    results = classify.run(client=None)

    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"source_url": "https://x.test", "status": "error", "classification": None, "error": ""}, "ERROR      https://x.test: "),
        ({"source_url": "https://x.test", "status": "skipped", "classification": None, "error": None}, "SKIPPED    https://x.test"),
        ({"source_url": "https://x.test", "status": "classified", "classification": "material", "error": None}, "CLASSIFIED https://x.test: material"),
    ],
)
def test_format_line(result, expected):
    assert classify.format_line(result) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_classify.py -v`
Expected: collection error — `classify.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# classify.py
import sys

from dotenv import load_dotenv

from classifier import classify_diff, get_client as get_openai_client
from db import (
    get_client as get_supabase_client,
    get_active_sources,
    get_two_latest_snapshots,
    has_signal_for_snapshot,
    insert_signal,
)
from diffing import compute_diff


def run(client) -> list[dict]:
    results = []
    for source in get_active_sources(client):
        url = source["url"]
        try:
            snaps = get_two_latest_snapshots(client, source["id"])
            if len(snaps) < 2:
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            newest, prior = snaps[0], snaps[1]
            if newest["content_hash"] == prior["content_hash"]:
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            if has_signal_for_snapshot(client, newest["id"]):
                results.append({"source_url": url, "status": "skipped", "classification": None, "error": None})
                continue
            diff_text = compute_diff(prior["content"], newest["content"])
            result = classify_diff(diff_text, source["source_type"])
            insert_signal(
                client,
                newest["id"],
                prior["id"],
                diff_text,
                result["classification"],
                result["theme"],
                result["summary"],
            )
            results.append(
                {"source_url": url, "status": "classified", "classification": result["classification"], "error": None}
            )
        except Exception as e:
            results.append(
                {"source_url": url, "status": "error", "classification": None, "error": f"{type(e).__name__}: {e}"}
            )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR      {r['source_url']}: {r['error']}"
    elif r["status"] == "skipped":
        return f"SKIPPED    {r['source_url']}"
    else:
        return f"CLASSIFIED {r['source_url']}: {r['classification']}"


if __name__ == "__main__":
    load_dotenv()
    get_openai_client()  # precondition: fail fast, before touching any source
    supabase_client = get_supabase_client()
    results = run(supabase_client)
    for r in results:
        print(format_line(r))
    sys.exit(1 if any(r["error"] is not None for r in results) else 0)
```

- [ ] **Step 4: Run to verify the unit tests pass**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_classify.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Live verification — engineer a material change**

Call the Supabase MCP `execute_sql` tool to insert a snapshot with clearly price-related content as the new latest snapshot for the real active source:

```sql
insert into snapshots (source_id, content, content_hash)
select id, 'Starting at $750 per month with contract. Everything included.', 'signal-test-material-hash'
from sources where is_active = true limit 1;
```

Run: `PYTHONPATH=.deps python3 classify.py`
Expected: one `CLASSIFIED ...: material` line (the prior snapshot's real content describes a lower/different starting price, so this reads as a genuine price change to the model). If it classifies as `cosmetic` instead, that's a real finding about prompt quality — do not treat it as passing; investigate whether the prompt needs sharper guidance before moving on.

- [ ] **Step 6: Verify it doesn't re-classify**

Run: `PYTHONPATH=.deps python3 classify.py` again (no new snapshot inserted).
Expected: `SKIPPED ...` — confirms `has_signal_for_snapshot` correctly prevents re-classification (Review Focus item 1).

- [ ] **Step 7: Live verification — engineer a cosmetic change**

Call `execute_sql`:

```sql
insert into snapshots (source_id, content, content_hash)
select id, 'Starting at $750 per month, contract required. Everything included.', 'signal-test-cosmetic-hash'
from sources where is_active = true limit 1;
```

Run: `PYTHONPATH=.deps python3 classify.py`
Expected: one `CLASSIFIED ...: cosmetic` line — only the wording changed ("with contract" → "contract required"), not the price. If it comes back `material`, that's a real prompt-quality finding, not a pass.

- [ ] **Step 8: Verify unchanged-hash skip (Review Focus item 2)**

Run: `PYTHONPATH=.deps python3 classify.py` again (no new snapshot inserted — same latest snapshot as Step 7).
Expected: `SKIPPED ...` for the same reason as Step 6, but this time because the hash is unchanged rather than because a signal already exists — both land on `skipped`, which is correct; this step exists to confirm neither path was accidentally merged into a single always-true condition.

- [ ] **Step 9: Verify diff reproducibility (Review Focus item 5)**

Write a scratch file `verify_diff_reproducible.py` (not committed — delete it after running):

```python
# verify_diff_reproducible.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import get_client, get_active_sources, get_two_latest_snapshots
from diffing import compute_diff

client = get_client()
source = get_active_sources(client)[0]
snaps = get_two_latest_snapshots(client, source["id"])
newest, prior = snaps[0], snaps[1]

response = (
    client.table("signals")
    .select("diff_text")
    .eq("snapshot_id", newest["id"])
    .execute()
)
stored_diff = response.data[0]["diff_text"]
recomputed_diff = compute_diff(prior["content"], newest["content"])
assert stored_diff == recomputed_diff
print("diff_text is reproducible from stored snapshot content")
```

Run: `PYTHONPATH=.deps python3 verify_diff_reproducible.py`
Expected: prints `diff_text is reproducible from stored snapshot content`.

Then delete the scratch file: `rm verify_diff_reproducible.py`.

- [ ] **Step 10: Commit**

```bash
git add classify.py tests/test_classify.py
git commit -m "Add signal classification orchestrator"
```

---

### Task 6: Document usage in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Running classification" section**

```markdown
## Running classification

```bash
PYTHONPATH=.deps python3 classify.py
```

For each active source, classifies the diff between its two most recent snapshots as `cosmetic` or `material` (via GPT-5.4 Mini) and writes it to `signals` — skipping sources with fewer than 2 snapshots, an unchanged hash, or an already-classified newest snapshot. Prints one line per source: `CLASSIFIED <url>: <classification>`, `SKIPPED <url>`, or `ERROR <url>: <message>`. Needs `OPENAI_API_KEY` in `.env` alongside the Supabase credentials.
```

- [ ] **Step 2: Verify**

Run: `grep -q "Running classification" README.md && echo "README documents classification usage"`
Expected: `README documents classification usage`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document classification usage"
```
