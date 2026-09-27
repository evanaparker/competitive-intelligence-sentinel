# Cross-Source Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group `material` signals from the same competitor that describe the same underlying business story into one compound insight, instead of scoring every signal in isolation.

**Architecture:** A new `correlate.py`/`correlator.py` pair (mirroring the existing `classify.py`/`classifier.py` and `score.py`/`scorer.py` orchestrator+logic split) runs between `classify.py` and `score.py`. It clusters uncorrelated `material` signals per competitor by a fixed 7-day time window, asks `gpt-5.4-mini` a binary "same story?" question for any cluster of 2+, and persists the outcome on a new nullable `signals.correlation_group_id` column. `score.py` is modified to group by that column (one insight per group, not per signal) and `scorer.py`'s `score_signal` is modified to accept a list of signals instead of one.

**Tech Stack:** Python 3.12, `supabase` client, `openai` client (OpenAI Structured Outputs), `pytest`, Supabase Postgres (migration applied live via the Supabase MCP tools, no local CLI).

**Spec:** [docs/superpowers/specs/2026-09-27-cross-source-correlation-design.md](../specs/2026-09-27-cross-source-correlation-design.md)

## Global Constraints

- `correlator.judge_correlation` uses `model="gpt-5.4-mini"`; `scorer.score_signal` keeps `model="gpt-5.1"` — both exact strings, unchanged from prior sub-projects.
- Every caught exception is formatted `f"{type(e).__name__}: {e}"` — never bare `str(e)` (some exceptions, e.g. `TimeoutError()`, stringify to `""`).
- `signals.embedding` and its HNSW index stay untouched and unused — no embedding generation, no similarity search, anywhere in this sub-project.
- A candidate cluster's correlation judgment is binary for the whole cluster — no partial sub-grouping of a cluster's members.
- Clustering uses a fixed window from the cluster's earliest (anchor) signal, `window_days=7` default — not a chained/sliding window.
- No new dependencies — reuse `openai` (already in `requirements.txt`) and the existing `llm.py` client.
- Environment: no working `python3 -m venv` in this sandbox. Install with `pip3 install --target=.deps -r requirements.txt`; run every script as `PYTHONPATH=.deps python3 <script>.py`; run tests as `PYTHONPATH=.deps python3 -m pytest tests/ -v`.
- The migration is applied directly to the live Supabase project via the Supabase MCP `apply_migration` tool, not a local Supabase CLI — matching this project's documented practice (see README's Database section). It is not idempotent and is not meant to be re-run.
- `.env` lives at the repo root; if implementing in a worktree, `python-dotenv`'s upward search finds it from any subdirectory — do not copy or recreate it inside the worktree.
- A signal that goes through `correlate.py` and is decided (grouped, singleton, or explicitly rejected) must end up with a **non-null** `correlation_group_id` — NULL means only "not yet decided by `correlate.py`" (never run, or a prior attempt errored on it). Getting this wrong breaks idempotency: see spec's Error Handling.

## Review Focus

- Two different signals that both have a null `correlation_group_id` (because `correlate.py` never ran, or errored on both) must be scored by `score.py` as **two independent singleton insights** — never silently merged into one group just because they share the same "no group" state. This is the sharpest bug risk in the grouping logic (a naive `groupby(key=lambda s: s["correlation_group_id"])` would merge every null-group signal into one bucket).
- A signal exactly `window_days` (7 days) away from a cluster's anchor signal is included in the cluster (inclusive boundary); a signal just over the window starts a new cluster. Pin this exactly, not approximately.
- A cluster's `judge_correlation` LLM call failing (rate limit, refusal, network error) must not stop other clusters or other competitors, and must leave those signals' `correlation_group_id` NULL (retry-eligible) rather than assigning them anything — distinct from an explicit `False` judgment, which must assign each signal its own id (permanently decided, never retried).
- A partial `insert_insight_signal` failure partway through a multi-signal group's linking loop must name the insight id, which signal ids already linked successfully, and which one failed — and must not delete the insight or attempt any rollback (matches sub-project 4's documented non-atomic-write limitation, now for groups of arbitrary size).
- `scorer.score_signal`'s prompt, given a group of 2+ signals, must actually include every signal's own `theme`/`summary`/`diff_text` in what's sent to the model — not just the first one silently dropped by an indexing bug.

---

### Task 1: Migration and `db.py` changes

**Files:**
- Create: `supabase/migrations/0010_signal_correlation.sql`
- Modify: `db.py:50-58` (`get_material_signals`), add a new function after `get_signal_ids_with_insight` (`db.py:61-63`)

**Interfaces:**
- Produces: `db.get_material_signals(client) -> list[dict]` now includes `created_at` and `correlation_group_id` in every returned row (in addition to the existing `id, snapshot_id, theme, summary, diff_text`). `db.assign_correlation_group(client, signal_ids: list[str], group_id: str) -> None`.
- Consumes: nothing new from earlier tasks (this is the first task).

- [ ] **Step 1: Apply the migration to the live Supabase project**

Use the Supabase MCP `apply_migration` tool (project id `wbjptxjrujyzmsjldwwo`) with name `signal_correlation` and this SQL, and also save it at `supabase/migrations/0010_signal_correlation.sql` for the repo's versioned record (per the README's documented practice — migrations are applied live first, the file is the record of what's live, not a queue):

```sql
-- supabase/migrations/0010_signal_correlation.sql
-- Cross-source correlation (sub-project 6): signals that describe the same
-- underlying business story share a correlation_group_id, so score.py can
-- create one insight per group instead of one per signal. NULL means
-- correlate.py has not yet decided this signal's correlation status — see
-- the design spec's Error Handling for why NULL must mean exactly that and
-- nothing else (idempotency depends on it).

alter table signals add column correlation_group_id uuid;
```

- [ ] **Step 2: Verify the column exists**

Run this query via the Supabase MCP `execute_sql` tool:

```sql
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'signals' and column_name = 'correlation_group_id';
```

Expected: one row — `correlation_group_id`, `uuid`, `YES`.

- [ ] **Step 3: Modify `get_material_signals` to select the new columns**

In `db.py`, replace:

```python
def get_material_signals(client: Client) -> list[dict]:
    response = (
        client.table("signals")
        .select("id, snapshot_id, theme, summary, diff_text")
        .eq("classification", "material")
        .order("created_at")
        .execute()
    )
    return response.data
```

with:

```python
def get_material_signals(client: Client) -> list[dict]:
    response = (
        client.table("signals")
        .select("id, snapshot_id, theme, summary, diff_text, created_at, correlation_group_id")
        .eq("classification", "material")
        .order("created_at")
        .execute()
    )
    return response.data
```

