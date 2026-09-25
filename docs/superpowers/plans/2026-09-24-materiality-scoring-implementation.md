# Materiality Scoring & Rationale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score each `material` signal without an insight yet (1-10 materiality, a confidence label, and a citation-grounded rationale) using `gpt-5.1`, and write the result to `insights` + `insight_signals` — end to end, provably working against the live Supabase project and the real OpenAI API.

**Architecture:** A shared `llm.py` (extracted from sub-project 3's `classifier.py`, no behavior change) holds the `OPENAI_API_KEY` precondition check both `classifier.py` and the new `scorer.py` need. `scorer.py` gets TDD against a fake/mocked OpenAI client plus one live smoke-test call, mirroring sub-project 3's `classifier.py`. Six new `db.py` functions (live-verified, no local test harness for the live dependency, consistent with prior sub-projects) support looking up a signal's competitor context and writing `insights`/`insight_signals`. `score.py` (the orchestrator) gets unit tests with the DB/LLM calls monkeypatched from the start — applying sub-project 3's final-review lessons (never-empty error messages, per-item error isolation proven by asserting nothing partial was written) proactively rather than as a fix pass.

**Tech Stack:** Python 3, `openai` (already a dependency from sub-project 3), plus the existing `httpx`, `beautifulsoup4`, `supabase`, `python-dotenv`, `pytest`.

**Spec:** [docs/superpowers/specs/2026-09-24-materiality-scoring-design.md](../specs/2026-09-24-materiality-scoring-design.md)

## Global Constraints

- `insights.rationale` cites specific evidence from the signal's `diff_text` — never generic boilerplate.
- `insights.confidence` is `needs_review` when the model itself signals uncertainty, never a confident-sounding guess.
- `insights.status` is left at its schema default (`pending`) — nothing here approves or publishes.
- Only `material` signals are scored; `cosmetic` signals never reach `scorer.py`.
- A signal already linked in `insight_signals` is never re-scored.
- `materiality_score`'s 1-10 range is enforced only by the database's existing `check` constraint, not duplicated in application code or the JSON Schema sent to the model (unconfirmed whether OpenAI's strict mode enforces numeric bounds — see the spec).
- One signal's scoring failure must not stop the others.
- Model: `gpt-5.1`.
- `llm.py`, `scorer.py`, `score.py` all live at the repo root, matching the existing convention — `python score.py` runs it.

## Review Focus

- A signal already linked in `insight_signals` must never be re-scored — no duplicate `insights` row, no wasted API call. → tested in Task 4 (unit) and re-confirmed live (a second run after scoring reports nothing pending).
- `materiality_score` outside 1-10 must be rejected by the database's `check` constraint — the only guardrail, since it isn't validated in application code or (confirmed) enforced by the JSON Schema. → tested in Task 2 (live, direct `insert_insight` call with an out-of-range value).
- `insert_insight` and `insert_insight_signal` are two separate, non-atomic writes — if the second fails after the first succeeds, the result is an orphaned `insights` row with no evidence link, undermining the whole point of citing evidence. The error message must name the orphaned insight's id so it's debuggable, not swallowed generically. → tested in Task 4 (unit).
- A signal whose source can't be resolved (missing from the active-sources map — e.g., the source was deactivated after the signal was created) must fail clearly as a per-signal error, not crash the whole run. → tested in Task 4 (unit).
- The rationale must be grounded in the actual diff, not generic — the live verification must confirm the rationale references something specific from an engineered diff, not just that it's a schema-valid string. → tested in Task 4 (live).

---

## File Structure

- Create: `llm.py` / `tests/test_llm.py`
- Modify: `classifier.py` (remove its own `get_client`, import from `llm.py`)
- Modify: `tests/test_classifier.py` (remove the three `get_client` tests, now covered by `tests/test_llm.py`)
- Modify: `db.py` — add `get_material_signals`, `get_signal_ids_with_insight`, `get_snapshot`, `get_competitor`, `insert_insight`, `insert_insight_signal`
- Create: `scorer.py` / `tests/test_scorer.py`
- Create: `score.py` / `tests/test_score.py`
- Modify: `README.md` — add a "Running materiality scoring" section

---

### Task 1: Extract `llm.py` from `classifier.py`

**Files:**
- Create: `llm.py`
- Create: `tests/test_llm.py`
- Modify: `classifier.py`
- Modify: `tests/test_classifier.py`

**Interfaces:**
- Produces: `get_client(timeout: float = 15.0) -> OpenAI` — consumed by `classifier.py` (re-exported, no change to its own callers) and `scorer.py` in Task 3.

- [ ] **Step 1: Write the failing tests for the new module**

```python
# tests/test_llm.py
import pytest

from llm import get_client


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    assert get_client() is not None


def test_get_client_default_timeout_is_15_seconds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client()
    assert client.timeout == 15.0


def test_get_client_accepts_a_custom_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client(timeout=30.0)
    assert client.timeout == 30.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_llm.py -v`
Expected: collection error — `llm.py` doesn't exist yet.

- [ ] **Step 3: Write `llm.py`**

```python
# llm.py
import os

from openai import OpenAI


def get_client(timeout: float = 15.0) -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY must be set (see .env.example)")
    return OpenAI(api_key=key, timeout=timeout)
```

- [ ] **Step 4: Run to verify `test_llm.py` passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_llm.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Point `classifier.py` at the shared module**

In `classifier.py`, replace:

```python
import json
import os

from openai import OpenAI
```

with:

```python
import json

from openai import OpenAI

from llm import get_client
```

And delete the now-duplicated function definition:

```python
def get_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY must be set (see .env.example)")
    return OpenAI(api_key=key, timeout=15.0)
```

`classifier.classify_diff` is unchanged — it still calls `get_client()` the same way, just imported rather than defined locally. `classify.py`'s `from classifier import classify_diff, get_client as get_openai_client` also needs no change: `get_client` is still an attribute of the `classifier` module (imported into its namespace), so the re-export works transparently.

- [ ] **Step 6: Trim `tests/test_classifier.py`**

Remove these three tests (now covered by `tests/test_llm.py`):

```python
def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    assert get_client() is not None


def test_get_client_sets_a_bounded_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client()
    # SDK default read timeout is 600s; an unattended run must not be able
    # to stall that long on one source.
    assert client.timeout == 15.0
```

And change the import line from:

```python
from classifier import classify_diff, get_client
```

to:

```python
from classifier import classify_diff
```

- [ ] **Step 7: Run the full suite to confirm nothing broke**

Run: `PYTHONPATH=.deps python3 -m pytest tests/ -v`
Expected: `49 passed` (48 before this task, minus the 3 `get_client` tests moved out of `tests/test_classifier.py`, plus `tests/test_llm.py`'s 4 tests).

- [ ] **Step 8: Commit**

```bash
git add llm.py tests/test_llm.py classifier.py tests/test_classifier.py
git commit -m "Extract shared llm.get_client from classifier.py"
```

---

### Task 2: `db.py` additions

**Files:**
- Modify: `db.py`

**Interfaces:**
- Produces: `get_material_signals(client) -> list[dict]`, `get_signal_ids_with_insight(client) -> set[str]`, `get_snapshot(client, snapshot_id) -> dict`, `get_competitor(client, competitor_id) -> dict`, `insert_insight(client, competitor_id, materiality_score, confidence, rationale) -> dict`, `insert_insight_signal(client, insight_id, signal_id) -> dict` — all six consumed by `score.py` in Task 4.

- [ ] **Step 1: Add the six functions**

Append to `db.py`:

```python
def get_material_signals(client: Client) -> list[dict]:
    response = (
        client.table("signals")
        .select("id, snapshot_id, theme, summary, diff_text")
        .eq("classification", "material")
        .execute()
    )
    return response.data


def get_signal_ids_with_insight(client: Client) -> set[str]:
    response = client.table("insight_signals").select("signal_id").execute()
    return {row["signal_id"] for row in response.data}


def get_snapshot(client: Client, snapshot_id: str) -> dict:
    response = (
        client.table("snapshots")
        .select("id, source_id, content_hash, fetched_at")
        .eq("id", snapshot_id)
        .limit(1)
        .execute()
    )
    return response.data[0]


def get_competitor(client: Client, competitor_id: str) -> dict:
    response = (
        client.table("competitors")
        .select("id, name")
        .eq("id", competitor_id)
        .limit(1)
        .execute()
    )
    return response.data[0]


def insert_insight(
    client: Client, competitor_id: str, materiality_score: int, confidence: str, rationale: str
) -> dict:
    response = (
        client.table("insights")
        .insert(
            {
                "competitor_id": competitor_id,
                "materiality_score": materiality_score,
                "confidence": confidence,
                "rationale": rationale,
            }
        )
        .execute()
    )
    return response.data[0]


def insert_insight_signal(client: Client, insight_id: str, signal_id: str) -> dict:
    response = (
        client.table("insight_signals")
        .insert({"insight_id": insight_id, "signal_id": signal_id})
        .execute()
    )
    return response.data[0]
```

- [ ] **Step 2: Live-verify against the real project**

Write a scratch file `verify_db_insights.py` at the repo root (not committed — delete it at the end of this step). This seeds one synthetic material signal (there are currently none in the database — sub-project 3's test signals were cleaned up after verification) against a real existing snapshot and competitor, exercises all six functions, then verifies the `materiality_score` check constraint fires for an out-of-range value (Review Focus item 2):

```python
# verify_db_insights.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import (
    get_client,
    get_active_sources,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_competitor,
    insert_insight,
    insert_insight_signal,
)

client = get_client()
source = get_active_sources(client)[0]

# Find any existing real snapshot to attach the synthetic signal to.
snapshots = (
    client.table("snapshots").select("id").eq("source_id", source["id"]).limit(1).execute()
).data
assert snapshots, "expected at least one snapshot to exist from prior sub-projects"
snapshot_id = snapshots[0]["id"]

signal = (
    client.table("signals")
    .insert(
        {
            "snapshot_id": snapshot_id,
            "diff_text": "[-old price-] {+new price+}",
            "classification": "material",
            "theme": "pricing",
            "summary": "Scratch verification signal for db.py Task 2",
        }
    )
    .execute()
).data[0]

assert signal["id"] in {s["id"] for s in get_material_signals(client)}
assert signal["id"] not in get_signal_ids_with_insight(client)

snapshot = get_snapshot(client, snapshot_id)
assert snapshot["source_id"] == source["id"]

competitor = get_competitor(client, source["competitor_id"])
assert competitor["id"] == source["competitor_id"]

# Review Focus item 2: the check constraint, not application code, is the guardrail.
try:
    insert_insight(client, source["competitor_id"], 11, "high", "out of range test")
    raise AssertionError("expected the materiality_score check constraint to reject 11")
except Exception as e:
    assert "materiality_score" in str(e) or "check" in str(e).lower(), f"unexpected error: {e}"

insight = insert_insight(client, source["competitor_id"], 7, "high", "Scratch verification insight for db.py Task 2")
insert_insight_signal(client, insight["id"], signal["id"])

assert signal["id"] in get_signal_ids_with_insight(client)
print("db.py insight functions live verification passed")
```

Run: `PYTHONPATH=.deps python3 verify_db_insights.py`
Expected: prints `db.py insight functions live verification passed`.

- [ ] **Step 3: Clean up the scratch data**

Call the Supabase MCP `execute_sql` tool with `project_id: "wbjptxjrujyzmsjldwwo"`:

```sql
delete from insight_signals where insight_id in (
  select id from insights where rationale = 'Scratch verification insight for db.py Task 2'
);
delete from insights where rationale = 'Scratch verification insight for db.py Task 2';
delete from signals where summary = 'Scratch verification signal for db.py Task 2';
```

Then delete the scratch file: `rm verify_db_insights.py`.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "Add insight data-access functions to db.py"
```

---

### Task 3: `scorer.py`

**Files:**
- Create: `scorer.py`
- Test: `tests/test_scorer.py`

**Interfaces:**
- Consumes: `llm.get_client` (Task 1).
- Produces: `score_signal(signal_context: dict, client: OpenAI | None = None) -> dict` — consumed by `score.py` in Task 4. `signal_context` keys: `diff_text`, `theme`, `summary`, `competitor_name`, `source_type`.

- [ ] **Step 1: Write the failing tests (fake client, no real API calls)**

```python
# tests/test_scorer.py
import json

import pytest

from scorer import score_signal


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


SAMPLE_CONTEXT = {
    "diff_text": "[-$500-] {+$750+}",
    "theme": "pricing",
    "summary": "Starting price increased",
    "competitor_name": "Sonar",
    "source_type": "pricing_page",
}


def test_score_signal_returns_parsed_response():
    fake_client = _FakeOpenAIClient(
        {"materiality_score": 8, "confidence": "high", "rationale": "Price rose from $500 to $750."}
    )
    result = score_signal(SAMPLE_CONTEXT, client=fake_client)
    assert result == {"materiality_score": 8, "confidence": "high", "rationale": "Price rose from $500 to $750."}


def test_score_signal_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient(
        {"materiality_score": 3, "confidence": "medium", "rationale": "x"}
    )
    score_signal(SAMPLE_CONTEXT, client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.1"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["materiality_score", "confidence", "rationale"]
    assert schema["additionalProperties"] is False
    assert "minimum" not in schema["properties"]["materiality_score"]


def test_score_signal_includes_diff_and_context_in_prompt():
    fake_client = _FakeOpenAIClient(
        {"materiality_score": 8, "confidence": "high", "rationale": "x"}
    )
    score_signal(SAMPLE_CONTEXT, client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    user_message = kwargs["messages"][-1]["content"]
    assert "[-$500-] {+$750+}" in user_message
    assert "Sonar" in user_message
    assert "pricing_page" in user_message


class _RefusalMessage:
    content = None
    refusal = "cannot assess this content"


class _RefusalChoice:
    message = _RefusalMessage()


class _RefusalResponse:
    choices = [_RefusalChoice()]


class _RefusalCompletions:
    def create(self, **kwargs):
        return _RefusalResponse()


class _RefusalChat:
    completions = _RefusalCompletions()


class _RefusalClient:
    chat = _RefusalChat()


def test_score_signal_raises_clear_error_on_refusal():
    with pytest.raises(RuntimeError, match="refused"):
        score_signal(SAMPLE_CONTEXT, client=_RefusalClient())
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_scorer.py -v`
Expected: collection error — `scorer.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# scorer.py
import json

from openai import OpenAI

from llm import get_client

SCORING_SCHEMA = {
    "type": "object",
    "properties": {
        "materiality_score": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "needs_review"]},
        "rationale": {"type": "string"},
    },
    "required": ["materiality_score", "confidence", "rationale"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst writing a materiality "
    "assessment for a product marketing or sales team. You are given a "
    "signal — a change already classified as material — from a "
    "competitor's public webpage, including the exact diff and a brief "
    "summary.\n\n"
    "Score how materially this change affects sales conversations or "
    "product strategy, on a 1-10 scale:\n"
    "- 1-3: minor — worth having on record, low urgency\n"
    "- 4-6: moderate — sales/product should know this week\n"
    "- 7-8: significant — could affect active deals or roadmap decisions, "
    "notify soon\n"
    "- 9-10: urgent — major pricing/positioning/feature shift, likely to "
    "come up in live sales calls\n\n"
    'Set confidence to "needs_review" if the evidence is ambiguous, '
    "incomplete, or you're not confident in your assessment — never "
    'invent detail the diff doesn\'t support. Otherwise use "high", '
    '"medium", or "low" based on how clear-cut the signal is.\n\n'
    "Write a one-to-two sentence rationale in plain language that a PMM "
    "or sales rep could read directly, citing the specific evidence from "
    "the diff."
)


def score_signal(signal_context: dict, client: OpenAI | None = None) -> dict:
    if client is None:
        client = get_client()
    user_content = (
        f"Competitor: {signal_context['competitor_name']}\n"
        f"Source type: {signal_context['source_type']}\n"
        f"Theme: {signal_context['theme']}\n"
        f"Summary: {signal_context['summary']}\n\n"
        f"Diff:\n{signal_context['diff_text']}"
    )
    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "materiality_assessment",
                "strict": True,
                "schema": SCORING_SCHEMA,
            },
        },
    )
    message = response.choices[0].message
    if message.content is None:
        if message.refusal:
            raise RuntimeError(f"model refused to score: {message.refusal}")
        raise RuntimeError("model returned no content")
    return json.loads(message.content)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_scorer.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Live smoke test against the real API**

Write a scratch file `verify_scorer.py` at the repo root (not committed — delete it at the end of this step):

```python
# verify_scorer.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from scorer import score_signal

result = score_signal(
    {
        "diff_text": "[-Starting at $500-] {+Starting at $750+} per month with contract.",
        "theme": "pricing",
        "summary": "Starting price increased",
        "competitor_name": "Sonar",
        "source_type": "pricing_page",
    }
)
print(result)
assert set(result.keys()) == {"materiality_score", "confidence", "rationale"}
assert isinstance(result["materiality_score"], int)
assert 1 <= result["materiality_score"] <= 10
assert result["confidence"] in ("high", "medium", "low", "needs_review")
print("scorer.py live smoke test passed")
```

Run: `PYTHONPATH=.deps python3 verify_scorer.py`

Expected: prints the parsed dict, then `scorer.py live smoke test passed`, with `materiality_score` landing in the higher end of 1-10 (this is a real price increase) even though the schema itself doesn't enforce the range — this is what confirms the prompt's stated scale is enough on its own. If `materiality_score` is outside 1-10, or any other part of the request shape is rejected, read the error and adjust `scorer.py` accordingly (same contingency as sub-project 3's `classifier.py` smoke test) — do not weaken the schema.

Then delete the scratch file: `rm verify_scorer.py`.

- [ ] **Step 6: Commit**

```bash
git add scorer.py tests/test_scorer.py
git commit -m "Add materiality scoring module"
```

---

### Task 4: `score.py`

**Files:**
- Create: `score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `score_signal` (Task 3); `get_client` as `get_openai_client` (Task 1, via `llm.py`); `get_client` as `get_supabase_client`, `get_active_sources`, `get_material_signals`, `get_signal_ids_with_insight`, `get_snapshot`, `get_competitor`, `insert_insight`, `insert_insight_signal` (Task 2, plus the existing `get_active_sources` from sub-project 2).
- Produces: `run(client, openai_client=None) -> list[dict]`, `format_line(r: dict) -> str`, and the `python score.py` CLI entry point.

- [ ] **Step 1: Write the failing unit tests (DB/LLM calls monkeypatched)**

```python
# tests/test_score.py
import pytest

import score


def _material_signal(id_, snapshot_id, diff_text="[-a-] {+b+}", theme="pricing", summary="s"):
    return {"id": id_, "snapshot_id": snapshot_id, "diff_text": diff_text, "theme": theme, "summary": summary}


def _snapshot(id_, source_id):
    return {"id": id_, "source_id": source_id, "content_hash": "h", "fetched_at": "2026-01-01T00:00:00Z"}


def _source(id_, competitor_id, source_type="pricing_page"):
    return {"id": id_, "url": "https://a.test", "competitor_id": competitor_id, "source_type": source_type}


def _competitor(id_, name):
    return {"id": id_, "name": name}


def test_no_pending_signals_returns_empty_list(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_active_sources", lambda c: [])

    assert score.run(client=None) == []


def test_already_insighted_signal_is_skipped(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: {"sig1"})
    monkeypatch.setattr(score, "get_active_sources", lambda c: [])
    called = []
    monkeypatch.setattr(score, "score_signal", lambda *a, **k: called.append(1))

    results = score.run(client=None)

    assert results == []
    assert called == []


def test_scores_a_pending_signal(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_active_sources", lambda c: [_source("src1", "comp1")])
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(
        score,
        "score_signal",
        lambda ctx, client=None: {"materiality_score": 8, "confidence": "high", "rationale": "x"},
    )
    inserted_insight = {}
    monkeypatch.setattr(
        score,
        "insert_insight",
        lambda c, competitor_id, materiality_score, confidence, rationale: inserted_insight.update(
            competitor_id=competitor_id, materiality_score=materiality_score, confidence=confidence
        )
        or {"id": "insight1"},
    )
    linked = {}
    monkeypatch.setattr(
        score,
        "insert_insight_signal",
        lambda c, insight_id, signal_id: linked.update(insight_id=insight_id, signal_id=signal_id) or {"id": "link1"},
    )

    results = score.run(client=None)

    assert results == [{"signal_id": "sig1", "status": "scored", "materiality_score": 8, "error": None}]
    assert inserted_insight == {"competitor_id": "comp1", "materiality_score": 8, "confidence": "high"}
    assert linked == {"insight_id": "insight1", "signal_id": "sig1"}


def test_scoring_error_does_not_stop_other_signals(monkeypatch):
    monkeypatch.setattr(
        score,
        "get_material_signals",
        lambda c: [
            _material_signal("bad", "snap-bad", summary="bad-marker"),
            _material_signal("good", "snap-good", summary="good-marker"),
        ],
    )
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(
        score, "get_active_sources", lambda c: [_source("src-bad", "comp1"), _source("src-good", "comp1")]
    )

    def fake_get_snapshot(c, sid):
        return _snapshot(sid, "src-bad" if sid == "snap-bad" else "src-good")

    monkeypatch.setattr(score, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))

    def fake_score_signal(ctx, client=None):
        # summary carries the marker since score_signal only sees signal_context, not signal_id
        if ctx["summary"] == "bad-marker":
            raise RuntimeError("rate limited")
        return {"materiality_score": 5, "confidence": "medium", "rationale": "ok"}

    monkeypatch.setattr(score, "score_signal", fake_score_signal)
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: {"id": "insight-good"})
    inserted = []
    monkeypatch.setattr(
        score, "insert_insight_signal", lambda c, insight_id, signal_id: inserted.append(signal_id) or {"id": "link1"}
    )

    results = score.run(client=None)

    assert len(results) == 2
    bad = next(r for r in results if r["signal_id"] == "bad")
    good = next(r for r in results if r["signal_id"] == "good")
    assert bad["status"] == "error"
    assert bad["error"] is not None
    assert good["status"] == "scored"
    assert good["error"] is None
    assert inserted == ["good"]  # only the good signal got linked


def test_error_message_is_never_empty(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_active_sources", lambda c: [_source("src1", "comp1")])
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))

    def fake_score_signal(ctx, client=None):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(score, "score_signal", fake_score_signal)

    results = score.run(client=None)

    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


def test_unresolvable_source_fails_clearly(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_active_sources", lambda c: [])  # source list doesn't include snap1's source
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src-missing"))

    results = score.run(client=None)

    assert results[0]["status"] == "error"
    assert results[0]["error"] is not None
    assert results[0]["error"] != ""


def test_orphaned_insight_error_names_the_insight_id(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_active_sources", lambda c: [_source("src1", "comp1")])
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(
        score, "score_signal", lambda ctx, client=None: {"materiality_score": 8, "confidence": "high", "rationale": "x"}
    )
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: {"id": "orphan-insight-123"})

    def failing_link(c, insight_id, signal_id):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(score, "insert_insight_signal", failing_link)

    results = score.run(client=None)

    assert results[0]["status"] == "error"
    assert "orphan-insight-123" in results[0]["error"]


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"signal_id": "s1", "status": "error", "materiality_score": None, "error": ""}, "ERROR  s1: "),
        ({"signal_id": "s1", "status": "scored", "materiality_score": 8, "error": None}, "SCORED s1: 8"),
    ],
)
def test_format_line(result, expected):
    assert score.format_line(result) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_score.py -v`
Expected: collection error — `score.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# score.py
import sys

