# Cross-Source Correlation Design (PoC)

Status: Approved
Date: 2026-09-27
Scope: Sub-project 6 of the Competitive Intelligence Sentinel PoC — groups `material` signals from the same competitor that describe the same underlying business story into a single compound insight, instead of scoring every signal in isolation. Covers PRD pipeline stage 4 (Cross-Source Correlation) only.

## Context

Sub-project 4 (Materiality Scoring) deliberately treated every `material` signal as its own insight — a degenerate single-signal case of `insight_signals`' many-to-many structure, explicitly left for "a future correlation sub-project" to populate with more than one signal per insight. This sub-project is that piece: it sits between `classify.py` and `score.py` in the pipeline, and changes `score.py` from "one insight per signal" to "one insight per group of correlated signals."

The original data model spec (sub-project 1) reserved `signals.embedding` (`vector(1024)`, Voyage-3.5, HNSW index) for this step, as a similarity-search mechanism. That plan predates the project's switch from Anthropic to OpenAI for every LLM call. This sub-project deliberately does **not** use `signals.embedding` — see Non-goals. The column and its index stay in the schema, unused, until a future sub-project revisits this if correlation ever needs to scale past a small time-windowed candidate set.

The live project currently tracks exactly one competitor and one source (a pricing page) — there is no real multi-source data to correlate yet, and `extract.py`/`fetch.py` have no extraction logic for any other `source_type`. This sub-project is built and verified against synthetic signals seeded directly via SQL, the same discipline sub-projects 3-5 used before real data existed for them.

## Goals

- For each competitor, group `material` signals with no insight yet into candidate clusters using a fixed 7-day time window from the earliest ungrouped signal.
- For any candidate cluster of 2+ signals, ask an LLM (`gpt-5.4-mini`) a binary question — do these signals describe the same underlying business story? — and persist a shared `correlation_group_id` on all of them if yes.
- `score.py` creates exactly one insight per group (grouped or singleton) instead of one per signal, with a rationale that speaks to the whole group's combined evidence.
- The step is idempotent: signals already grouped (or already correlated-and-rejected) are never reconsidered by a later run.
- One competitor's or one cluster's correlation failure must not stop the others, and must not block those signals from eventually being scored — a failed correlation attempt degrades to "treat as uncorrelated," not "stuck forever."

## Non-goals (explicitly out of scope for this sub-project)

- **Embedding-based similarity search.** `signals.embedding` stays unused. Correlation candidates come only from the competitor + 7-day time window; the LLM call is the only judgment mechanism. A future sub-project can revisit embeddings if the candidate-generation step ever needs to scale past what a time window can cheaply produce.
- **Partial sub-grouping within a candidate cluster.** The LLM's judgment is binary for the whole candidate cluster: either all of it is one group, or none of it is. A cluster of 3 where only 2 truly correlate is not split — known limitation, see below.
- **Real extraction for additional `source_type`s** (G2, job boards, changelogs, etc.). This sub-project only changes how already-classified signals get grouped and scored; it adds no new ingestion capability. Verified with synthetic seeded signals.
- **Cross-competitor correlation.** Candidate clusters are always scoped to a single competitor's signals.
- **Re-evaluating signals that already have a `correlation_group_id` or an insight.** Matches sub-projects 3-5's "no backfill" precedent.
- **A lock against concurrent runs**, same accepted limitation as `score.py`.

## Architecture

