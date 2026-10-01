# Deferred idea: source link in the review app

**Status:** not approved, not scheduled. Parked for possible future pickup.

**Original request:** add a link to the page that produced each piece of evidence
in the review app, so the reviewer can check the source directly. A second
request for a change-history view (date, description, link) was raised at the
same time but was dropped — not part of this idea.

**Classification:** bounded (small change to existing code — `review.py` /
`review_data.py` / `db.py` — not a new subsystem).

## Design (presented, not yet approved for implementation)

Add, under each piece of evidence in the "Evidence (raw diff)" section, a link
to the source page that produced it (`sources.url`).

The data already exists — it just isn't threaded through today:

- `signals.snapshot_id` → `snapshots.source_id` → `sources.url`.

Changes:

- `db.get_signal`: add `snapshot_id` to its `select`.
- `review_data.get_review_queue`: for each signal, call
  `get_snapshot(client, signal["snapshot_id"])` then
  `get_source(client, snapshot["source_id"])`, and include `source_url` in the
  signal dict it already builds.
- `review.py`: in `render_header_and_evidence`, add
  `st.markdown(f"[Ver fuente]({signal['source_url']})")` next to each piece of
  evidence.

**Testing:** TDD in `review_data.py` (it already has its own test file) — a
test asserting `get_review_queue` includes `source_url` per signal, with
`get_snapshot`/`get_source` monkeypatched. No UI test for `review.py` (no file
in this repo has one today); would need manual verification in the deployed
app.

**Files touched (if implemented):** `db.py`, `review_data.py`, `review.py`,
and their existing tests. No schema changes.
