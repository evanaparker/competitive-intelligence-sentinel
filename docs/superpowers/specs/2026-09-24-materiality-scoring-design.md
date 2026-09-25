# Materiality Scoring & Rationale Design (PoC)

Status: Approved
Date: 2026-09-24
Scope: Sub-project 4 of the Competitive Intelligence Sentinel PoC — scores material signals for materiality (1-10), assigns a confidence label, writes a plain-language rationale, and creates the corresponding `insights` row. Covers PRD pipeline stage 5 (Materiality Scoring & Rationale Generation) only.

## Context

This builds on sub-project 1's data model, sub-project 2's ingestion pipeline, and sub-project 3's signal classification, which already produces `signals` rows labeled `cosmetic` or `material` with a `diff_text` and a short `summary`. This sub-project is the second to call an LLM: it takes `material` signals with no `insights` row yet, scores how materially each one matters (1-10), assigns a confidence label, writes a citation-grounded rationale, and creates the `insights` row plus the corresponding `insight_signals` link.

PRD stage 4 (Cross-Source Correlation), which groups multiple related signals from different sources into a single compound insight, has not been built. This sub-project treats each material signal as producing exactly one insight — a degenerate single-signal case of the same `insight_signals` many-to-many structure a future correlation sub-project will populate with more than one signal per insight.

## Goals

- For each `material` signal with no existing insight, score it end to end against the live Supabase project and a real `gpt-5.1` call — not a stub.
- `insights.rationale` cites the specific evidence from the signal's `diff_text` — the PRD's anti-hallucination requirement that every insight links back to exact source text, carried forward from sub-project 3's evidence discipline.
- `insights.confidence` is set to `needs_review` when the model itself signals the evidence is ambiguous or insufficient, rather than asserting a confident-sounding score anyway.
- `insights.status` stays at its schema default (`pending`) — nothing in this sub-project auto-approves or publishes an insight. Human review is PRD stage 6, not built yet.
- One signal's scoring failure must not stop the others.
- Consolidate `classifier.py`'s `get_client()` (added in sub-project 3) into a shared `llm.py` module reused by this sub-project's `scorer.py`, rather than duplicating the same `OPENAI_API_KEY` precondition check in two files.

## Non-goals (explicitly out of scope for this sub-project)

- Cross-source correlation (grouping multiple signals into one insight) — PRD stage 4, a later sub-project. Every insight created here links to exactly one signal.
- Human feedback / approval UI, or anything writing to `feedback` — PRD stage 6, a later sub-project.
- Delivery/distribution (Slack, email, CRM/battlecard push) — PRD stage 7, a later sub-project.
- Scoring `cosmetic` signals — only `material` signals produce insights; cosmetic signals already served their purpose by being recorded and classified.
- Backfilling scoring logic changes against already-scored signals — like sub-project 3, only signals without an existing insight are considered.
- Enforcing `materiality_score`'s 1-10 range in the JSON Schema sent to OpenAI — see Classification Prompt below for why this relies on the database's existing `check` constraint instead.

## Architecture

```
signals (classification = 'material', no insight yet)
   │
   ▼
db: look up each signal's snapshot -> source -> competitor (context for the prompt)
   │
   ▼
scorer.score_signal(signal_context)  → gpt-5.1, JSON Schema-constrained output
   │  {"materiality_score": int, "confidence": "high"|"medium"|"low"|"needs_review", "rationale": str}
   ▼
db: insert_insight(...) + insert_insight_signal(insight_id, signal_id)
```

Run with `python score.py` from the repo root, mirroring `classify.py`'s structure and CLI conventions from sub-project 3.

## Module Interfaces

```python
# llm.py (new, shared between classifier.py and scorer.py)
def get_client(timeout: float = 15.0) -> OpenAI:
    """Same OPENAI_API_KEY precondition check classifier.py already has.
    Raises RuntimeError immediately if the key is missing."""

# classifier.py (modified)
# Removes its own get_client() definition; imports it from llm.py instead.
# No behavior change — same precondition check, same call sites.

# scorer.py
def score_signal(signal_context: dict, client: OpenAI | None = None) -> dict:
    """signal_context: {"diff_text": str, "theme": str, "summary": str,
    "competitor_name": str, "source_type": str}. Calls gpt-5.1 with
    response_format json_schema (strict) to force structured output.
    Returns {"materiality_score": int, "confidence": "high"|"medium"|
    "low"|"needs_review", "rationale": str}. Raises on API error (caught
    per-signal by score.run()). client is injectable for tests, matching
    classify_diff's pattern from sub-project 3."""

# db.py (new functions, added to the existing module)
def get_material_signals(client) -> list[dict]:
    """All signals with classification = 'material':
    id, snapshot_id, theme, summary, diff_text."""

def get_signal_ids_with_insight(client) -> set[str]:
    """Every signal_id already present in insight_signals, for filtering
    get_material_signals down to ones still needing an insight."""

def get_snapshot(client, snapshot_id: str) -> dict:
    """id, source_id, content_hash, fetched_at for one snapshot."""

def get_competitor(client, competitor_id: str) -> dict:
    """id, name for one competitor."""

def insert_insight(client, competitor_id: str, materiality_score: int, confidence: str, rationale: str) -> dict:
    """Insert an insights row (status defaults to 'pending') and return it."""

def insert_insight_signal(client, insight_id: str, signal_id: str) -> dict:
    """Insert the insight_signals link row and return it."""

# score.py
def run(client, openai_client=None) -> list[dict]:
    """For each material signal with no existing insight: look up its
    snapshot's source and competitor, score it, then insert_insight +
    insert_insight_signal. Catches any exception per-signal (status
    "error"; nothing written for that signal) without stopping the rest.
    Returns [{"signal_id": str, "status": "scored"|"error",
    "materiality_score": int | None, "error": str | None}, ...].

if __name__ == "__main__":
    ...  # load .env, build clients, call run(), print one line per result
```