```
signals (classification = 'material', no insight yet, no correlation_group_id yet)
   │
   ▼
group by competitor_id
   │
   ▼
correlator.cluster_by_time_window(signals, window_days=7)  → list[list[signal]]
   │
   ├─ cluster of 1  → leave correlation_group_id NULL (scored as a singleton later)
   │
   └─ cluster of 2+ → correlator.judge_correlation(cluster) → gpt-5.4-mini, JSON Schema-constrained
        │  {"correlated": bool}
        ├─ true  → db: assign a new shared correlation_group_id to every signal in the cluster
        └─ false → leave correlation_group_id NULL on all of them

--- (score.py, modified) ---

signals (classification = 'material', no insight yet)
   │
   ▼
group by correlation_group_id (NULL → the signal's own id, i.e. a singleton group)
   │
   ▼
scorer.score_signal(list_of_signal_contexts)  → gpt-5.1, JSON Schema-constrained output
   │  {"materiality_score": int, "confidence": ..., "rationale": str}
   ▼
db: insert_insight(...) + insert_insight_signal(insight_id, signal_id) for every signal in the group
```

Run with `python correlate.py` (new) before `python score.py` (modified), from the repo root — mirroring the existing `ingest.py` → `classify.py` → `score.py` sequence.

## Module Interfaces

```python
# correlator.py (new, pure logic — no DB/LLM client construction of its own)
def cluster_by_time_window(signals: list[dict], window_days: int = 7) -> list[list[dict]]:
    """signals: [{"id": str, "created_at": str (ISO), ...}], already
    filtered to one competitor, already sorted by created_at ascending.
    Partitions into a list of clusters: starting from the earliest
    remaining signal, every remaining signal within window_days of THAT
    signal's created_at joins the same cluster (a fixed window from the
    cluster's start, not a chained sliding window — so cluster span never
    exceeds window_days). Every input signal appears in exactly one
    output cluster. Order of clusters and of signals within a cluster
    matches input order."""

def judge_correlation(cluster: list[dict], client: OpenAI | None = None) -> bool:
    """cluster: 2+ signals, each a dict with competitor_name, source_type,
    theme, summary, created_at (for the "N days apart" framing in the
    prompt). Calls gpt-5.4-mini with response_format json_schema (strict)
    forcing {"correlated": bool}. Returns that bool. Raises on API error
    (caught per-cluster by correlate.run()). client is injectable for
    tests, matching scorer.score_signal's pattern."""

# correlate.py (new orchestrator)
def run(client, openai_client=None) -> list[dict]:
    """Fetches material signals (db.get_material_signals) filtered in
    Python to those with no insight yet (db.get_signal_ids_with_insight,
    same helper score.py already uses) and no correlation_group_id yet —
    the same "fetch broad, filter in Python" shape score.py already uses
    for its own pending-signal query, not a new db.py read function.
    Resolves each remaining signal's competitor via its snapshot -> source
    (same lookup chain score.py already uses), groups by competitor_id
    (stable order, preserving get_material_signals' created_at ordering),
    and for each competitor's signals calls cluster_by_time_window. For each cluster of size 1:
    records a "singleton" result, no DB write. For each cluster of size
    2+: calls judge_correlation; on True, calls
    db.assign_correlation_group for all signal ids in the cluster with a
    freshly generated uuid; on False, records "ungrouped", no DB write.
    Catches any exception per-cluster (status "error"; no DB write for
    that cluster) without stopping other clusters or other competitors.
    Returns [{"competitor_id": str, "signal_ids": list[str], "status":
    "grouped"|"ungrouped"|"singleton"|"error", "group_id": str | None,
    "error": str | None}, ...].

if __name__ == "__main__":
    ...  # load .env, build clients, call run(), print one line per result

# db.py (modified + new functions)
def get_material_signals(client) -> list[dict]:
    """MODIFIED: select list now also includes created_at and
    correlation_group_id, needed by both correlate.py and score.py.
    Still: id, snapshot_id, theme, summary, diff_text, classification =
    'material' filter, ordered by created_at. (Existing callers unaffected
    — this only adds columns to the same rows already returned.)"""

def assign_correlation_group(client, signal_ids: list[str], group_id: str) -> None:
    """Sets correlation_group_id = group_id on every signal in signal_ids
    (single .in_("id", signal_ids).update(...) call)."""

# scorer.py (MODIFIED — breaking signature change, not additive)
def score_signal(signals: list[dict], client: OpenAI | None = None) -> dict:
    """signals: list of {"diff_text": str, "theme": str, "summary": str,
    "competitor_name": str, "source_type": str} — one entry per signal in
    the group (a singleton group is still a list of length 1). Calls
    gpt-5.1 with response_format json_schema (strict). The prompt lists
    every signal's source_type/theme/summary/diff so the model can reason
    about — and the rationale can cite — evidence across all of them, not
    just one. Returns {"materiality_score": int, "confidence": "high"|
    "medium"|"low"|"needs_review", "rationale": str}. Raises on API error
    (caught per-group by score.run()). client is injectable for tests."""

# score.py (MODIFIED)
def run(client, openai_client=None) -> list[dict]:
    """Fetches material signals with no existing insight
    (get_material_signals filtered by get_signal_ids_with_insight, as
    before), groups them by correlation_group_id — a NULL group_id means
    a singleton group keyed by that signal's own id, so grouping never
    drops a signal. For each group: looks up every signal's snapshot ->
    source -> competitor (same lookup chain as before, now per signal in
    the group), builds the list of signal contexts, calls
    scorer.score_signal(contexts), then insert_insight(...) once plus
    insert_insight_signal(insight_id, signal_id) for every signal in the
    group. Catches any exception per-group (status "error" for every
    signal_id in that group; nothing written for the group) without
    stopping other groups. If insert_insight succeeds but a later
    insert_insight_signal call fails partway through a multi-signal
    group, the error names the insight id, which signal_ids already
    linked successfully, and which one failed — the insight is not
    deleted (same non-atomic-write limitation sub-project 4 already
    documented, now stated for groups of arbitrary size). Returns
    [{"signal_id": str, "status": "scored"|"error", "materiality_score":
    int | None, "error": str | None}, ...] — one entry per signal, even
    though signals in the same group share one materiality_score/error.
```

