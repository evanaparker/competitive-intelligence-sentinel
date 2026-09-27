import json
from datetime import datetime, timedelta, timezone

from correlator import cluster_by_time_window


def _signal(id_, created_at):
    return {"id": id_, "created_at": created_at}


def test_empty_input_returns_empty_list():
    assert cluster_by_time_window([]) == []


def test_single_signal_is_its_own_cluster():
    signals = [_signal("s1", "2026-01-01T00:00:00Z")]
    assert cluster_by_time_window(signals) == [[_signal("s1", "2026-01-01T00:00:00Z")]]


def test_two_signals_within_window_form_one_cluster():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-03T00:00:00Z"),
    ]
    assert cluster_by_time_window(signals) == [signals]


def test_signal_exactly_window_days_away_is_included():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-08T00:00:00Z"),  # exactly 7 days later
    ]
    assert cluster_by_time_window(signals, window_days=7) == [signals]


def test_signal_just_past_window_starts_a_new_cluster():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-08T00:00:01Z"),  # 7 days and 1 second later
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0]], [signals[1]]]


def test_third_signal_does_not_chain_through_the_second():
    # s1 and s2 are 6 days apart (would cluster). s3 is 4 days after s2 but
    # 10 days after s1 — a chained/sliding window would pull s3 into s1's
    # cluster via s2; the fixed-window-from-anchor design must not.
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-07T00:00:00Z"),
        _signal("s3", "2026-01-11T00:00:00Z"),
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0], signals[1]], [signals[2]]]


def test_preserves_input_order_within_and_across_clusters():
    signals = [
        _signal("s1", "2026-01-01T00:00:00Z"),
        _signal("s2", "2026-01-02T00:00:00Z"),
        _signal("s3", "2026-02-01T00:00:00Z"),
    ]
    result = cluster_by_time_window(signals, window_days=7)
    assert result == [[signals[0], signals[1]], [signals[2]]]


import pytest

from correlator import judge_correlation


class _FakeMessage:
    def __init__(self, content=None, refusal=None):
        self.content = content
        self.refusal = refusal


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, response_json):
        self._response_json = response_json
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(_FakeMessage(content=json.dumps(self._response_json)))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, response_json):
        self.chat = _FakeChat(_FakeCompletions(response_json))


def _cluster_signal(competitor_name="Sonar", source_type="pricing_page", theme="pricing", summary="s", created_at="2026-01-01T00:00:00Z"):
    return {
        "competitor_name": competitor_name,
        "source_type": source_type,
        "theme": theme,
        "summary": summary,
        "created_at": created_at,
    }


def test_judge_correlation_returns_true():
    fake_client = _FakeOpenAIClient({"correlated": True})
    result = judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    assert result is True


def test_judge_correlation_returns_false():
    fake_client = _FakeOpenAIClient({"correlated": False})
    result = judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    assert result is False


def test_judge_correlation_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient({"correlated": True})
    judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["correlated"]
    assert schema["additionalProperties"] is False


def test_judge_correlation_includes_every_signal_evidence_in_prompt():
    fake_client = _FakeOpenAIClient({"correlated": True})
    cluster = [
        _cluster_signal(source_type="pricing_page", theme="pricing", summary="Price rose to $750"),
        _cluster_signal(source_type="job_board", theme="hiring", summary="Posted 3 enterprise AE roles", created_at="2026-01-04T00:00:00Z"),
    ]
    judge_correlation(cluster, client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "Price rose to $750" in user_message
    assert "Posted 3 enterprise AE roles" in user_message
    assert "pricing_page" in user_message
    assert "job_board" in user_message


def test_judge_correlation_raises_clear_error_on_refusal():
    fake_client = _FakeOpenAIClient({"correlated": True})
    fake_client.chat.completions.create = lambda **kwargs: _FakeResponse(
        _FakeMessage(content=None, refusal="cannot assess this content")
    )
    with pytest.raises(RuntimeError, match="refused"):
        judge_correlation([_cluster_signal(), _cluster_signal()], client=fake_client)


from correlator import window_has_closed


def test_window_has_closed_false_for_a_recent_signal():
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert window_has_closed({"created_at": recent}) is False


def test_window_has_closed_true_for_an_old_signal():
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert window_has_closed({"created_at": old}) is True


def test_window_has_closed_respects_custom_window_days():
    eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert window_has_closed({"created_at": eight_days_ago}, window_days=7) is True
    assert window_has_closed({"created_at": eight_days_ago}, window_days=14) is False
