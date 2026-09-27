import review_data


def _insight(id_, competitor_id, score=8, confidence="high", rationale="r"):
    return {
        "id": id_,
        "competitor_id": competitor_id,
        "materiality_score": score,
        "confidence": confidence,
        "rationale": rationale,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _competitor(id_, name):
    return {"id": id_, "name": name}


def _signal(id_, diff_text="[-a-] {+b+}", theme="pricing", summary="s"):
    return {"id": id_, "diff_text": diff_text, "theme": theme, "summary": summary, "classification": "material"}


def test_get_review_queue_assembles_full_context(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [_insight("ins1", "comp1")])
    monkeypatch.setattr(review_data, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(review_data, "get_signal_ids_for_insight", lambda c, iid: ["sig1"])
    monkeypatch.setattr(review_data, "get_signal", lambda c, sid: _signal("sig1"))

    queue = review_data.get_review_queue(client=None)

    assert queue == [
        {
            "insight_id": "ins1",
            "competitor_name": "Sonar",
            "materiality_score": 8,
            "confidence": "high",
            "rationale": "r",
            "signals": [{"diff_text": "[-a-] {+b+}", "theme": "pricing", "summary": "s"}],
        }
    ]


def test_get_review_queue_handles_multiple_signals_per_insight(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [_insight("ins1", "comp1")])
    monkeypatch.setattr(review_data, "get_competitor", lambda c, cid: _competitor("comp1", "Sonar"))
    monkeypatch.setattr(review_data, "get_signal_ids_for_insight", lambda c, iid: ["sig1", "sig2"])
    monkeypatch.setattr(review_data, "get_signal", lambda c, sid: _signal(sid, summary=f"summary-{sid}"))

    queue = review_data.get_review_queue(client=None)

    assert len(queue[0]["signals"]) == 2
    assert {s["summary"] for s in queue[0]["signals"]} == {"summary-sig1", "summary-sig2"}


def test_get_review_queue_returns_empty_list_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(review_data, "get_pending_insights", lambda c: [])
    assert review_data.get_review_queue(client=None) == []


def test_submit_review_approves_without_feedback(monkeypatch):
    updated = {}
    monkeypatch.setattr(
        review_data,
        "update_insight_status",
        lambda c, iid, status: updated.update(insight_id=iid, status=status) or {"id": iid, "status": status},
    )
    called_feedback = []
    monkeypatch.setattr(review_data, "insert_feedback", lambda *a, **k: called_feedback.append(1))

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved")

    assert result == {"status_updated": True, "feedback_saved": None, "error": None}
    assert updated == {"insight_id": "ins1", "status": "approved"}
    assert called_feedback == []


def test_submit_review_with_feedback_saves_both(monkeypatch):
    monkeypatch.setattr(review_data, "update_insight_status", lambda c, iid, status: {"id": iid, "status": status})
    fed = {}
    monkeypatch.setattr(
        review_data,
        "insert_feedback",
        lambda c, iid, rating, comment=None: fed.update(insight_id=iid, rating=rating, comment=comment)
        or {"id": "fb1"},
    )

    result = review_data.submit_review(
        client=None, insight_id="ins1", decision="rejected", rating="incorrect", comment="wrong evidence"
    )

    assert result == {"status_updated": True, "feedback_saved": True, "error": None}
    assert fed == {"insight_id": "ins1", "rating": "incorrect", "comment": "wrong evidence"}


def test_submit_review_status_update_failure_never_attempts_feedback(monkeypatch):
    def fail_update(c, iid, status):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(review_data, "update_insight_status", fail_update)
    called_feedback = []
    monkeypatch.setattr(review_data, "insert_feedback", lambda *a, **k: called_feedback.append(1))

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved", rating="useful")

    assert result["status_updated"] is False
    assert result["feedback_saved"] is None
    assert result["error"] is not None
    assert called_feedback == []


def test_submit_review_feedback_failure_after_successful_status_update(monkeypatch):
    monkeypatch.setattr(review_data, "update_insight_status", lambda c, iid, status: {"id": iid, "status": status})

    def fail_feedback(c, iid, rating, comment=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(review_data, "insert_feedback", fail_feedback)

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved", rating="useful")

    assert result["status_updated"] is True
    assert result["feedback_saved"] is False
    assert result["error"] is not None


def test_error_message_is_never_empty_on_status_failure(monkeypatch):
    def fail_update(c, iid, status):
        raise TimeoutError()  # str(TimeoutError()) == ""

    monkeypatch.setattr(review_data, "update_insight_status", fail_update)

    result = review_data.submit_review(client=None, insight_id="ins1", decision="approved")

    assert result["error"] != ""
    assert "TimeoutError" in result["error"]
