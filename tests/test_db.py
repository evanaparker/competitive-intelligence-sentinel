import pytest

from db import get_client, update_insight_status


class _FakeQuery:
    def __init__(self, captured):
        self._captured = captured

    def update(self, payload):
        self._captured["payload"] = payload
        return self

    def eq(self, column, value):
        self._captured["id"] = value
        return self

    def execute(self):
        return type("Response", (), {"data": [{"id": self._captured["id"], **self._captured["payload"]}]})()


class _FakeClient:
    def __init__(self, captured):
        self._captured = captured

    def table(self, name):
        return _FakeQuery(self._captured)


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


def test_update_insight_status_advances_updated_at():
    captured = {}
    update_insight_status(_FakeClient(captured), "ins1", "approved")
    assert "updated_at" in captured["payload"]
    assert captured["payload"]["updated_at"] != ""
