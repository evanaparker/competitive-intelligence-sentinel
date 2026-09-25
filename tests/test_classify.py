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
