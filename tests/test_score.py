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
