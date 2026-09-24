import httpx
import pytest

import ingest


def _source(id_, url):
    return {"id": id_, "url": url, "competitor_id": "c1", "source_type": "other"}


def test_source_error_does_not_stop_others(monkeypatch):
    monkeypatch.setattr(
        ingest, "get_active_sources", lambda c: [_source("bad", "https://bad.test"), _source("good", "https://good.test")]
    )

    def fake_fetch(url):
        if url == "https://bad.test":
            raise httpx.ConnectError("connection refused")
        return "<p>hi</p>"

    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    monkeypatch.setattr(ingest, "get_latest_snapshot_hash", lambda c, sid: None)
    inserted = []
    monkeypatch.setattr(
        ingest, "insert_snapshot", lambda c, sid, content, h: inserted.append(sid) or {"id": "s1"}
    )

    results = ingest.run(client=None)

    assert len(results) == 2
    bad = next(r for r in results if r["url"] == "https://bad.test")
    good = next(r for r in results if r["url"] == "https://good.test")
    assert bad["error"] is not None
    assert bad["changed"] is False
    assert good["error"] is None
    assert good["changed"] is True
    assert inserted == ["good"]  # no snapshot inserted for the failed source


def test_error_message_is_never_empty_even_for_exceptions_with_no_message(monkeypatch):
    monkeypatch.setattr(ingest, "get_active_sources", lambda c: [_source("s1", "https://bad.test")])

    def fake_fetch(url):
        raise TimeoutError()  # str(TimeoutError()) == "" — the exact case that leaked through before

    monkeypatch.setattr(ingest, "fetch", fake_fetch)

    results = ingest.run(client=None)

    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


def test_first_check_of_a_source_is_reported_as_changed(monkeypatch):
    monkeypatch.setattr(ingest, "get_active_sources", lambda c: [_source("s1", "https://new.test")])
    monkeypatch.setattr(ingest, "fetch", lambda url: "<p>content</p>")
    monkeypatch.setattr(ingest, "get_latest_snapshot_hash", lambda c, sid: None)
    monkeypatch.setattr(ingest, "insert_snapshot", lambda c, sid, content, h: {"id": "s1"})

    results = ingest.run(client=None)

    assert results[0]["changed"] is True
    assert results[0]["error"] is None


def test_unchanged_content_reports_false_but_still_inserts_snapshot(monkeypatch):
    from extract import extract_text
    from hashing import compute_hash

    html = "<p>same content</p>"
    prior_hash = compute_hash(extract_text(html))

    monkeypatch.setattr(ingest, "get_active_sources", lambda c: [_source("s1", "https://same.test")])
    monkeypatch.setattr(ingest, "fetch", lambda url: html)
    monkeypatch.setattr(ingest, "get_latest_snapshot_hash", lambda c, sid: prior_hash)
    inserted = []
    monkeypatch.setattr(
        ingest, "insert_snapshot", lambda c, sid, content, h: inserted.append(h) or {"id": "s1"}
    )

    results = ingest.run(client=None)

    assert results[0]["changed"] is False
    assert results[0]["error"] is None
    assert inserted == [prior_hash]  # still inserted, per spec — repeated hash is expected


def test_changed_content_reports_true(monkeypatch):
    monkeypatch.setattr(ingest, "get_active_sources", lambda c: [_source("s1", "https://changed.test")])
    monkeypatch.setattr(ingest, "fetch", lambda url: "<p>new content</p>")
    monkeypatch.setattr(ingest, "get_latest_snapshot_hash", lambda c, sid: "some-old-hash")
    monkeypatch.setattr(ingest, "insert_snapshot", lambda c, sid, content, h: {"id": "s1"})

    results = ingest.run(client=None)

    assert results[0]["changed"] is True
    assert results[0]["error"] is None


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"url": "https://x.test", "changed": False, "error": ""}, "ERROR   https://x.test: "),
        ({"url": "https://x.test", "changed": False, "error": "boom"}, "ERROR   https://x.test: boom"),
        ({"url": "https://x.test", "changed": True, "error": None}, "CHANGED https://x.test"),
        ({"url": "https://x.test", "changed": False, "error": None}, "SAME    https://x.test"),
    ],
)
def test_format_line(result, expected):
    assert ingest.format_line(result) == expected
