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
