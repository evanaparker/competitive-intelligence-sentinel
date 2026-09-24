import pytest

from db import get_client


def test_get_client_raises_when_url_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-key")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        get_client()


def test_get_client_raises_when_key_missing(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        get_client()


def test_get_client_returns_client_when_both_set(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    # the supabase client validates the key looks like a JWT (3 dot-separated
    # segments) before returning, even without making a network call
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "aaa.bbb.ccc")
    assert get_client() is not None