from dotenv import load_dotenv

from db import (
    get_client as get_supabase_client,
    get_active_sources,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_competitor,
    insert_insight,
    insert_insight_signal,
)
from llm import get_client as get_openai_client
from scorer import score_signal


def run(client, openai_client=None) -> list[dict]:
    results = []
    already_insighted = get_signal_ids_with_insight(client)
    sources_by_id = {s["id"]: s for s in get_active_sources(client)}
    pending = [s for s in get_material_signals(client) if s["id"] not in already_insighted]
    for signal in pending:
        signal_id = signal["id"]
        try:
            snapshot = get_snapshot(client, signal["snapshot_id"])
            source = sources_by_id[snapshot["source_id"]]
            competitor = get_competitor(client, source["competitor_id"])
            signal_context = {
                "diff_text": signal["diff_text"],
                "theme": signal["theme"],
                "summary": signal["summary"],
                "competitor_name": competitor["name"],
                "source_type": source["source_type"],
            }
            result = score_signal(signal_context, client=openai_client)
            insight = insert_insight(
                client,
                source["competitor_id"],
                result["materiality_score"],
                result["confidence"],
                result["rationale"],
            )
            try:
                insert_insight_signal(client, insight["id"], signal_id)
            except Exception as link_error:
                raise RuntimeError(
                    f"insight {insight['id']} created but failed to link to signal {signal_id}: "
                    f"{type(link_error).__name__}: {link_error}"
                ) from link_error
            results.append(
                {"signal_id": signal_id, "status": "scored", "materiality_score": result["materiality_score"], "error": None}
            )
        except Exception as e:
            results.append(
                {"signal_id": signal_id, "status": "error", "materiality_score": None, "error": f"{type(e).__name__}: {e}"}
            )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR  {r['signal_id']}: {r['error']}"
    else:
        return f"SCORED {r['signal_id']}: {r['materiality_score']}"


