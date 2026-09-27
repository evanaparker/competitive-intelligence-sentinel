import uuid

import correlate


def _material_signal(id_, snapshot_id, created_at, theme="pricing", summary="s", correlation_group_id=None):
    return {
        "id": id_,
        "snapshot_id": snapshot_id,
        "diff_text": "[-a-] {+b+}",
        "theme": theme,
        "summary": summary,
        "created_at": created_at,
        "correlation_group_id": correlation_group_id,
    }


def _snapshot(id_, source_id):
    return {"id": id_, "source_id": source_id, "content_hash": "h", "fetched_at": "2026-01-01T00:00:00Z"}


def _source(id_, competitor_id, source_type="pricing_page"):
    return {"id": id_, "url": "https://a.test", "competitor_id": competitor_id, "source_type": source_type}


def _competitor(id_, name="Sonar"):
    return {"id": id_, "name": name}


def test_no_pending_signals_returns_empty_list(monkeypatch):
    monkeypatch.setattr(correlate, "get_material_signals", lambda c: [])
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    assert correlate.run(client=None) == []


def test_already_insighted_signal_is_excluded(monkeypatch):
    monkeypatch.setattr(
        correlate, "get_material_signals", lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z")]
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: {"sig1"})
    called = []
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: called.append(1))
    assert correlate.run(client=None) == []
    assert called == []


def test_already_decided_signal_is_excluded(monkeypatch):
    # non-null correlation_group_id (whether shared or self-referential)
    # means correlate.py already decided this one — must never reconsider it
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z", correlation_group_id="sig1")],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    called = []
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: called.append(1))
    assert correlate.run(client=None) == []
    assert called == []


def test_singleton_signal_gets_self_assigned_group_id_no_llm_call(monkeypatch):
    monkeypatch.setattr(
        correlate, "get_material_signals", lambda c: [_material_signal("sig1", "snap1", "2026-01-01T00:00:00Z")]
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot("snap1", "src1"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source("src1", "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    llm_called = []
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: llm_called.append(1))
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(signal_ids): group_id}),
    )

    results = correlate.run(client=None)

    assert llm_called == []
    assert assigned == {("sig1",): "sig1"}
    assert results == [
        {"competitor_id": "comp1", "signal_ids": ["sig1"], "status": "singleton", "group_id": "sig1", "error": None}
    ]


def test_two_signals_within_window_get_grouped_when_llm_says_correlated(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-03T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: True)
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(sorted(signal_ids)): group_id}),
    )

    results = correlate.run(client=None)

    assert len(results) == 1
    assert results[0]["status"] == "grouped"
    assert sorted(results[0]["signal_ids"]) == ["sig1", "sig2"]
    group_id = results[0]["group_id"]
    assert uuid.UUID(group_id)  # a real uuid was generated
    assert assigned == {("sig1", "sig2"): group_id}


def test_two_signals_left_ungrouped_when_llm_says_not_correlated(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-03T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))
    monkeypatch.setattr(correlate, "judge_correlation", lambda cluster, client=None: False)
    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(signal_ids): group_id}),
    )

    results = correlate.run(client=None)

    assert len(results) == 1
    assert results[0]["status"] == "ungrouped"
    assert sorted(results[0]["signal_ids"]) == ["sig1", "sig2"]
    # each signal gets its OWN id, not a shared one — two separate calls
    assert assigned == {("sig1",): "sig1", ("sig2",): "sig2"}


def test_judge_correlation_error_does_not_stop_other_competitors(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("bad1", "snap-bad1", "2026-01-01T00:00:00Z", summary="bad-marker"),
            _material_signal("bad2", "snap-bad2", "2026-01-02T00:00:00Z", summary="bad-marker"),
            _material_signal("good1", "snap-good1", "2026-01-01T00:00:00Z", summary="good-marker"),
            _material_signal("good2", "snap-good2", "2026-01-02T00:00:00Z", summary="good-marker"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())

    def fake_get_snapshot(c, sid):
        source_id = "src-bad" if "bad" in sid else "src-good"
        return _snapshot(sid, source_id)

    def fake_get_source(c, sid):
        competitor_id = "comp-bad" if "bad" in sid else "comp-good"
        return _source(sid, competitor_id)

    monkeypatch.setattr(correlate, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(correlate, "get_source", fake_get_source)
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))

    def fake_judge(cluster, client=None):
        if cluster[0]["summary"] == "bad-marker":
            raise RuntimeError("rate limited")
        return True

    monkeypatch.setattr(correlate, "judge_correlation", fake_judge)

    assigned = {}
    monkeypatch.setattr(
        correlate,
        "assign_correlation_group",
        lambda c, signal_ids, group_id: assigned.update({tuple(sorted(signal_ids)): group_id}),
    )

    results = correlate.run(client=None)

    bad = next(r for r in results if r["competitor_id"] == "comp-bad")
    good = next(r for r in results if r["competitor_id"] == "comp-good")
    assert bad["status"] == "error"
    assert bad["error"] is not None
    assert good["status"] == "grouped"
    assert ("good1", "good2") in assigned


def test_error_message_is_never_empty(monkeypatch):
    monkeypatch.setattr(
        correlate,
        "get_material_signals",
        lambda c: [
            _material_signal("sig1", "snap1", "2026-01-01T00:00:00Z"),
            _material_signal("sig2", "snap2", "2026-01-02T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(correlate, "get_signal_ids_with_insight", lambda c: set())
    monkeypatch.setattr(correlate, "get_snapshot", lambda c, sid: _snapshot(sid, f"src-{sid}"))
    monkeypatch.setattr(correlate, "get_source", lambda c, sid: _source(sid, "comp1"))
    monkeypatch.setattr(correlate, "get_competitor", lambda c, cid: _competitor(cid))

    def fake_judge(cluster, client=None):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(correlate, "judge_correlation", fake_judge)

    results = correlate.run(client=None)

    assert results[0]["error"] is not None
    assert results[0]["error"] != ""
    assert "TimeoutError" in results[0]["error"]


def test_format_line_variants():
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1", "s2"], "status": "grouped", "group_id": "g1", "error": None}
    ) == "GROUPED   c1: 2 signals (g1)"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1", "s2"], "status": "ungrouped", "group_id": None, "error": None}
    ) == "UNGROUPED c1: 2 signals (not correlated)"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1"], "status": "singleton", "group_id": "s1", "error": None}
    ) == "SINGLETON c1: 1 signal"
    assert correlate.format_line(
        {"competitor_id": "c1", "signal_ids": ["s1"], "status": "error", "group_id": None, "error": "boom"}
    ) == "ERROR     c1: boom"