- [ ] **Step 4: Add `assign_correlation_group` to `db.py`**

Immediately after `get_signal_ids_with_insight` (currently `db.py:61-63`), add:

```python
def assign_correlation_group(client: Client, signal_ids: list[str], group_id: str) -> None:
    client.table("signals").update({"correlation_group_id": group_id}).in_("id", signal_ids).execute()
```

- [ ] **Step 5: Live-verify `assign_correlation_group` and the widened `get_material_signals` select against a scratch signal**

There is currently no material signal in the live database (confirmed via `execute_sql` before writing this plan) and no active second source, so seed one scratch snapshot + signal for the existing Sonar pricing-page source. Use the Supabase MCP `execute_sql` tool:

```sql
-- get the existing source id
select id from sources where competitor_id = (select id from competitors where name = 'Sonar') limit 1;
```

```sql
-- insert a scratch snapshot for that source (use the source id from above)
insert into snapshots (source_id, content, content_hash)
values ('<source-id-from-above>', 'scratch content for Task 1 verification', 'scratch-hash-task1')
returning id;
```

```sql
-- insert a scratch material signal on that snapshot (use the snapshot id from above)
insert into signals (snapshot_id, classification, theme, summary, diff_text)
values ('<snapshot-id-from-above>', 'material', 'pricing', 'Task 1 scratch signal', '[-a-] {+b+}')
returning id, created_at, correlation_group_id;
```

Expected: `correlation_group_id` is `null` in the returned row.

Then, from the repo root, run a one-off Python check (not a pytest file — this exercises the real Supabase client directly, following the same discipline every prior sub-project's `db.py`-only work used):

```bash
PYTHONPATH=.deps python3 -c "
from dotenv import load_dotenv
load_dotenv()
from db import get_client, get_material_signals, assign_correlation_group

client = get_client()
signals = get_material_signals(client)
assert len(signals) == 1, signals
sig = signals[0]
assert 'created_at' in sig and 'correlation_group_id' in sig, sig
assert sig['correlation_group_id'] is None, sig
assign_correlation_group(client, [sig['id']], sig['id'])
after = get_material_signals(client)[0]
assert after['correlation_group_id'] == sig['id'], after
print('OK', sig['id'])
"
```

Expected: prints `OK <the scratch signal's id>` with no assertion errors.

- [ ] **Step 6: Clean up the scratch signal and snapshot**

```sql
delete from signals where summary = 'Task 1 scratch signal';
delete from snapshots where content_hash = 'scratch-hash-task1';
```

Verify via `execute_sql`: `select count(*) from signals where summary = 'Task 1 scratch signal';` → `0`.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/0010_signal_correlation.sql db.py
git commit -m "feat: add signals.correlation_group_id and assign_correlation_group"
```

---

### Task 2: `correlator.py` — clustering and the LLM correlation judgment

**Files:**
- Create: `correlator.py`
- Test: `tests/test_correlator.py`

**Interfaces:**
- Consumes: nothing from Task 1 (pure logic module, no DB access).
- Produces: `correlator.cluster_by_time_window(signals: list[dict], window_days: int = 7) -> list[list[dict]]`. `correlator.judge_correlation(cluster: list[dict], client: OpenAI | None = None) -> bool`. Both consumed by Task 3's `correlate.py`.

- [ ] **Step 1: Write the failing tests for `cluster_by_time_window`**

Create `tests/test_correlator.py`:

```python
from correlator import cluster_by_time_window


def _signal(id_, created_at):
    return {"id": id_, "created_at": created_at}


def test_empty_input_returns_empty_list():
    assert cluster_by_time_window([]) == []


def test_single_signal_is_its_own_cluster():
    signals = [_signal("s1", "2026-01-01T00:00:00Z")]
    assert cluster_by_time_window(signals) == [[_signal("s1", "2026-01-01T00:00:00Z")]]


def test_two_signals_within_window_form_one_cluster():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-03T00:00:00Z"),
    ]
    assert cluster_by_time_window(signals) == [signals]


def test_signal_exactly_window_days_away_is_included():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-08T00:00:00Z"),  # exactly 7 days later
    ]
    assert cluster_by_time_window(signals, window_days=7) == [signals]


def test_signal_just_past_window_starts_a_new_cluster():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-08T00:00:01Z"),  # 7 days and 1 second later
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0]], [signals[1]]]


def test_third_signal_does_not_chain_through_the_second():
    # s1 and s2 are 6 days apart (would cluster). s3 is 4 days after s2 but
    # 10 days after s1 — a chained/sliding window would pull s3 into s1's
    # cluster via s2; the fixed-window-from-anchor design must not.
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-07T00:00:00Z"),
        _signal("s3", "2026-01-11T00:00:00Z"),
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0], signals[1]], [signals[2]]]


def test_preserves_input_order_within_and_across_clusters():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-02T00:00:00Z"),
        _signal("s3", "2026-02-01T00:00:00Z"),
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0], signals[1]], [signals[2]]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlator.py -v`
Expected: `ModuleNotFoundError: No module named 'correlator'` (or collection error) — the module doesn't exist yet.

- [ ] **Step 3: Write `correlator.py`'s clustering function**

Create `correlator.py`:

```python
import json
from datetime import datetime, timedelta

from openai import OpenAI

from llm import get_client

CORRELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "correlated": {"type": "boolean"},
    },
    "required": ["correlated"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst deciding whether several "
    "signals about the same competitor, detected within a short time "
    "window, describe the same underlying business story — or are "
    "unrelated changes that happen to be close in time.\n\n"
    "You will be given each signal's source type, theme, a brief summary, "
    "and how many days apart it was detected from the others. Decide: do "
    "these signals, taken together, describe one coherent story (e.g., a "
    "price increase alongside a hiring push for enterprise sales; a new "
    "feature announcement alongside matching changelog and pricing-page "
    "updates)? Or are they unrelated coincidences?\n\n"
    "Answer true only if a reasonable analyst would write about these as "
    "one connected development. Answer false if they are plausibly "
    "unrelated, even if they involve the same competitor and theme."
)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def cluster_by_time_window(signals: list[dict], window_days: int = 7) -> list[list[dict]]:
    clusters = []
    remaining = list(signals)
    window = timedelta(days=window_days)
    while remaining:
        anchor_time = _parse(remaining[0]["created_at"])
        cluster = [s for s in remaining if _parse(s["created_at"]) - anchor_time <= window]
        clusters.append(cluster)
        remaining = [s for s in remaining if s not in cluster]
    return clusters