if __name__ == "__main__":
    load_dotenv()
    openai_client = get_openai_client()  # precondition: fail fast, before touching any signal
    supabase_client = get_supabase_client()
    try:
        results = run(supabase_client, openai_client=openai_client)
        for r in results:
            print(format_line(r))
        sys.exit(1 if any(r["error"] is not None for r in results) else 0)
    finally:
        openai_client.close()
```

Note: `test_unresolvable_source_fails_clearly` exercises `sources_by_id[snapshot["source_id"]]` raising `KeyError` when the source isn't in the active-sources map — this is caught by the per-signal `except Exception` and reported as `status: "error"` with a `KeyError: '...'` message. That is sufficient per Review Focus item 4 (fails clearly, doesn't crash the run) even though the message isn't especially pretty; don't add a bespoke message for this — it's an edge case unlikely to occur in this PoC (a source going inactive after generating a signal), and the generic per-item error handling already covers it correctly.

- [ ] **Step 4: Run to verify the unit tests pass**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_score.py -v`
Expected: `9 passed` (7 test functions plus `test_format_line`'s 2 parametrized cases).

- [ ] **Step 5: Live verification — score a real engineered signal**

Call the Supabase MCP `execute_sql` tool with `project_id: "wbjptxjrujyzmsjldwwo"` to seed a material signal on a real existing snapshot, with a diff engineered to be unambiguously scored high and to let the rationale-grounding check (Review Focus item 5) find a distinctive phrase:

```sql
insert into signals (snapshot_id, diff_text, classification, theme, summary)
select id, '[-Starting at $500 per month-] {+Starting at $1,999 per month, annual contract required-}', 'material', 'pricing', 'Major price increase for materiality scoring live test'
from snapshots
order by fetched_at desc
limit 1;
```

Run: `PYTHONPATH=.deps python3 score.py`
Expected: one `SCORED <signal-id>: <score>` line, with `<score>` in the higher end of the 1-10 range (this is an unambiguous, large price increase).

- [ ] **Step 6: Verify the insight and its rationale**

Call `execute_sql`:

```sql
select i.materiality_score, i.confidence, i.rationale, i.status
from insights i
join insight_signals ix on ix.insight_id = i.id
join signals s on s.id = ix.signal_id
where s.summary = 'Major price increase for materiality scoring live test';
```

Expected: one row. `status` is `pending` (the schema default — confirms nothing here auto-approves). `rationale` mentions the price figures from the diff (`$500`, `$1,999`, or both) — confirms Review Focus item 5, that the rationale is grounded in the specific evidence rather than generic.

- [ ] **Step 7: Verify it doesn't re-score**

Run: `PYTHONPATH=.deps python3 score.py` again (no new signal inserted).
Expected: no output at all (empty results list, exit 0) — confirms `get_signal_ids_with_insight` correctly prevents re-scoring (Review Focus item 1).

- [ ] **Step 8: Clean up the live-test data**

Call `execute_sql`:

```sql
delete from insight_signals where signal_id in (
  select id from signals where summary = 'Major price increase for materiality scoring live test'
);
delete from insights where rationale like '%$500%' and rationale like '%1,999%';
delete from signals where summary = 'Major price increase for materiality scoring live test';
```

If the second `delete` matches zero rows because the model's rationale didn't happen to quote both figures verbatim, instead delete by the insight's id noted from Step 6's query result (`delete from insights where id = '<id-from-step-6>';`) — do not leave a dangling `insights` row with a plausible-sounding fabricated-looking rationale in the database, per the lesson from sub-project 3's final review.

- [ ] **Step 9: Commit**

```bash
git add score.py tests/test_score.py
git commit -m "Add materiality scoring orchestrator"
```

---

### Task 5: Document usage in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Running materiality scoring" section**

```markdown
## Running materiality scoring

```bash
PYTHONPATH=.deps python3 score.py
```

For each `material` signal with no insight yet, scores it 1-10 for materiality (via `gpt-5.1`), assigns a confidence label (`high`/`medium`/`low`/`needs_review`), writes a citation-grounded rationale, and creates the `insights` row (`status` stays at its default `pending` — nothing here approves or publishes) plus the `insight_signals` link. Prints one line per scored signal: `SCORED <signal-id>: <score>`, or `ERROR <signal-id>: <message>`. Prints nothing and exits 0 if there's nothing pending. Needs `OPENAI_API_KEY` in `.env` (same key `classify.py` uses).
```

- [ ] **Step 2: Verify**

Run: `grep -q "Running materiality scoring" README.md && echo "README documents scoring usage"`
Expected: `README documents scoring usage`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document materiality scoring usage"
```
