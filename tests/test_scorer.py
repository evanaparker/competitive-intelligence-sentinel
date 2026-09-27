import json

import pytest

from scorer import score_signal


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response_json):
        self._response_json = response_json
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(json.dumps(self._response_json))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, response_json):
        self.chat = _FakeChat(_FakeCompletions(response_json))


SAMPLE_SIGNAL = {
    "diff_text": "[-$500-] {+$750+}",
    "theme": "pricing",
    "summary": "Starting price increased",
    "competitor_name": "Sonar",
    "source_type": "pricing_page",
}


def test_score_signal_returns_parsed_response():
    fake_client = _FakeOpenAIClient(
        {"materiality_score": 8, "confidence": "high", "rationale": "Price rose from $500 to $750."}
    )
    result = score_signal([SAMPLE_SIGNAL], client=fake_client)
    assert result == {"materiality_score": 8, "confidence": "high", "rationale": "Price rose from $500 to $750."}


def test_score_signal_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient({"materiality_score": 3, "confidence": "medium", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL], client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.1"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["materiality_score", "confidence", "rationale"]
    assert schema["additionalProperties"] is False
    assert "minimum" not in schema["properties"]["materiality_score"]


def test_score_signal_includes_diff_and_context_in_prompt():
    fake_client = _FakeOpenAIClient({"materiality_score": 8, "confidence": "high", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL], client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "[-$500-] {+$750+}" in user_message
    assert "Sonar" in user_message
    assert "pricing_page" in user_message


def test_score_signal_with_multiple_signals_includes_every_signals_evidence():
    second_signal = {
        "diff_text": "[-hiring 1-] {+hiring 5+}",
        "theme": "hiring",
        "summary": "Posted 3 enterprise AE roles",
        "competitor_name": "Sonar",
        "source_type": "job_board",
    }
    fake_client = _FakeOpenAIClient({"materiality_score": 9, "confidence": "high", "rationale": "x"})
    score_signal([SAMPLE_SIGNAL, second_signal], client=fake_client)
    user_message = fake_client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "[-$500-] {+$750+}" in user_message
    assert "[-hiring 1-] {+hiring 5+}" in user_message
    assert "Posted 3 enterprise AE roles" in user_message
    assert "pricing_page" in user_message
    assert "job_board" in user_message
    # competitor is stated once, not duplicated per signal
    assert user_message.count("Sonar") == 1


class _RefusalMessage:
    content = None
    refusal = "cannot assess this content"


class _RefusalChoice:
    message = _RefusalMessage()


class _RefusalResponse:
    choices = [_RefusalChoice()]


class _RefusalCompletions:
    def create(self, **kwargs):
        return _RefusalResponse()


class _RefusalChat:
    completions = _RefusalCompletions()


class _RefusalClient:
    chat = _RefusalChat()


def test_score_signal_raises_clear_error_on_refusal():
    with pytest.raises(RuntimeError, match="refused"):
        score_signal([SAMPLE_SIGNAL], client=_RefusalClient())
