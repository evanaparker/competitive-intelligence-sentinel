# Human Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Streamlit app where a human can see every `pending` insight with its raw evidence, approve or reject it, and optionally leave quality feedback — end to end, provably working against the live Supabase project and driven through the Browser pane.

**Architecture:** Five new `db.py` functions (live-verified, no local test harness, consistent with every prior sub-project). `review_data.py` holds the actual logic — assembling the review queue and submitting a decision — with full TDD against monkeypatched `db.py` calls, following `score.py`'s pattern including its two-write failure-mode split. `review.py` is a thin Streamlit layer with no pytest coverage by design; it's verified by actually running it and driving it through the Browser pane, the first UI-based verification in this project (every prior sub-project verified a CLI script).

**Tech Stack:** Python 3, `streamlit` (new), plus the existing `httpx`, `beautifulsoup4`, `supabase`, `openai`, `python-dotenv`, `pytest`.

**Spec:** [docs/superpowers/specs/2026-09-25-human-feedback-loop-design.md](../specs/2026-09-25-human-feedback-loop-design.md)

## Global Constraints

- Approve/Reject (`insights.status` -> `approved`/`rejected`) is the one required action per insight; quality feedback (`useful`/`not_useful`/`incorrect` + optional comment) is optional and independent.
- `submit_review()`'s two writes (status update, feedback insert) are sequential and independently reported — a feedback-insert failure after a successful status update must never be reported as if the whole review failed, and must never trigger a redundant re-submission of the status update.
- The raw evidence (`signals.diff_text`) is always shown alongside the rationale — the reviewer must be able to check the LLM's claim against the actual diff, not just read prose.
- No deployment in this sub-project — built and verified locally only (see spec's Non-goals).
- `review.py` gets no pytest coverage; `review_data.py` does. This split is deliberate, not a gap.

## Review Focus

- The raw evidence (`diff_text`) must be genuinely rendered on the page for a pending insight, not just the LLM-authored rationale — checked by reading the actual page content through the Browser pane, not assumed from the code. → tested in Task 3 (live).
- Approve/Reject must work with no feedback rating given at all — feedback is optional, never blocking. → tested in Task 2 (unit) and Task 3 (live).
- A feedback-insert failure occurring after a successful status update must be reported as `status_updated: True, feedback_saved: False` (partial success), not as a blanket failure — and the caller must be able to tell this apart from a failure of the status update itself (`status_updated: False`). → tested in Task 2 (unit, both failure modes exercised separately).
- Once approved or rejected, an insight must actually leave the pending queue — `get_pending_insights`'s `status = 'pending'` filter must genuinely exclude it on the next read, not just in the UI's own state. → tested in Task 3 (live, by querying Supabase directly after the click, not just trusting the rerendered page).
- An insight linked to more than one signal (forward-compatible with a future correlation sub-project) must have every linked signal's evidence assembled, not just the first one. → tested in Task 2 (unit, two signals).

---

## File Structure

- Modify: `requirements.txt` — add `streamlit`.
- Modify: `db.py` — add `get_pending_insights`, `get_signal_ids_for_insight`, `get_signal`, `update_insight_status`, `insert_feedback`.
- Create: `review_data.py` / `tests/test_review_data.py`
- Create: `review.py` (the Streamlit app)
- Create: `run_review_app.sh` (wrapper so `PYTHONPATH=.deps` is set for the `streamlit run` process — the Browser pane's `preview_start` launches a command, not a shell with env vars pre-set)
- Create: `.claude/launch.json` — registers the app as a named dev server for `preview_start`.
- Modify: `README.md` — add a "Running the review app" section.

---

### Task 1: `db.py` additions

**Files:**
- Modify: `db.py`

**Interfaces:**
- Produces: `get_pending_insights(client) -> list[dict]`, `get_signal_ids_for_insight(client, insight_id) -> list[str]`, `get_signal(client, signal_id) -> dict`, `update_insight_status(client, insight_id, status) -> dict`, `insert_feedback(client, insight_id, rating, comment=None) -> dict` — all five consumed by `review_data.py` in Task 2.

- [ ] **Step 1: Add the five functions**

Append to `db.py`:

```python
def get_pending_insights(client: Client) -> list[dict]:
    response = (
        client.table("insights")
        .select("id, competitor_id, materiality_score, confidence, rationale, created_at")
        .eq("status", "pending")
        .order("materiality_score", desc=True)
        .execute()
    )
    return response.data


def get_signal_ids_for_insight(client: Client, insight_id: str) -> list[str]:
    response = (
        client.table("insight_signals")
        .select("signal_id")
        .eq("insight_id", insight_id)
        .execute()
    )
    return [row["signal_id"] for row in response.data]


def get_signal(client: Client, signal_id: str) -> dict:
    response = (
        client.table("signals")
        .select("id, diff_text, theme, summary, classification")
        .eq("id", signal_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"signal {signal_id} not found")
    return response.data[0]


def update_insight_status(client: Client, insight_id: str, status: str) -> dict:
    response = (
        client.table("insights")
        .update({"status": status})
        .eq("id", insight_id)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"insight {insight_id} not found")
    return response.data[0]


def insert_feedback(client: Client, insight_id: str, rating: str, comment: str | None = None) -> dict:
    response = (
        client.table("feedback")
        .insert({"insight_id": insight_id, "rating": rating, "comment": comment})
        .execute()
    )
    return response.data[0]
```

- [ ] **Step 2: Live-verify against the real project**

Write a scratch file `verify_db_review.py` at the repo root (not committed — delete it at the end of this step). Seeds two pending insights (different scores, to verify ordering) on real existing data, exercises all five functions, then cleans up:

```python
# verify_db_review.py (scratch, delete after running)
from dotenv import load_dotenv
load_dotenv()

from db import (
    get_client,
    get_active_sources,
    get_pending_insights,
    get_signal_ids_for_insight,
    get_signal,
    update_insight_status,
    insert_feedback,
)

client = get_client()
source = get_active_sources(client)[0]
snapshot_id = (
    client.table("snapshots").select("id").eq("source_id", source["id"]).limit(1).execute()
).data[0]["id"]


def make_signal(summary):
    return (
        client.table("signals")
        .insert(
            {
                "snapshot_id": snapshot_id,
                "diff_text": f"[-old-] {{+{summary}+}}",
                "classification": "material",
                "theme": "pricing",
                "summary": summary,
            }
        )
        .execute()
    ).data[0]


def make_insight(score, rationale):
    return (
        client.table("insights")
        .insert(
            {
                "competitor_id": source["competitor_id"],
                "materiality_score": score,
                "confidence": "high",
                "rationale": rationale,
            }
        )
        .execute()
    ).data[0]


sig_low = make_signal("Low priority scratch signal for Task 1")
sig_high = make_signal("High priority scratch signal for Task 1")
insight_low = make_insight(3, "Scratch low-priority insight for Task 1")
insight_high = make_insight(9, "Scratch high-priority insight for Task 1")
client.table("insight_signals").insert({"insight_id": insight_low["id"], "signal_id": sig_low["id"]}).execute()
client.table("insight_signals").insert({"insight_id": insight_high["id"], "signal_id": sig_high["id"]}).execute()

pending = get_pending_insights(client)
pending_ids = [p["id"] for p in pending]
assert insight_low["id"] in pending_ids
assert insight_high["id"] in pending_ids
# Ordering: materiality_score descending, so the high-priority one comes first
# among these two (there may be other pending insights from other testing —
# just check relative order of these two).
assert pending_ids.index(insight_high["id"]) < pending_ids.index(insight_low["id"])

signal_ids = get_signal_ids_for_insight(client, insight_high["id"])
assert signal_ids == [sig_high["id"]]
signal = get_signal(client, sig_high["id"])
assert signal["summary"] == "High priority scratch signal for Task 1"

updated = update_insight_status(client, insight_high["id"], "approved")
assert updated["status"] == "approved"
assert insight_high["id"] not in {p["id"] for p in get_pending_insights(client)}

feedback = insert_feedback(client, insight_high["id"], "useful", "test comment")
assert feedback["rating"] == "useful"
assert feedback["comment"] == "test comment"

print("db.py review functions live verification passed")
```

Run: `PYTHONPATH=.deps python3 verify_db_review.py`
Expected: prints `db.py review functions live verification passed`.

- [ ] **Step 3: Clean up the scratch data**

Call the Supabase MCP `execute_sql` tool with `project_id: "wbjptxjrujyzmsjldwwo"`:

```sql
delete from feedback where comment = 'test comment';
delete from insight_signals where signal_id in (
  select id from signals where summary in (
    'Low priority scratch signal for Task 1', 'High priority scratch signal for Task 1'
  )
);
delete from insights where rationale in (
  'Scratch low-priority insight for Task 1', 'Scratch high-priority insight for Task 1'
);
delete from signals where summary in (
  'Low priority scratch signal for Task 1', 'High priority scratch signal for Task 1'
);
```

Then delete the scratch file: `rm verify_db_review.py`.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "Add review data-access functions to db.py"
```

---

### Task 2: `review_data.py`

**Files:**
- Create: `review_data.py`
- Test: `tests/test_review_data.py`

**Interfaces:**
- Consumes: `get_pending_insights`, `get_signal_ids_for_insight`, `get_signal`, `get_competitor`, `update_insight_status`, `insert_feedback` (Task 1, plus `get_competitor` from sub-project 4).
- Produces: `get_review_queue(client) -> list[dict]`, `submit_review(client, insight_id, decision, rating=None, comment=None) -> dict` — both consumed by `review.py` in Task 3.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_data.py
import review_data


def _insight(id_, competitor_id, score=8, confidence="high", rationale="r"):
    return {
        "id": id_,
        "competitor_id": competitor_id,
        "materiality_score": score,
        "confidence": confidence,
        "rationale": rationale,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _competitor(id_, name):
    return {"id": id_, "name": name}


def _signal(id_, diff_text="[-a-] {+b+}", theme="pricing", summary="s"):
    return {"id": id_, "diff_text": diff_text, "theme": theme, "summary": summary, "classification": "material"}


def test_get_review_queue_assembles_full_context(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [_insight("ins1", "comp1")])
    monkeypatch.setattr(review_data, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(review_data, "get_signal_ids_for_insight", lambda c, iid: ["sig1"])
    monkeypatch.setattr(review_data, "get_signal", lambda c, sid: _signal("sig1"))

    queue = review_data.get_review_queue(client=None)

    assert queue == [
        {
            "insight_id": "ins1",
            "competitor_name": "Sonar",
            "materiality_score": 8,
            "confidence": "high",
            "rationale": "r",
            "signals": [{"diff_text": "[-a-] {+b+}", "theme": "pricing", "summary": "s"}],
        }
    ]


def test_get_review_queue_handles_multiple_signals_per_insight(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [_insight("ins1", "comp1")])
    monkeypatch.setattr(review_data, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(review_data, "get_signal_ids_for_insight", lambda c, iid: ["sig1", "sig2"])
    monkeypatch.setattr(review_data, "get_signal", lambda c, sid: _signal(sid, summary=f"summary-{sid}"))

    queue = review_data.get_review_queue(client=None)

    assert len(queue[0]["signals"]) == 2
    assert {s["summary"] for s in queue[0]["signals"]} == {"summary-sig1", "summary-sig2"}


def test_get_review_queue_returns_empty_list_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [])
    assert review_data.get_review_queue(client=None) == []


def test_submit_review_approves_without_feedback(monkeypatch):
    updated = {}
    monkeypatch.setattr(
        review_data,
        "update_insight_status",
        lambda c, iid, status: updated.update(insight_id=iid, status=status) or {"id": iid, "status": status},
    )
    called_feedback = []
    monkeypatch.setattr(review_data, "insert_feedback", lambda *a, **k: called_feedback.append(1))

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved")

    assert result == {"status_updated": True, "feedback_saved": None, "error": None}
    assert updated == {"insight_id": "ins1", "status": "approved"}
    assert called_feedback == []


def test_submit_review_with_feedback_saves_both(monkeypatch):
    monkeypatch.setattr(review_data, "update_insight_status", lambda c, iid, status: {"id": iid, "status": status})
    fed = {}
    monkeypatch.setattr(
        review_data,
        "insert_feedback",
        lambda c, iid, rating, comment=None: fed.update(insight_id=iid, rating=rating, comment=comment)
        or {"id": "fb1"},
    )

    result = review_data.submit_review(
        client=None, insight_id="ins1", decision="rejected", rating="incorrect", comment="wrong evidence"
    )

    assert result == {"status_updated": True, "feedback_saved": True, "error": None}
    assert fed == {"insight_id": "ins1", "rating": "incorrect", "comment": "wrong evidence"}


def test_submit_review_status_update_failure_never_attempts_feedback(monkeypatch):
    def fail_update(c, iid, status):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(review_data, "update_insight_status", fail_update)
    called_feedback = []
    monkeypatch.setattr(review_data, "insert_feedback", lambda *a, **k: called_feedback.append(1))

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved", rating="useful")

    assert result["status_updated"] is False
    assert result["feedback_saved"] is None
    assert result["error"] is not None
    assert called_feedback == []


def test_submit_review_feedback_failure_after_successful_status_update(monkeypatch):
    monkeypatch.setattr(review_data, "update_insight_status", lambda c, iid, status: {"id": iid, "status": status})

    def fail_feedback(c, iid, rating, comment=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(review_data, "insert_feedback", fail_feedback)

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved", rating="useful")

    assert result["status_updated"] is True
    assert result["feedback_saved"] is False
    assert result["error"] is not None


def test_error_message_is_never_empty_on_status_failure(monkeypatch):
    def fail_update(c, iid, status):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(review_data, "update_insight_status", fail_update)

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved")

    assert result["error"] != ""
    assert "TimeoutError" in result["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_review_data.py -v`
Expected: collection error — `review_data.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# review_data.py
from db import (
    get_pending_insights,
    get_signal_ids_for_insight,
    get_signal,
    get_competitor,
    update_insight_status,
    insert_feedback,
)


def get_review_queue(client) -> list[dict]:
    queue = []
    for insight in get_pending_insights(client):
        competitor = get_competitor(client, insight["competitor_id"])
        signal_ids = get_signal_ids_for_insight(client, insight["id"])
        signals = [get_signal(client, sid) for sid in signal_ids]
        queue.append(
            {
                "insight_id": insight["id"],
                "competitor_name": competitor["name"],
                "materiality_score": insight["materiality_score"],
                "confidence": insight["confidence"],
                "rationale": insight["rationale"],
                "signals": [
                    {"diff_text": s["diff_text"], "theme": s["theme"], "summary": s["summary"]} for s in signals
                ],
            }
        )
    return queue


def submit_review(
    client, insight_id: str, decision: str, rating: str | None = None, comment: str | None = None
) -> dict:
    try:
        update_insight_status(client, insight_id, decision)
    except Exception as e:
        return {"status_updated": False, "feedback_saved": None, "error": f"{type(e).__name__}: {e}"}

    if rating is None:
        return {"status_updated": True, "feedback_saved": None, "error": None}

    try:
        insert_feedback(client, insight_id, rating, comment)
        return {"status_updated": True, "feedback_saved": True, "error": None}
    except Exception as e:
        return {"status_updated": True, "feedback_saved": False, "error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_review_data.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add review_data.py tests/test_review_data.py
git commit -m "Add review queue and submission logic"
```

---

### Task 3: `review.py` — the Streamlit app

**Files:**
- Modify: `requirements.txt`
- Create: `review.py`
- Create: `run_review_app.sh`
- Create: `.claude/launch.json`

**Interfaces:**
- Consumes: `get_review_queue`, `submit_review` (Task 2); `get_client` (existing `db.py`).
- Produces: nothing further downstream — this is the plan's final deliverable.

- [ ] **Step 1: Add the dependency and install**

Append to `requirements.txt`:

```
streamlit==1.40.2
```

```bash
pip3 install --target=.deps -r requirements.txt
```

Verify: `PYTHONPATH=.deps python3 -c "import streamlit; print(streamlit.__version__)"`
Expected: prints `1.40.2`.

- [ ] **Step 2: Confirm how to invoke it**

`pip install --target` doesn't create console scripts on `PATH` (confirmed in sub-project 2 for `pytest`) — there is almost certainly no bare `streamlit` command available. Confirm the module-invocation form works:

Run: `PYTHONPATH=.deps timeout 5 python3 -m streamlit run --server.headless true /dev/null 2>&1 | head -20`

Expected: some Streamlit output (a startup banner, or an error about the script — `/dev/null` isn't a real app, this is only checking that `python3 -m streamlit` itself is recognized and runs, not that it fully starts). If this reports "No module named streamlit" or similar, something about Step 1's install needs fixing before continuing. If it instead behaves like a real (if content-free) Streamlit process attempting to start, the invocation form is confirmed — use `python3 -m streamlit run <app>.py` in every step below.

- [ ] **Step 3: Write `review.py`**

```python
# review.py
import streamlit as st
from dotenv import load_dotenv

from db import get_client
from review_data import get_review_queue, submit_review

load_dotenv()

st.title("Competitive Intelligence Sentinel — Insight Review")

client = get_client()
queue = get_review_queue(client)

if not queue:
    st.info("Nothing to review — no pending insights.")
else:
    for item in queue:
        with st.container(border=True):
            st.subheader(
                f"{item['competitor_name']} — materiality {item['materiality_score']}/10 ({item['confidence']})"
            )
            st.write(item["rationale"])
            with st.expander("Evidence (raw diff)", expanded=True):
                for signal in item["signals"]:
                    st.caption(f"Theme: {signal['theme']}")
                    st.code(signal["diff_text"])
                    st.write(signal["summary"])

            rating = st.selectbox(
                "Feedback (optional)",
                ["", "useful", "not_useful", "incorrect"],
                key=f"rating_{item['insight_id']}",
            )
            comment = st.text_input("Comment (optional)", key=f"comment_{item['insight_id']}")

            col1, col2 = st.columns(2)
            if col1.button("Approve", key=f"approve_{item['insight_id']}"):
                result = submit_review(
                    client, item["insight_id"], "approved", rating=rating or None, comment=comment or None
                )
                if not result["status_updated"]:
                    st.error(f"Failed to approve: {result['error']}")
                elif result["feedback_saved"] is False:
                    st.warning(f"Approved, but feedback wasn't saved: {result['error']}")
                else:
                    st.success("Approved.")
                    st.rerun()
            if col2.button("Reject", key=f"reject_{item['insight_id']}"):
                result = submit_review(
                    client, item["insight_id"], "rejected", rating=rating or None, comment=comment or None
                )
                if not result["status_updated"]:
                    st.error(f"Failed to reject: {result['error']}")
                elif result["feedback_saved"] is False:
                    st.warning(f"Rejected, but feedback wasn't saved: {result['error']}")
                else:
                    st.success("Rejected.")
                    st.rerun()
```

- [ ] **Step 4: Write the launcher wrapper**

`preview_start` (the Browser pane's dev-server tool) runs a command directly — it doesn't source a shell profile, so `PYTHONPATH=.deps` needs to be set inside the invoked process itself, not assumed from the calling shell:

```bash
# run_review_app.sh
#!/bin/bash
export PYTHONPATH=.deps
exec python3 -m streamlit run review.py --server.port 8501 --server.headless true
```

```bash
chmod +x run_review_app.sh
```

- [ ] **Step 5: Register it for `preview_start`**

Create `.claude/launch.json` (or add to it, if it already exists — check first):

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "review-app",
      "runtimeExecutable": "bash",
      "runtimeArgs": ["run_review_app.sh"],
      "port": 8501
    }
  ]
}
```

- [ ] **Step 6: Seed a real pending insight to review**

Call the Supabase MCP `execute_sql` tool with `project_id: "wbjptxjrujyzmsjldwwo"`:

```sql
insert into signals (snapshot_id, diff_text, classification, theme, summary)
select id, '[-Starting at $500 per month-] {+Starting at $1,250 per month-}', 'material', 'pricing', 'Review app live test signal'
from snapshots
order by fetched_at desc
limit 1
returning id;
```

Note the returned signal id, then:

```sql
insert into insights (competitor_id, materiality_score, confidence, rationale)
select competitor_id, 7, 'high', 'Sonar raised its starting price from $500 to $1,250 per month, a 2.5x increase — worth flagging to sales.'
from sources where is_active = true limit 1
returning id;
```

Note the returned insight id, then:

```sql
insert into insight_signals (insight_id, signal_id) values ('<insight-id-from-above>', '<signal-id-from-above>');
```

- [ ] **Step 7: Launch and verify the page renders the evidence (Review Focus item 1)**

Use `preview_start` with `{"name": "review-app"}`. Once it's up, use `get_page_text` (or `read_page`) to confirm the rendered page contains:
- The competitor name and `materiality 7/10`
- The rationale text
- The raw diff text `Starting at $500 per month` and `Starting at $1,250 per month` — this is the check that matters: the evidence must be genuinely on the page, not just the LLM's rationale summarizing it.

If `preview_start` fails to launch (a wrong flag, a port conflict, a missing Streamlit dependency), diagnose from `preview_logs` and fix `run_review_app.sh`/`review.py` rather than working around it — this is the one part of this plan without prior verification in this project (first Streamlit app), same caveat as sub-project 3's first OpenAI call.

- [ ] **Step 8: Approve it and verify Review Focus items 2 and 4**

Use `find` to locate the "Approve" button and the feedback selectbox; select `useful` as the feedback rating, then click Approve (`computer` with the button's ref or coordinates).

Then verify directly against Supabase (not just by re-reading the page) via `execute_sql`:

```sql
select status from insights where rationale like 'Sonar raised its starting price from $500 to $1,250%';
```

Expected: `status = 'approved'`. Also check feedback was saved:

```sql
select rating, comment from feedback where insight_id = (
  select id from insights where rationale like 'Sonar raised its starting price from $500 to $1,250%'
);
```

Expected: one row, `rating = 'useful'`.

Re-read the app page (reload or re-fetch): the insight should no longer appear (Review Focus item 4 — it actually left the pending queue, not just visually).

- [ ] **Step 9: Clean up the live-test data**

Call `execute_sql`:

```sql
delete from feedback where insight_id in (
  select id from insights where rationale like 'Sonar raised its starting price from $500 to $1,250%'
);
delete from insight_signals where insight_id in (
  select id from insights where rationale like 'Sonar raised its starting price from $500 to $1,250%'
);
delete from insights where rationale like 'Sonar raised its starting price from $500 to $1,250%';
delete from signals where summary = 'Review app live test signal';
```

Stop the preview server (`preview_stop`).

- [ ] **Step 10: Commit**

```bash
git add requirements.txt review.py run_review_app.sh .claude/launch.json
git commit -m "Add the insight review Streamlit app"
```

---

### Task 4: Document usage in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Running the review app" section**

```markdown
## Running the review app

```bash
PYTHONPATH=.deps python3 -m streamlit run review.py
```

Shows every `pending` insight (highest materiality first) with its competitor, score, confidence, rationale, and the raw evidence (the underlying signal's diff) it's based on. Approve or Reject moves the insight out of the queue; an optional feedback rating (`useful`/`not_useful`/`incorrect`) and comment can be left alongside either decision — feedback is not required and a failure saving it is reported separately from the approve/reject decision itself, which still stands.

Not deployed anywhere yet — runs locally, same as every other script in this repo so far.
```

- [ ] **Step 2: Verify**

Run: `grep -q "Running the review app" README.md && echo "README documents the review app"`
Expected: `README documents the review app`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the review app"
```