def judge_correlation(cluster: list[dict], client: OpenAI | None = None) -> bool:
    if client is None:
        client = get_client()
    anchor_time = _parse(cluster[0]["created_at"])
    blocks = []
    for s in cluster:
        days_apart = (_parse(s["created_at"]) - anchor_time).days
        blocks.append(
            f"Source type: {s['source_type']}\n"
            f"Theme: {s['theme']}\n"
            f"Summary: {s['summary']}\n"
            f"Days after the earliest signal: {days_apart}"
        )
    user_content = f"Competitor: {cluster[0]['competitor_name']}\n\n" + "\n\n".join(blocks)
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "correlation_judgment",
                "strict": True,
                "schema": CORRELATION_SCHEMA,
            },
        },
    )
    message = response.choices[0].message
    if message.content is None:
        if message.refusal:
            raise RuntimeError(f"model refused to judge correlation: {message.refusal}")
        raise RuntimeError("model returned no content")
    return json.loads(message.content)["correlated"]
```

- [ ] **Step 4: Run to verify the clustering tests pass**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlator.py -v`
Expected: 7 passed (the `judge_correlation` tests don't exist yet, so only the clustering tests run so far).

- [ ] **Step 5: Write the failing tests for `judge_correlation`**

Append to `tests/test_correlator.py`:

```python
import pytest

from correlator import judge_correlation


class _FakeMessage:
    def __init__(self, content=None, refusal=None):
        self.content = content
        self.refusal = refusal


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, response_json):
        self._response_json = response_json
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(_FakeMessage(content=json.dumps(self._response_json)))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, response_json):
        self.chat = _FakeChat(_FakeCompletions(response_json))


def _cluster_signal(competitor_name="Sonar", source_type="pricing_page", theme="pricing", summary="s", created_at="2026-01-01T00:00:00Z"):
    return {
        "competitor_name": competitor_name,
        "source_type": source_type,
        "theme": theme,
        "summary": summary,
        "created_at": created_at,
    }


def test_judge_correlation_returns_true():
    fake_client = _FakeOpenAIClient({"correlated": True})
    result = judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    assert result is True


def test_judge_correlation_returns_false():
    fake_client = _FakeOpenAIClient({"correlated": False})
    result = judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    assert result is False


def test_judge_correlation_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient({"correlated": True})
    judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["correlated"]
    assert schema["additionalProperties"] is False


def test_judge_correlation_includes_every_signal_evidence_in_prompt():
    fake_client = _FakeOpenAIClient({"correlated": True})
    cluster = [
        _cluster_signal(source_type="pricing_page", theme="pricing", summary="Price rose to $750"),
        _cluster_signal(source_type="job_board", theme="hiring", summary="Posted 3 enterprise AE roles", created_at="2026-01-04T00:00:00Z"),
    ]
    judge_correlation(cluster, client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "Price rose to $750" in user_message
    assert "Posted 3 enterprise AE roles" in user_message
    assert "pricing_page" in user_message
    assert "job_board" in user_message


def test_judge_correlation_raises_clear_error_on_refusal():
    fake_client = _FakeOpenAIClient({"correlated": True})
    fake_client.chat.completions.create = lambda **kwargs: _FakeResponse(
        _FakeMessage(content=None, refusal="cannot assess this content")
    )
    with pytest.raises(RuntimeError, match="refused"):
        judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
```

- [ ] **Step 6: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlator.py -v`
Expected: the 5 new `judge_correlation` tests fail — `judge_correlation` doesn't exist as an importable name in the test file's `from correlator import judge_correlation` line until this exact import is added (it will actually succeed at import since Step 3 already defined `judge_correlation` — re-read: Step 3 already wrote the real implementation, so these tests should PASS immediately). Since `judge_correlation` was already implemented in Step 3 alongside `cluster_by_time_window`, this step instead confirms all 12 tests pass together.

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlator.py -v`
Expected: 12 passed.

- [ ] **Step 7: Commit**

```bash
git add correlator.py tests/test_correlator.py
git commit -m "feat: add correlator.py (time-window clustering + LLM correlation judgment)"
```

---

### Task 3: `correlate.py` orchestrator

**Files:**
- Create: `correlate.py`
- Test: `tests/test_correlate.py`

**Interfaces:**
- Consumes: `db.get_material_signals`, `db.get_signal_ids_with_insight`, `db.get_snapshot`, `db.get_source`, `db.assign_correlation_group` (Task 1); `correlator.cluster_by_time_window`, `correlator.judge_correlation` (Task 2); `llm.get_client`.
- Produces: `correlate.run(client, openai_client=None) -> list[dict]` and `correlate.format_line(r: dict) -> str`, both consumed only by this file's own `__main__` block (no later task imports `correlate.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_correlate.py`:

```python
import uuid

import correlate


def _material_signal(id_, snapshot_id, created_at, theme="pricing", summary="s", correlation_group_id=None):
    return {
        "id": id_,
        "snapshot_id": snapshot_id,
        "diff_text": "[-a-] {+b+}",
        "theme": theme,
        "summary": summary,
        "created_at": created_at,
        "correlation_group_id": correlation_group_id,
    }


def _snapshot(id_, source_id):
    return {"id": id_, "source_id": source_id, "content_hash": "h", "fetched_at": "2026-01-01T00:00:00Z"}


def _source(id_, competitor_id, source_type="pricing_page"):
    return {"id": id_, "url": "https://a.test", "competitor_id": competitor_id, "source_type": source_type}


def _competitor(id_, name="Sonar"):
    return {"id": id_, "name": name}


def test_no_pending_signals_returns_empty_list(monkeypatch):
    monkeypatch.setattr(correlate, "get_material_signals", lambda c: [])
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    assert correlate.run(client=None) == []


def test_already_insighted_signal_is_excluded(monkeypatch):
    monkeypatch.setattr(
        correlate, "get_material_signals", lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z")]
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: {"sig1"})
    called = []
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: called.append(1))
    assert correlate.run(client=None) == []
    assert called == []


def test_already_decided_signal_is_excluded(monkeypatch):
    # non-null correlation_group_id (whether shared or self-referential)
    # means correlate.py already decided this one — must never reconsider it
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z", correlation_group_id="sig1")],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    called = []
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: called.append(1))
    assert correlate.run(client=None) == []
    assert called == []


def test_singleton_signal_gets_self_assigned_group_id_no_llm_call(monkeypatch):
    monkeypatch.setattr(
        correlate, "get_material_signals", lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z")]
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source("src1", "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    llm_called = []
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: llm_called.append(1))
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(signal_ids): group_id}),
    )

    results = correlate.run(client=None)

    assert llm_called == []
    assert assigned == {("sig1",): "sig1"}
    assert results == [
        {"competitor_id": "comp1", "signal_ids": ["sig1"], "status": "singleton", "group_id": "sig1", "error": None}
    ]


def test_two_signals_within_window_get_grouped_when_llm_says_correlated(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-03T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: True)
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(sorted(signal_ids)): group_id}),
    )

    results = correlate.run(client=None)

    assert len(results) == 1
    assert results[0]["status"] == "grouped"
    assert sorted(results[0]["signal_ids"]) == ["sig1", "sig2"]
    group_id = results[0]["group_id"]
    assert uuid.UUID(group_id)  # a real uuid was generated
    assert assigned == {("sig1", "sig2"): group_id}


def test_two_signals_left_ungrouped_when_llm_says_not_correlated(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-03T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: False)
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(signal_ids): group_id}),
    )

    results = correlate.run(client=None)

    assert len(results) == 1
    assert results[0]["status"] == "ungrouped"
    assert sorted(results[0]["signal_ids"]) == ["sig1", "sig2"]
    # each signal gets its OWN id, not a shared one — two separate calls
    assert assigned == {("sig1",): "sig1", ("sig2",): "sig2"}


def test_judge_correlation_error_does_not_stop_other_competitors(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("bad1", "snap-bad1", "2026-01-01T00:00:00Z", summary="bad-marker"),
            _material_signal("bad2", "snap-bad2", "2026-01-02T00:00:00Z", summary="bad-marker"),
            _material_signal("good1", "snap-good1", "2026-01-01T00:00:00Z", summary="good-marker"),
            _material_signal("good2", "snap-good2", "2026-01-02T00:00:00Z", summary="good-marker"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())

    def fake_get_snapshot(c, sid):
        source_id = "src-bad" if "bad" in sid else "src-good"
        return _snapshot(sid, source_id)

    def fake_get_source(c, sid):
        competitor_id = "comp-bad" if "bad" in sid else "comp-good"
        return _source(sid, competitor_id)

    monkeypatch.setattr(correlate, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(correlate, "get_source", fake_get_source)
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))

    def fake_judge(cluster, client=None):
        if cluster[0]["summary"] == "bad-marker":
            raise RuntimeError("rate limited")
        return True

    monkeypatch.setattr(correlate, "judge_correlation", fake_judge)

    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(sorted(signal_ids)): group_id}),
    )

    results = correlate.run(client=None)

    bad = next(r for r in results if r["competitor_id"] == "comp-bad")
    good = next(r for r in results if r["competitor_id"] == "comp-good")
    assert bad["status"] == "error"
    assert bad["error"] is not None
    assert good["status"] == "grouped"
    assert ("good1", "good2") in assigned


def test_error_message_is_never_empty(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-02T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))

    def fake_judge(cluster, client=None):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(correlate, "judge_correlation", fake_judge)

    results = correlate.run(client=None)

    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


def test_format_line_variants():
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1", "s2"], "status": "grouped", "group_id": "g1", "error": None}
    ) == "GROUPED   c1: 2 signals (g1)"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1", "s2"], "status": "ungrouped", "group_id": None, "error": None}
    ) == "UNGROUPED c1: 2 signals (not correlated)"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1"], "status": "singleton", "group_id": "s1", "error": None}
    ) == "SINGLETON c1: 1 signal"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1"], "status": "error", "group_id": None, "error": "boom"}
    ) == "ERROR     c1: boom"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlate.py -v`
Expected: `ModuleNotFoundError: No module named 'correlate'`.

- [ ] **Step 3: Write `correlate.py`**

```python
import sys
import uuid

from dotenv import load_dotenv

from correlator import cluster_by_time_window, judge_correlation
from db import (
    get_client as get_supabase_client,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_source,
    get_competitor,
    assign_correlation_group,
)
from llm import get_client as get_openai_client


def run(client, openai_client=None) -> list[dict]:
    results = []
    already_insighted = get_signal_ids_with_insight(client)
    pending = [
        s
        for s in get_material_signals(client)
        if s["id"] not in already_insighted and s["correlation_group_id"] is None
    ]

    # Resolve each signal's competitor and source_type up front — cluster_by_time_window
    # and judge_correlation both need competitor_name/source_type already attached,
    # and grouping-by-competitor needs competitor_id regardless of cluster size.
    by_competitor: dict[str, list[dict]] = {}
    for signal in pending:
        try:
            snapshot = get_snapshot(client, signal["snapshot_id"])
            source = get_source(client, snapshot["source_id"])
            competitor = get_competitor(client, source["competitor_id"])
            competitor_id = source["competitor_id"]
            enriched = {**signal, "competitor_name": competitor["name"], "source_type": source["source_type"]}
            by_competitor.setdefault(competitor_id, []).append(enriched)
        except Exception as e:
            results.append(
                {
                    "competitor_id": None,
                    "signal_ids": [signal["id"]],
                    "status": "error",
                    "group_id": None,
                    "error": f"{type(e).__name__}: {e}",
                }
            )

    for competitor_id, comp_signals in by_competitor.items():
        for cluster in cluster_by_time_window(comp_signals):
            signal_ids = [s["id"] for s in cluster]
            if len(cluster) == 1:
                assign_correlation_group(client, signal_ids, signal_ids[0])
                results.append(
                    {
                        "competitor_id": competitor_id,
                        "signal_ids": signal_ids,
                        "status": "singleton",
                        "group_id": signal_ids[0],
                        "error": None,
                    }
                )
                continue
            try:
                correlated = judge_correlation(cluster, client=openai_client)
            except Exception as e:
                results.append(
                    {
                        "competitor_id": competitor_id,
                        "signal_ids": signal_ids,
                        "status": "error",
                        "group_id": None,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
                continue
            if correlated:
                group_id = str(uuid.uuid4())
                assign_correlation_group(client, signal_ids, group_id)
                results.append(
                    {
                        "competitor_id": competitor_id,
                        "signal_ids": signal_ids,
                        "status": "grouped",
                        "group_id": group_id,
                        "error": None,
                    }
                )
            else:
                # Each signal gets its OWN id — a decision, not a group.
                # Never a shared id: they were judged NOT to belong together.
                for signal_id in signal_ids:
                    assign_correlation_group(client, [signal_id], signal_id)
                results.append(
                    {
                        "competitor_id": competitor_id,
                        "signal_ids": signal_ids,
                        "status": "ungrouped",
                        "group_id": None,
                        "error": None,
                    }
                )
    return results


def format_line(r: dict) -> str:
    if r["error"] is not None:
        return f"ERROR     {r['competitor_id']}: {r['error']}"
    elif r["status"] == "grouped":
        return f"GROUPED   {r['competitor_id']}: {len(r['signal_ids'])} signals ({r['group_id']})"
    elif r["status"] == "ungrouped":
        return f"UNGROUPED {r['competitor_id']}: {len(r['signal_ids'])} signals (not correlated)"
    else:
        return f"SINGLETON {r['competitor_id']}: 1 signal"


if __name__ == "__main__":
    load_dotenv()
    openai_client = get_openai_client()  # precondition: fail fast, before touching any competitor
    supabase_client = get_supabase_client()
    try:
        results = run(supabase_client, openai_client=openai_client)
        for r in results:
            print(format_line(r))
        sys.exit(1 if any(r["error"] is not None for r in results) else 0)
    finally:
        openai_client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_correlate.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run the whole suite to confirm nothing else broke**

Run: `PYTHONPATH=.deps python3 -m pytest tests/ -v`
Expected: all tests pass (the pre-existing suite plus Task 2's and this task's new tests).

- [ ] **Step 6: Commit**

```bash
git add correlate.py tests/test_correlate.py
git commit -m "feat: add correlate.py orchestrator"
```

---

### Task 4: `scorer.py` — accept a list of signals (breaking change)

**Files:**
- Modify: `scorer.py` (whole file)
- Modify: `tests/test_scorer.py` (whole file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `scorer.score_signal(signals: list[dict], client: OpenAI | None = None) -> dict` — **replaces** the old single-dict signature. Consumed by Task 5's modified `score.py`.

- [ ] **Step 1: Rewrite `tests/test_scorer.py` for the list-based signature (RED first)**

Replace the whole file:

```python
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


SAMPLE_SIGNAL = {
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
    result = score_signal([SAMPLE_SIGNAL], client=fake_client)
    assert result == {"materiality_score": 8, "confidence": "high", "rationale": "Price rose from $500 to $750."}


def test_score_signal_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient({"materiality_score": 3, "confidence": "medium", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL], client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.1"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["materiality_score", "confidence", "rationale"]
    assert schema["additionalProperties"] is False
    assert "minimum" not in schema["properties"]["materiality_score"]


def test_score_signal_includes_diff_and_context_in_prompt():
    fake_client = _FakeOpenAIClient({"materiality_score": 8, "confidence": "high", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL], client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "[-$500-] {+$750+}" in user_message
    assert "Sonar" in user_message
    assert "pricing_page" in user_message


def test_score_signal_with_multiple_signals_includes_every_signals_evidence():
    second_signal = {
        "diff_text": "[-hiring 1-] {+hiring 5+}",
        "theme": "hiring",
        "summary": "Posted 3 enterprise AE roles",
        "competitor_name": "Sonar",
        "source_type": "job_board",
    }
    fake_client = _FakeOpenAIClient({"materiality_score": 9, "confidence": "high", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL, second_signal], client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "[-$500-] {+$750+}" in user_message
    assert "[-hiring 1-] {+hiring 5+}" in user_message
    assert "Posted 3 enterprise AE roles" in user_message
    assert "pricing_page" in user_message
    assert "job_board" in user_message
    # competitor is stated once, not duplicated per signal
    assert user_message.count("Sonar") == 1


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
        score_signal([SAMPLE_SIGNAL], client=_RefusalClient())
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_scorer.py -v`
Expected: every test fails — `score_signal([SAMPLE_SIGNAL], ...)` doesn't match the old single-dict signature (`TypeError` or a prompt built from the wrong shape, failing the content assertions).

- [ ] **Step 3: Rewrite `scorer.py`**

Replace the whole file:

```python
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
    "assessment for a product marketing or sales team. You are given one "
    "or more signals — changes already classified as material — from a "
    "competitor's public webpages, including the exact diff and a brief "
    "summary for each.\n\n"
    "Score how materially this change (or, if there is more than one "
    "signal, this combined development) affects sales conversations or "
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
    "the diff.\n\n"
    "You may be given more than one signal. If so, they were flagged as "
    "describing the same underlying business story — write one rationale "
    "that speaks to the group as a whole, citing evidence from each "
    "signal that contributed to your score, not just the first one."
)


def score_signal(signals: list[dict], client: OpenAI | None = None) -> dict:
    if client is None:
        client = get_client()
    blocks = []
    for i, s in enumerate(signals, start=1):
        blocks.append(
            f"Signal {i}:\n"
            f"Source type: {s['source_type']}\n"
            f"Theme: {s['theme']}\n"
            f"Summary: {s['summary']}\n\n"
            f"Diff:\n{s['diff_text']}"
        )
    user_content = f"Competitor: {signals[0]['competitor_name']}\n\n" + "\n\n".join(blocks)
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
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scorer.py tests/test_scorer.py
git commit -m "refactor: scorer.score_signal accepts a list of signals"
```

---

### Task 5: `score.py` — group by `correlation_group_id`

**Files:**
- Modify: `score.py` (whole file)
- Modify: `tests/test_score.py` (whole file)

**Interfaces:**
- Consumes: `db.get_material_signals` (now includes `correlation_group_id`, Task 1), `scorer.score_signal` (list-based, Task 4). Unchanged: `db.get_signal_ids_with_insight`, `db.get_snapshot`, `db.get_source`, `db.get_competitor`, `db.insert_insight`, `db.insert_insight_signal`.
- Produces: `score.run` and `score.format_line` keep their existing signatures and return shapes (still one result dict per **signal**, even though signals sharing a group share one `materiality_score`/`error`) — no downstream task depends on new return shape.

- [ ] **Step 1: Rewrite `tests/test_score.py` (RED first)**

Replace the whole file:

```python
import pytest

import score


def _material_signal(id_, snapshot_id, diff_text="[-a-] {+b+}", theme="pricing", summary="s", correlation_group_id=None):
    return {
        "id": id_,
        "snapshot_id": snapshot_id,
        "diff_text": diff_text,
        "theme": theme,
        "summary": summary,
        "correlation_group_id": correlation_group_id,
    }


def _snapshot(id_, source_id):
    return {"id": id_, "source_id": source_id, "content_hash": "h", "fetched_at": "2026-01-01T00:00:00Z"}


def _source(id_, competitor_id, source_type="pricing_page"):
    return {"id": id_, "url": "https://a.test", "competitor_id": competitor_id, "source_type": source_type}


def _competitor(id_, name):
    return {"id": id_, "name": name}


def test_no_pending_signals_returns_empty_list(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    assert score.run(client=None) == []


def test_already_insighted_signal_is_skipped(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: {"sig1"})
    called = []
    monkeypatch.setattr(score, "score_signal", lambda *a, **k: called.append(1))
    results = score.run(client=None)
    assert results == []
    assert called == []


def test_null_group_signal_is_scored_as_its_own_singleton(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source("src1", "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    score_calls = []
    monkeypatch.setattr(
        score,
        "score_signal",
        lambda signals, client=None: score_calls.append(signals)
        or {"materiality_score": 8, "confidence": "high", "rationale": "Real cited evidence"},
    )
    inserted_insight = {}
    monkeypatch.setattr(
        score,
        "insert_insight",
        lambda c, competitor_id, materiality_score, confidence, rationale: inserted_insight.update(
            competitor_id=competitor_id, materiality_score=materiality_score, confidence=confidence, rationale=rationale
        )
        or {"id": "insight1"},
    )
    linked = []
    monkeypatch.setattr(
        score,
        "insert_insight_signal",
        lambda c, insight_id, signal_id: linked.append((insight_id, signal_id)) or {"id": "link1"},
    )

    results = score.run(client=None)

    assert results == [{"signal_id": "sig1", "status": "scored", "materiality_score": 8, "error": None}]
    assert len(score_calls) == 1
    assert len(score_calls[0]) == 1  # singleton group: one signal in the list passed to score_signal
    assert inserted_insight == {
        "competitor_id": "comp1",
        "materiality_score": 8,
        "confidence": "high",
        "rationale": "Real cited evidence",
    }
    assert linked == [("insight1", "sig1")]


def test_two_different_null_group_signals_are_scored_as_two_separate_insights(monkeypatch):
    # The sharpest bug risk in this grouping logic: two signals that both
    # happen to have correlation_group_id=None must NEVER be merged into
    # one group just because they share the same "ungrouped" state.
    monkeypatch.setattr(
        score,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", summary="first"),
            _material_signal("sig2", "snap2", summary="second"),
        ],
    )
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    score_calls = []
    monkeypatch.setattr(
        score,
        "score_signal",
        lambda signals, client=None: score_calls.append(signals)
        or {"materiality_score": 5, "confidence": "medium", "rationale": "x"},
    )
    insight_ids = iter(["insight-a", "insight-b"])
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: {"id": next(insight_ids)})
    linked = []
    monkeypatch.setattr(
        score, "insert_insight_signal", lambda c, insight_id, signal_id: linked.append((insight_id, signal_id)) or {}
    )

    results = score.run(client=None)

    assert len(results) == 2
    assert len(score_calls) == 2  # two separate score_signal calls
    assert all(len(call) == 1 for call in score_calls)  # each a singleton group
    assert linked == [("insight-a", "sig1"), ("insight-b", "sig2")]


def test_grouped_signals_produce_one_insight_linked_to_all(monkeypatch):
    monkeypatch.setattr(
        score,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", diff_text="[-$500-] {+$750+}", correlation_group_id="group-x"),
            _material_signal("sig2", "snap2", diff_text="[-hiring 1-] {+hiring 5+}", correlation_group_id="group-x"),
        ],
    )
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    score_calls = []
    monkeypatch.setattr(
        score,
        "score_signal",
        lambda signals, client=None: score_calls.append(signals)
        or {"materiality_score": 9, "confidence": "high", "rationale": "Combined story"},
    )
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: {"id": "insight-group"})
    linked = []
    monkeypatch.setattr(
        score, "insert_insight_signal", lambda c, insight_id, signal_id: linked.append((insight_id, signal_id)) or {}
    )

    results = score.run(client=None)

    assert len(score_calls) == 1
    assert len(score_calls[0]) == 2  # both signals passed together to score_signal
    assert {s["diff_text"] for s in score_calls[0]} == {"[-$500-] {+$750+}", "[-hiring 1-] {+hiring 5+}"}
    assert sorted(results, key=lambda r: r["signal_id"]) == [
        {"signal_id": "sig1", "status": "scored", "materiality_score": 9, "error": None},
        {"signal_id": "sig2", "status": "scored", "materiality_score": 9, "error": None},
    ]
    assert sorted(linked) == [("insight-group", "sig1"), ("insight-group", "sig2")]


def test_scoring_error_does_not_stop_other_groups(monkeypatch):
    monkeypatch.setattr(
        score,
        "get_material_signals",
        lambda c: [
            _material_signal("bad", "snap-bad", summary="bad-marker"),
            _material_signal("good", "snap-good", summary="good-marker"),
        ],
    )
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())

    def fake_get_snapshot(c, sid):
        return _snapshot(sid, "src-bad" if sid == "snap-bad" else "src-good")

    monkeypatch.setattr(score, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))

    def fake_score_signal(signals, client=None):
        if signals[0]["summary"] == "bad-marker":
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
    assert inserted == ["good"]


def test_error_message_is_never_empty(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source("src1", "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))

    def fake_score_signal(signals, client=None):
        raise TimeoutError()

    monkeypatch.setattr(score, "score_signal", fake_score_signal)
    results = score.run(client=None)
    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


def test_unresolvable_source_fails_clearly(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src-missing"))

    def fake_get_source(c, sid):
        raise RuntimeError(f"source {sid} not found")

    monkeypatch.setattr(score, "get_source", fake_get_source)
    results = score.run(client=None)
    assert results[0]["status"] == "error"
    assert "src-missing" in results[0]["error"]
    assert "not found" in results[0]["error"]


def test_out_of_range_score_fails_fast_before_the_database_round_trip(monkeypatch):
    monkeypatch.setattr(score, "get_material_signals", lambda c: [_material_signal("sig1", "snap1")])
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source("src1", "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(
        score, "score_signal", lambda signals, client=None: {"materiality_score": 11, "confidence": "high", "rationale": "x"}
    )
    insert_calls = []
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: insert_calls.append(1) or {"id": "should-not-exist"})
    results = score.run(client=None)
    assert results[0]["status"] == "error"
    assert "11" in results[0]["error"]
    assert insert_calls == []


def test_partial_link_failure_in_a_group_names_linked_and_failed_signals(monkeypatch):
    monkeypatch.setattr(
        score,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", correlation_group_id="group-x"),
            _material_signal("sig2", "snap2", correlation_group_id="group-x"),
        ],
    )
    monkeypatch.setattr(score, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(score, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(score, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(score, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(
        score, "score_signal", lambda signals, client=None: {"materiality_score": 8, "confidence": "high", "rationale": "x"}
    )
    monkeypatch.setattr(score, "insert_insight", lambda c, *a, **k: {"id": "orphan-insight-123"})

    def flaky_link(c, insight_id, signal_id):
        if signal_id == "sig2":
            raise RuntimeError("connection reset")
        return {"id": "link1"}

    monkeypatch.setattr(score, "insert_insight_signal", flaky_link)

    results = score.run(client=None)

    assert len(results) == 2
    for r in results:
        assert r["status"] == "error"
        assert "orphan-insight-123" in r["error"]
        assert "sig1" in r["error"]  # names what already linked
        assert "sig2" in r["error"]  # names what failed


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
Expected: multiple failures — `score.py` still scores per-signal, not per-group (e.g. `test_grouped_signals_produce_one_insight_linked_to_all` fails because `score_calls` has 2 entries instead of 1; `score_signal` is still being called with a single dict, not a list, so several tests error on the shape mismatch).

- [ ] **Step 3: Rewrite `score.py`**

Replace the whole file:

```python
import sys

from dotenv import load_dotenv

from db import (
    get_client as get_supabase_client,
    get_material_signals,
    get_signal_ids_with_insight,
    get_snapshot,
    get_source,
    get_competitor,
    insert_insight,
    insert_insight_signal,
)
from llm import get_client as get_openai_client
from scorer import score_signal


def run(client, openai_client=None) -> list[dict]:
    results = []
    already_insighted = get_signal_ids_with_insight(client)
    pending = [s for s in get_material_signals(client) if s["id"] not in already_insighted]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for signal in pending:
        # A null correlation_group_id means correlate.py never decided this
        # signal (never ran, or errored on it) — falls back to the signal's
        # OWN id as its group key, so it becomes its own singleton group.
        # Two different null-group signals therefore get two different
        # fallback keys (their own distinct ids), never merged together.
        key = signal["correlation_group_id"] or signal["id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(signal)

    for key in order:
        group_signals = groups[key]
        signal_ids = [s["id"] for s in group_signals]
        try:
            contexts = []
            competitor_id = None
            for signal in group_signals:
                snapshot = get_snapshot(client, signal["snapshot_id"])
                # Resolved by id directly, not filtered through active-only
                # sources: a source deactivated after generating a signal
                # must not permanently wedge that signal's scoring.
                source = get_source(client, snapshot["source_id"])
                competitor = get_competitor(client, source["competitor_id"])
                competitor_id = source["competitor_id"]
                contexts.append(
                    {
                        "diff_text": signal["diff_text"],
                        "theme": signal["theme"],
                        "summary": signal["summary"],
                        "competitor_name": competitor["name"],
                        "source_type": source["source_type"],
                    }
                )
            result = score_signal(contexts, client=openai_client)
            if not 1 <= result["materiality_score"] <= 10:
                raise ValueError(f"materiality_score {result['materiality_score']} outside 1-10")
            insight = insert_insight(
                client,
                competitor_id,
                result["materiality_score"],
                result["confidence"],
                result["rationale"],
            )
            linked = []
            try:
                for signal_id in signal_ids:
                    insert_insight_signal(client, insight["id"], signal_id)
                    linked.append(signal_id)
            except Exception as link_error:
                unlinked = [sid for sid in signal_ids if sid not in linked]
                raise RuntimeError(
                    f"insight {insight['id']} created and linked to {linked} but failed to link "
                    f"{unlinked[0]}: {type(link_error).__name__}: {link_error}"
                ) from link_error
            for signal_id in signal_ids:
                results.append(
                    {
                        "signal_id": signal_id,
                        "status": "scored",
                        "materiality_score": result["materiality_score"],
                        "error": None,
                    }
                )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            for signal_id in signal_ids:
                results.append({"signal_id": signal_id, "status": "error", "materiality_score": None, "error": err})
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

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_score.py -v`
Expected: 12 passed (10 test functions plus `test_format_line`'s 2 parametrized cases).

- [ ] **Step 5: Run the whole suite**

Run: `PYTHONPATH=.deps python3 -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add score.py tests/test_score.py
git commit -m "refactor: score.py groups signals by correlation_group_id before scoring"
```

---

### Task 6: Live verification against real Supabase + OpenAI

**Files:** none created or modified — this task seeds and cleans up live data only.

**Interfaces:**
- Consumes: `correlate.py`, `score.py` (Tasks 3 and 5) run for real against the live Supabase project and a real OpenAI call.

- [ ] **Step 1: Seed a second, inactive source for Sonar**

Cross-source correlation needs signals from more than one `source_type`. There is only one real source (the pricing page). Add a second, inactive one purely as a scratch fixture — `is_active = false` so `ingest.py` never touches it in a real run:

```sql
insert into sources (competitor_id, source_type, url, is_active)
values ((select id from competitors where name = 'Sonar'), 'job_board', 'https://sonar.software/careers', false)
returning id;
```

- [ ] **Step 2: Seed three scratch snapshots (one per signal)**

Using the pricing-page source id (from Task 1's Step 5 query, or re-query `select id from sources where source_type = 'pricing_page'`) and the new job-board source id from Step 1:

```sql
insert into snapshots (source_id, content, content_hash, fetched_at)
values ('<pricing-page-source-id>', 'scratch content A', 'scratch-hash-task6-a', now())
returning id;

insert into snapshots (source_id, content, content_hash, fetched_at)
values ('<job-board-source-id>', 'scratch content B', 'scratch-hash-task6-b', now() + interval '2 days')
returning id;

insert into snapshots (source_id, content, content_hash, fetched_at)
values ('<pricing-page-source-id>', 'scratch content C', 'scratch-hash-task6-c', now() + interval '15 days')
returning id;
```

- [ ] **Step 3: Seed three material signals with a deliberately obvious correlated pair (A, B) and one clear outlier (C)**

```sql
insert into signals (snapshot_id, classification, theme, summary, diff_text, created_at)
values (
  '<snapshot-A-id>', 'material', 'pricing',
  'Sonar raised its starting price from $500 to $1,250 per month',
  '[-Starting at $500 per month-] {+Starting at $1,250 per month-}',
  now()
)
returning id;

insert into signals (snapshot_id, classification, theme, summary, diff_text, created_at)
values (
  '<snapshot-B-id>', 'material', 'hiring',
  'Sonar posted 4 new "Enterprise Account Executive" roles emphasizing larger deal sizes',
  '[-2 open sales roles-] {+6 open sales roles, including 4 Enterprise Account Executive+}',
  now() + interval '2 days'
)
returning id;

insert into signals (snapshot_id, classification, theme, summary, diff_text, created_at)
values (
  '<snapshot-C-id>', 'material', 'features',
  'Sonar added a new CSV export option to its dashboard',
  '[-No export options-] {+Added CSV export+}',
  now() + interval '15 days'
)
returning id;
```

Signal A and B are 2 days apart (within the 7-day window) and deliberately describe a textbook "price increase + enterprise sales hiring push" story — the kind of pair a competitive intelligence analyst would treat as one connected development. Signal C is 15 days after A (well outside the window) and about an unrelated theme — guaranteed to be its own cluster regardless of the model's judgment.

- [ ] **Step 4: Run `correlate.py` against the live project**

```bash
cd <repo root>
PYTHONPATH=.deps python3 correlate.py
```

Expected: exit code 0, one `GROUPED` or `UNGROUPED` line for the A/B cluster (2 signals) and one `SINGLETON` line for C. If the A/B line is `GROUPED` (the expected, designed-for outcome given how obviously connected the seeded story is), continue to Step 5. If the model judges it `UNGROUPED` — a genuine live model-judgment outcome, not a bug — note this in the plan's ledger as a Ruling, and re-run Step 4 after checking Supabase (`correlation_group_id` on A and B should each now be their own id, since a rejection is a permanent decision) is not an option for re-testing the same pair (idempotency means they won't be reconsidered); instead, seed a fresh, even more obviously-connected pair (e.g. an explicit price increase alongside an explicit "raising prices to fund enterprise expansion" hiring post) as new scratch signals and retry from Step 3.

- [ ] **Step 5: Verify grouping in the database**

```sql
select id, summary, correlation_group_id from signals where summary like 'Sonar%' order by created_at;
```

Expected: signal A and signal B share the same non-null `correlation_group_id`; signal C's is a different non-null value equal to its own `id` (self-referential singleton marker).

- [ ] **Step 6: Re-run `correlate.py` to confirm idempotency**

```bash
PYTHONPATH=.deps python3 correlate.py
```

Expected: prints nothing and exits 0 — all three signals already have a non-null `correlation_group_id`, so none are reconsidered (no LLM call made this time; if verifying this precisely matters, check `read_network_requests`-equivalent isn't available here, so instead just confirm the correlation_group_id values from Step 5 are byte-for-byte unchanged after this re-run).

- [ ] **Step 7: Run `score.py` against the live project**

```bash
PYTHONPATH=.deps python3 score.py
```

Expected: exit code 0, two `SCORED` lines (one for signal C alone, and the two group-A/B result lines sharing one `materiality_score` — three `SCORED` lines total: one per signal, matching `score.py`'s per-signal result shape).

- [ ] **Step 8: Verify insight creation in the database**

```sql
select i.id, i.materiality_score, i.confidence, i.rationale, array_agg(s.summary) as linked_signal_summaries
from insights i
join insight_signals ins on ins.insight_id = i.id
join signals s on s.id = ins.signal_id
where i.competitor_id = (select id from competitors where name = 'Sonar')
group by i.id, i.materiality_score, i.confidence, i.rationale
order by i.created_at;
```

Expected: exactly 2 rows. One insight linked to exactly 2 signal summaries (the price-increase and hiring summaries) with a rationale that references both. One insight linked to exactly 1 signal summary (the CSV export one).

- [ ] **Step 9: Clean up all scratch data**

```sql
delete from insight_signals where signal_id in (select id from signals where summary like 'Sonar%' and diff_text like '%Starting at%' or diff_text like '%export%' or diff_text like '%Enterprise Account Executive%');
delete from insights where id not in (select insight_id from insight_signals);
delete from signals where content_hash is null and summary in (
  'Sonar raised its starting price from $500 to $1,250 per month',
  'Sonar posted 4 new "Enterprise Account Executive" roles emphasizing larger deal sizes',
  'Sonar added a new CSV export option to its dashboard'
);
```

This is fragile as a single script — instead, capture every id returned by the `insert` statements in Steps 1-3 while executing them, and delete by those exact ids, in dependency order: `insight_signals` rows first, then `insights`, then `signals`, then `snapshots`, then the scratch `sources` row from Step 1. Verify with:

```sql
select count(*) from sources where url = 'https://sonar.software/careers';
```

Expected: `0`.

- [ ] **Step 10: Record this task's completion**

No pytest command applies (this task ran live verification, not unit tests) — the completion evidence is Steps 4-9's outputs above. Note in the ledger: `Task 6: complete (live verification against Supabase project wbjptxjrujyzmsjldwwo — correlate.py grouped 2/3 signals, score.py created 2 insights, idempotency re-run confirmed, all scratch data cleaned up)`.

---

### Task 7: Document usage in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Running correlation" section**

Insert after the existing "## Running classification" section and before "## Running materiality scoring":

```markdown
## Running correlation

```bash
PYTHONPATH=.deps python3 correlate.py
```

For each competitor, groups `material` signals with no insight yet into candidate clusters using a fixed 7-day time window from the earliest ungrouped signal, then — for any cluster of 2+ — asks `gpt-5.4-mini` a binary question: do these signals describe the same underlying business story? If yes, they share a `correlation_group_id` so `score.py` creates one insight for the group instead of one per signal. If no, or if the cluster only had one signal to begin with, each signal is marked decided with its own id (so it is never reconsidered) and gets scored on its own. Prints one line per cluster: `GROUPED <competitor>: N signals (<group_id>)`, `UNGROUPED <competitor>: N signals (not correlated)`, `SINGLETON <competitor>: 1 signal`, or `ERROR <competitor>: <message>`. Exit code is `1` if any cluster errored, `0` otherwise. Needs `OPENAI_API_KEY` in `.env` (same key `classify.py`/`score.py` use).

Run this before `score.py` — it's what lets `score.py` bundle related signals into one insight instead of scoring each in isolation.
```

- [ ] **Step 2: Update the "Running materiality scoring" section's opening line to reflect grouping**

Find the existing line (currently under `## Running materiality scoring`):

```
For each `material` signal with no insight yet, scores it 1-10 for materiality (via `gpt-5.1`), assigns a confidence label (`high`/`medium`/`low`/`needs_review`), writes a citation-grounded rationale, and creates the `insights` row (`status` stays at its default `pending` — nothing here approves or publishes) plus the `insight_signals` link.
```

Replace with:

```
Groups `material` signals with no insight yet by `correlation_group_id` (set by `correlate.py` — a signal with no group of its own, e.g. because `correlate.py` was never run, is scored alone), and for each group scores it 1-10 for materiality (via `gpt-5.1`), assigns a confidence label (`high`/`medium`/`low`/`needs_review`), writes a citation-grounded rationale covering every signal in the group, and creates one `insights` row (`status` stays at its default `pending` — nothing here approves or publishes) linked to every signal in the group via `insight_signals`.
```

- [ ] **Step 3: Verify**

Run: `grep -q "Running correlation" README.md && grep -q "correlation_group_id" README.md && echo "README documents correlation"`
Expected: `README documents correlation`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document running correlate.py and score.py's grouping"
```
