import json

import pytest

from classifier import classify_diff, get_client


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


def test_classify_diff_returns_parsed_response():
    fake_client = _FakeOpenAIClient(
        {"classification": "material", "theme": "pricing", "summary": "Price increased"}
    )
    result = classify_diff("[-1.25-] {+2.50+}", "pricing_page", client=fake_client)
    assert result == {"classification": "material", "theme": "pricing", "summary": "Price increased"}


def test_classify_diff_sends_correct_model_and_strict_schema():
    fake_client = _FakeOpenAIClient(
        {"classification": "cosmetic", "theme": "other", "summary": "Wording changed"}
    )
    classify_diff("some diff", "pricing_page", client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["classification", "theme", "summary"]
    assert schema["additionalProperties"] is False


def test_classify_diff_includes_diff_text_in_prompt():
    fake_client = _FakeOpenAIClient(
        {"classification": "material", "theme": "pricing", "summary": "x"}
    )
    classify_diff("[-old-] {+new+}", "pricing_page", client=fake_client)
    kwargs = fake_client.chat.completions.last_kwargs
    user_message = kwargs["messages"][-1]["content"]
    assert "[-old-] {+new+}" in user_message


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    assert get_client() is not None


def test_get_client_sets_a_bounded_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client()
    # SDK default read timeout is 600s; an unattended run must not be able
    # to stall that long on one source.
    assert client.timeout == 15.0


class _RefusalMessage:
    content = None
    refusal = "cannot classify this content"


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


def test_classify_diff_raises_clear_error_on_refusal():
    with pytest.raises(RuntimeError, match="refused"):
        classify_diff("some diff", "pricing_page", client=_RefusalClient())


class _NoContentNoRefusalMessage:
    content = None
    refusal = None


class _NoContentChoice:
    message = _NoContentNoRefusalMessage()


class _NoContentResponse:
    choices = [_NoContentChoice()]


class _NoContentCompletions:
    def create(self, **kwargs):
        return _NoContentResponse()


class _NoContentChat:
    completions = _NoContentCompletions()


class _NoContentClient:
    chat = _NoContentChat()


def test_classify_diff_raises_clear_error_when_content_missing_without_refusal():
    with pytest.raises(RuntimeError, match="no content"):
        classify_diff("some diff", "pricing_page", client=_NoContentClient())