`db.get_source`/`db.get_competitor`/`db.get_snapshot` (sub-project 4) are reused unchanged by both `correlate.py` and the modified `score.py`.

## Error Handling

- **No material signals pending correlation, or all already grouped/insighted**: `correlate.py`'s `run()` returns an empty list; the CLI prints nothing and exits 0. Not an error.
- **A cluster's `judge_correlation` call fails** (rate limit, network error, refusal, malformed response): caught per-cluster inside `correlate.py`'s `run()`. Recorded as `status: "error"`; no `correlation_group_id` written for that cluster's signals. Other clusters and other competitors still get processed. Those signals remain ungrouped and are picked up by `score.py` as singletons — degradation, not a stall.
- **Missing `OPENAI_API_KEY`**: precondition failure for the whole run, raised immediately via `llm.get_client()`, before any competitor is processed — same as `classify.py`/`score.py`.
- **`score.py`'s per-group scoring failure**: caught per-group, same as sub-project 4's per-signal handling, just scoped to a group. `materiality_score` out-of-range check (sub-project 4's final-review fix) is unchanged — still checked before the database round-trip.
- **Partial `insert_insight_signal` failure within a group**: see Module Interfaces above. The remaining unlinked signals in that group keep their `correlation_group_id`, so the next `score.py` run naturally retries them as a smaller group — no special-case recovery code needed.

## Known Limitations

- **All-or-nothing clustering.** A candidate cluster's correlation judgment is binary for the whole cluster. If 2 of 3 signals in a time window truly tell the same story and the third doesn't, the model may say "correlated: true" and wrongly bundle all three, or "false" and wrongly leave all three as singletons — there is no partial credit. Acceptable for this PoC's scale (clusters are rare and small while there's only one source); a future sub-project could ask the model to partition the cluster instead of judging it as a whole.
- **Fixed window from cluster start, not adaptive.** Two signals 8 days apart never cluster, even if a third signal 4 days after the first would have chained them under a sliding-window design. Chosen deliberately (see Architecture) to keep cluster span bounded and predictable.
- **`signals.embedding` remains unused.** See Non-goals. The column and its HNSW index are schema debt from a superseded plan (Voyage-3.5 embeddings), not exercised by this sub-project.
- **Unbounded queries**, same accepted PoC-scale limitation `get_material_signals`/`get_signal_ids_with_insight` already carry (sub-project 4).
- **No lock against concurrent runs** of either `correlate.py` or `score.py` — same accepted limitation as sub-project 4, now shared by two scripts instead of one.

