import pytest

from llm import get_client


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    assert get_client() is not None


def test_get_client_default_timeout_is_15_seconds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client()
    assert client.timeout == 15.0


def test_get_client_accepts_a_custom_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = get_client(timeout=30.0)
    assert client.timeout == 30.0
