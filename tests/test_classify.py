import pytest

import classify


def _source(id_, url, source_type="pricing_page"):
    return {"id": id_, "url": url, "competitor_id": "c1", "source_type": source_type}


def _snapshot(id_, content, content_hash, fetched_at):
    return {"id": id_, "content": content, "content_hash": content_hash, "fetched_at": fetched_at}


def test_skips_when_fewer_than_two_snapshots(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify, "get_snapshots_for_source", lambda c, sid: [_snapshot("sn1", "x", "h1", "2026-01-01T00:00:00Z")]
    )

    results = classify.run(client=None)

    assert results == [
        {"source_url": "https://a.test", "status": "skipped", "reason": "insufficient_history", "classification": None, "error": None}
    ]


def test_skips_when_hash_unchanged_since_baseline(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_snapshots_for_source",
        lambda c, sid: [
            _snapshot("sn2", "same", "h1", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "same", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)
    called = []
    monkeypatch.setattr(classify, "classify_diff", lambda *a, **k: called.append(1))

    results = classify.run(client=None)

    assert results[0]["status"] == "skipped"
    assert results[0]["reason"] == "unchanged"
    assert called == []  # no LLM call made


def test_skips_when_newest_already_classified(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_snapshots_for_source",
        lambda c, sid: [
            _snapshot("sn2", "new", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "old", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: sid == "sn2")
    called = []
    monkeypatch.setattr(classify, "classify_diff", lambda *a, **k: called.append(1))

    results = classify.run(client=None)

    assert results[0]["status"] == "skipped"
    assert results[0]["reason"] == "already_classified"
    assert called == []


def test_classifies_against_first_ever_baseline_when_no_snapshot_has_a_signal(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_snapshots_for_source",
        lambda c, sid: [
            _snapshot("sn3", "newest content", "h3", "2026-01-03T00:00:00Z"),
            _snapshot("sn2", "middle content", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("sn1", "oldest content", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)  # nothing classified yet, ever
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
            snapshot_id=snap_id, prior_snapshot_id=prior_id, diff_text=diff_text, classification=classification, theme=theme, summary=summary
        )
        or {"id": "sig1"},
    )

    results = classify.run(client=None)

    # baseline is the OLDEST snapshot (sn1), not the immediately-prior one (sn2) —
    # this is the "first ever classification" case with no anchor yet
    assert results[0]["status"] == "classified"
    assert inserted["snapshot_id"] == "sn3"
    assert inserted["prior_snapshot_id"] == "sn1"


def test_classifies_against_last_classified_snapshot_even_across_a_gap(monkeypatch):
    # Regression test for the bug where classify.py only ever compared the
    # two most recent snapshots: if an ingest run occurs between two
    # classify.py runs without a change, and the *previous* ingest run did
    # carry a real change, the change must still be found by walking back
    # to the last snapshot that already has a signal — not lost because it
    # is no longer one of "the two most recent".
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_snapshots_for_source",
        lambda c, sid: [
            _snapshot("sn3", "changed content", "h2", "2026-01-03T00:00:00Z"),  # same hash as sn2: no new change here
            _snapshot("sn2", "changed content", "h2", "2026-01-02T00:00:00Z"),  # this is the real (unclassified) change
            _snapshot("sn1", "original content", "h1", "2026-01-01T00:00:00Z"),  # already classified (the anchor)
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: sid == "sn1")
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
            snapshot_id=snap_id, prior_snapshot_id=prior_id, diff_text=diff_text
        )
        or {"id": "sig1"},
    )

    results = classify.run(client=None)

    assert results[0]["status"] == "classified"
    assert inserted["snapshot_id"] == "sn3"
    assert inserted["prior_snapshot_id"] == "sn1"  # baseline walked back past sn2, not "sn2 vs sn3" (which share a hash)
    # diff is between sn1 ("original content") and sn3 ("changed content") —
    # only the differing word appears, "content" is equal so it's omitted
    assert inserted["diff_text"] == "[-original-]{+changed+}"


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

    monkeypatch.setattr(classify, "get_snapshots_for_source", fake_get_snaps)
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
        "get_snapshots_for_source",
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


def test_openai_client_is_passed_through_to_classify_diff(monkeypatch):
    monkeypatch.setattr(classify, "get_active_sources", lambda c: [_source("s1", "https://a.test")])
    monkeypatch.setattr(
        classify,
        "get_snapshots_for_source",
        lambda c, sid: [
            _snapshot("n", "new", "h2", "2026-01-02T00:00:00Z"),
            _snapshot("p", "old", "h1", "2026-01-01T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(classify, "has_signal_for_snapshot", lambda c, sid: False)
    received = {}

    def fake_classify(diff_text, source_type, client=None):
        received["client"] = client
        return {"classification": "cosmetic", "theme": "other", "summary": "x"}

    monkeypatch.setattr(classify, "classify_diff", fake_classify)
    monkeypatch.setattr(classify, "insert_signal", lambda *a, **k: {"id": "sig1"})

    sentinel_client = object()
    classify.run(client=None, openai_client=sentinel_client)

    assert received["client"] is sentinel_client


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            {"source_url": "https://x.test", "status": "error", "reason": None, "classification": None, "error": ""},
            "ERROR      https://x.test: ",
        ),
        (
            {"source_url": "https://x.test", "status": "skipped", "reason": "unchanged", "classification": None, "error": None},
            "SKIPPED    https://x.test (unchanged)",
        ),
        (
            {"source_url": "https://x.test", "status": "classified", "reason": None, "classification": "material", "error": None},
            "CLASSIFIED https://x.test: material",
        ),
    ],
)
def test_format_line(result, expected):
    assert classify.format_line(result) == expected