## Correlation Prompt

System prompt (English):

> You are a competitive intelligence analyst deciding whether several signals about the same competitor, detected within a short time window, describe the same underlying business story — or are unrelated changes that happen to be close in time.
>
> You will be given each signal's source type, theme, a brief summary, and how many days apart it was detected from the others. Decide: do these signals, taken together, describe one coherent story (e.g., a price increase alongside a hiring push for enterprise sales; a new feature announcement alongside matching changelog and pricing-page updates)? Or are they unrelated coincidences?
>
> Answer `true` only if a reasonable analyst would write about these as one connected development. Answer `false` if they are plausibly unrelated, even if they involve the same competitor and theme.

Output schema (forced via OpenAI Structured Outputs):

```json
{
  "type": "object",
  "properties": {
    "correlated": {"type": "boolean"}
  },
  "required": ["correlated"],
  "additionalProperties": false
}
```

Request parameters: `model="gpt-5.4-mini"` — a categorical yes/no judgment, the same complexity class as sub-project 3's cosmetic/material classification, not sub-project 4's deeper reasoning.

## Scoring Prompt (modified from sub-project 4)

Same system prompt and 1-10 scale as sub-project 4, with one addition after the existing instructions:

> You may be given more than one signal. If so, they were flagged as describing the same underlying business story — write one rationale that speaks to the group as a whole, citing evidence from each signal that contributed to your score, not just the first one.

Output schema unchanged from sub-project 4 (`materiality_score`, `confidence`, `rationale` — same JSON Schema, same deliberate absence of `minimum`/`maximum` on `materiality_score`). User content changes from a single diff block to one block per signal in the group, each labeled with its own `source_type`/`theme`/`summary`/diff; `competitor_name` is stated once (every signal in a group shares a competitor by construction).

## Dependencies

None new — reuses `openai` (already a dependency since sub-project 3) and the existing `llm.py` client.

## Testing

- `correlator.py`: unit tests for `cluster_by_time_window` (pure function, no mocking needed — exact partition boundaries, including a signal exactly `window_days` away, and an empty input) and for `judge_correlation` using a fake/mocked `OpenAI` client (dependency-injected, same pattern as `scorer.py`'s tests). No real API calls in the test suite.
- `correlate.py`: unit tests with the DB/LLM calls monkeypatched, following `score.py`'s test pattern — including per-cluster error isolation (one cluster's `judge_correlation` failure doesn't stop another competitor's clusters from being processed) and idempotency (`run()`'s own filtering drops any signal with a non-null `correlation_group_id` before grouping by competitor, so a previously-decided signal is never reclustered).
- `scorer.py`: existing tests (sub-project 4) updated for the new list-based `score_signal` signature — single-signal case (list of 1) and multi-signal case (list of 2+, asserting the prompt includes every signal's evidence).
- `score.py`: existing tests (sub-project 4) updated for grouping by `correlation_group_id`, including a null-group-id signal being scored as its own singleton group, a multi-signal group producing one insight linked to all its signals, and the partial-link-failure error message naming which signals succeeded vs failed.
- `db.py`'s new/modified functions: no local test harness for the live Supabase dependency (consistent with sub-projects 2-5); verified by running against the real project. Since no signal in the live database has ever had more than one candidate correlation partner (only one source is tracked), live verification seeds 2-3 synthetic signals for the same competitor — some within the 7-day window, at least one outside it — the implementation plan defines exactly how, following the same seed-then-clean-up discipline every prior sub-project used.
