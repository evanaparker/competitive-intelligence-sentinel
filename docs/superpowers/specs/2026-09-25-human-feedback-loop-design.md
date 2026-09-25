# Human Feedback Loop Design (PoC)

Status: Approved
Date: 2026-09-25
Scope: Sub-project 5 of the Competitive Intelligence Sentinel PoC — a Streamlit app for reviewing pending insights: approve/reject (`insights.status`) and optional quality feedback (`feedback`). Covers PRD pipeline stage 6 (Human Feedback Loop) only.

## Context

This builds on sub-project 4's materiality scoring, which creates `insights` rows with `status = 'pending'` and no way to move them out of that state, and on the `feedback` table from sub-project 1's schema, which has never been written to. This sub-project is the first UI component in the codebase — every prior sub-project was a CLI script (`ingest.py`, `classify.py`, `score.py`). It is also the first to touch two related-but-distinct mechanisms the data model already separates: `insights.status` (the approval gate — nothing before this sub-project can move an insight out of `pending`) and `feedback` (a quality signal, `useful`/`not_useful`/`incorrect` + optional comment, intended to eventually tune materiality scoring — that tuning itself is not built and is not in scope here).

## Goals

- A human can see every `pending` insight with enough context to judge it without leaving the app: the competitor, the score, the confidence, the rationale, **and the raw evidence** (the underlying signal's `diff_text`) — so the reviewer can verify the rationale against the actual diff, not just trust the LLM's prose. This is the PRD's anti-hallucination requirement applied to the review step itself.
- Approving or rejecting an insight is the one required action; it updates `insights.status` and removes the insight from the queue.
- Giving quality feedback (`useful`/`not_useful`/`incorrect` + an optional comment) is optional and independent of the approve/reject decision.
- The two writes involved (`insights.status` update, `feedback` insert) are handled so that a failure of the optional second write is reported distinctly from a failure of the required first write — approving/rejecting must not silently appear to fail just because an optional feedback comment didn't save.
- Provably working: seeded against the live Supabase project, driven through the Browser pane (this sub-project's equivalent of the prior sub-projects' live API/DB verification), not just unit-tested logic.

## Non-goals (explicitly out of scope for this sub-project)

- Deploying the app anywhere (Streamlit Community Cloud or otherwise). Like every prior sub-project, this one is built and verified locally, in this session; deployment for the whole pipeline (this app plus a GitHub Actions schedule for `ingest.py`/`classify.py`/`score.py`) is a separate infrastructure sub-project, once all PRD stages have a working implementation.
- Using `feedback` to actually tune materiality scoring — the `feedback` table gets written to for the first time here, but nothing reads it back yet. That's implied future work, not this sub-project's job.
- Reviewing anything other than `pending` insights — no view of already-approved/rejected/published insights, no way to change a decision after submitting it.
- Authentication or multi-reviewer identity — matches the data model's existing single-user assumption from sub-project 1 (no reviewer column on `feedback`).
- Cross-source correlation display (showing multiple signals grouped under one insight) — every insight today links to exactly one signal (sub-project 4's scope), so the UI shows one signal's evidence per insight; the `review_data.get_review_queue()` interface still returns a list of signals per insight (not a single signal) so it doesn't need to change shape when a future correlation sub-project starts producing multi-signal insights.

## Architecture

```
review_data.py   — pure logic: assemble the review queue, submit a decision
   |                (mockable/testable with TDD, same pattern as score.py)
   v
review.py        — the Streamlit app itself, a thin layer: calls
                     review_data.py, renders, handles button clicks.
                     Verified by actually running it and driving it
                     through the Browser pane — not pytest.
```

Run with `streamlit run review.py` from the repo root — or, if this environment's dependency-install approach (`pip install --target=.deps`, no working venv) turns out not to expose a `streamlit` console script the same way it didn't expose one for `pytest` in prior sub-projects, whatever invocation form is confirmed to work during implementation (e.g. `python3 -m streamlit run review.py`). This is confirmed empirically in the implementation plan, the same way sub-project 2 confirmed its own dependency-install workaround.

## Module Interfaces

```python
# db.py (new functions, added to the existing module)
def get_pending_insights(client) -> list[dict]:
    """Insights with status = 'pending', ordered by materiality_score
    descending (most important first): id, competitor_id,
    materiality_score, confidence, rationale, created_at."""

def get_signal_ids_for_insight(client, insight_id: str) -> list[str]:
    """Every signal_id linked to this insight via insight_signals."""

def get_signal(client, signal_id: str) -> dict:
    """id, diff_text, theme, summary, classification for one signal."""

def update_insight_status(client, insight_id: str, status: str) -> dict:
    """Update insights.status (also bumps updated_at via the column's
    own semantics) and return the updated row."""

def insert_feedback(client, insight_id: str, rating: str, comment: str | None = None) -> dict:
    """Insert a feedback row and return it."""

# review_data.py
def get_review_queue(client) -> list[dict]:
    """Assembles each pending insight with its competitor name and its
    linked signals' evidence:
    [{"insight_id": str, "competitor_name": str, "materiality_score": int,
    "confidence": str, "rationale": str,
    "signals": [{"diff_text": str, "theme": str, "summary": str}, ...]},
    ...], ordered by materiality_score descending."""

def submit_review(client, insight_id: str, decision: str, rating: str | None = None, comment: str | None = None) -> dict:
    """decision: "approved" | "rejected" (required). Updates
    insights.status first — this is the action that must succeed for the
    review to count as done. If rating is given, then tries to insert
    feedback as a second, independent step. Returns
    {"status_updated": bool, "feedback_saved": bool | None,
    "error": str | None} — feedback_saved is None when no rating was
    given (nothing attempted). A failure updating status is reported as
    status_updated: False, error: <message>, and feedback is never
    attempted. A failure inserting feedback (status update already
    succeeded) is reported as status_updated: True, feedback_saved: False,
    error: <message> — the caller can tell the two failure modes apart
    and must not retry a successful status update."""

# review.py
# Streamlit app. Calls get_review_queue() to render the pending list;
# on Approve/Reject (with optional rating/comment fields), calls
# submit_review() and reruns to refresh the queue.
```

## Error Handling

- **No pending insights**: `get_review_queue()` returns `[]`; the app shows a "nothing to review" message rather than an empty page with no explanation.
- **`submit_review()`'s status update fails**: reported as `status_updated: False`; feedback is never attempted (there's nothing to attach it to from the reviewer's point of view — the app shows the failure and leaves the insight in the queue for the reviewer to retry).
- **`submit_review()`'s feedback insert fails after the status update succeeded**: reported as `status_updated: True, feedback_saved: False`. The app must show this as "your approve/reject decision was saved, but the feedback comment wasn't" — not as a blanket failure, and must not resubmit the status update on retry (that would be redundant, not harmful, since `insights.status` is idempotent to set to the same value again, but the UI shouldn't imply the whole action failed when it mostly succeeded).
- **Missing `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`**: same precondition check as every prior sub-project's `db.get_client()` — fails immediately, before the app renders anything useful.

## Testing

- `review_data.py`: unit tests with `db.py` calls monkeypatched, following `score.py`'s test pattern — including the two-write failure-mode split (status-update failure vs. feedback-insert failure reported distinctly), matching the lesson already carried forward from sub-project 4's orphaned-insight handling.
- `db.py`'s five new functions: no local test harness for the live Supabase dependency (consistent with every prior sub-project); verified by running against the real project.
- `review.py`: no pytest coverage — Streamlit apps don't separate cleanly enough from their rendering to unit-test meaningfully, and the thin-layer split above is specifically designed so the only untested code is UI wiring, not logic. Verified live: seed a real pending insight, run the app, drive it through the Browser pane (read the rendered evidence, click Approve/Reject, optionally submit feedback), and confirm the resulting Supabase state directly.