`get_active_sources`, already in `db.py` from sub-project 2, supplies each active source's `competitor_id` — `score.py` builds a `source_id -> competitor_id` map from it once per run rather than adding a per-signal source lookup.

## Error Handling

- **No material signals pending, or all already have an insight**: `run()` returns an empty list; the CLI prints nothing and exits 0. Not an error.
- **Scoring call fails** (rate limit, network error, refusal, malformed response): caught per-signal inside `run()`. Recorded as `status: "error"`; no `insights` or `insight_signals` row inserted for that signal. Other signals still get processed.
- **`materiality_score` outside 1-10**: not validated in application code — the `insights.materiality_score` `check` constraint (from sub-project 1's schema) rejects the insert, which surfaces as a normal per-signal error through the same exception handling. No redundant application-level range check.
- **Missing `OPENAI_API_KEY`**: precondition failure for the whole run, not a per-signal error — raised immediately via `llm.get_client()`, before any signal is processed, matching `db.get_client()`'s `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` check and `classify.py`'s equivalent precondition from sub-project 3.

## Classification Prompt

System prompt (English):

> You are a competitive intelligence analyst writing a materiality assessment for a product marketing or sales team. You are given a signal — a change already classified as material — from a competitor's public webpage, including the exact diff and a brief summary.
>
> Score how materially this change affects sales conversations or product strategy, on a 1-10 scale:
> - 1-3: minor — worth having on record, low urgency
> - 4-6: moderate — sales/product should know this week
> - 7-8: significant — could affect active deals or roadmap decisions, notify soon
> - 9-10: urgent — major pricing/positioning/feature shift, likely to come up in live sales calls
>
> Set confidence to "needs_review" if the evidence is ambiguous, incomplete, or you're not confident in your assessment — never invent detail the diff doesn't support. Otherwise use "high", "medium", or "low" based on how clear-cut the signal is.
>
> Write a one-to-two sentence rationale in plain language that a PMM or sales rep could read directly, citing the specific evidence from the diff.

Output schema (forced via OpenAI Structured Outputs, same mechanism sub-project 3 validated against the real API):

```json
{
  "type": "object",
  "properties": {
    "materiality_score": {"type": "integer"},
    "confidence": {"type": "string", "enum": ["high", "medium", "low", "needs_review"]},
    "rationale": {"type": "string"}
  },
  "required": ["materiality_score", "confidence", "rationale"],
  "additionalProperties": false
}
```

`materiality_score` deliberately has no `minimum`/`maximum` keywords — whether OpenAI's strict structured-output mode enforces numeric bounds (as opposed to just structural keywords like `type`/`enum`/`required`) is unconfirmed without a bundled reference for this SDK. The prompt's stated 1-10 scale plus the database's own `check` constraint are the real guardrails; this is verified live in the implementation plan, same as sub-project 3's request shape.

Request parameters: `model="gpt-5.1"`. A stronger, non-mini model than sub-project 3's `gpt-5.4-mini`, per the explicit choice to spend more here — this is the reasoning a PMM or sales rep reads directly and acts on.

## Dependencies

None new — reuses `openai` from sub-project 3.

## Testing

- `scorer.py`: unit tests using a fake/mocked `OpenAI` client (dependency-injected, same pattern as `classifier.py`'s tests from sub-project 3) — no real API calls in the test suite. Live-verified separately against the real API and model.
- `score.py`: unit tests with the DB/LLM calls monkeypatched, following sub-project 3's `classify.py` test pattern (including its own final-review lessons: error messages that are never empty, per-signal error isolation proven by asserting nothing was inserted for the failed signal, not just that its status is "error").
- `db.py`'s six new functions: no local test harness for the live Supabase dependency (consistent with sub-projects 2 and 3); verified by running against the real project. Since no material signal currently exists in the database (the two prior sub-projects' synthetic test data was deliberately cleaned up after verification), live verification seeds a synthetic material signal to exercise the full path — the implementation plan defines exactly how, following the same seed-then-clean-up discipline sub-project 3's final review established.
