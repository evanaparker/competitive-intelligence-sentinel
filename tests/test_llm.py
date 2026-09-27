import pytest

from llm import get_client


def test_get_client_raises_when_endpoint_missing(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_ENDPOINT"):
        get_client()


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
        get_client()


def test_get_client_returns_client_when_both_set(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    assert get_client() is not None


def test_get_client_default_timeout_is_15_seconds(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    client = get_client()
    assert client.timeout == 15.0


def test_get_client_accepts_a_custom_timeout(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    client = get_client(timeout=30.0)
    assert client.timeout == 30.0


def test_get_client_builds_v1_base_url_with_trailing_slash_on_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    client = get_client()
    assert str(client.base_url) == "https://example.openai.azure.com/openai/v1/"


def test_get_client_builds_v1_base_url_without_trailing_slash_on_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    client = get_client()
    assert str(client.base_url) == "https://example.openai.azure.com/openai/v1/"
