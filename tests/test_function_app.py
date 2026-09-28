import types

import pytest

import function_app
import ingest


def _fake_module(name, run_fn, format_line_fn=None):
    fake = types.SimpleNamespace()
    fake.__name__ = name
    fake.run = run_fn
    fake.format_line = format_line_fn or (lambda r: f"{r['status']} {r.get('error', '')}")
    return fake


def test_run_stage_does_not_raise_when_no_errors():
    fake = _fake_module("fake", lambda client, openai_client=None: [{"error": None, "status": "ok"}])
    function_app._run_stage(fake, client=None, openai_client=None)  # must not raise


def test_run_stage_raises_naming_module_and_first_error():
    fake = _fake_module(
        "fake_stage",
        lambda client, openai_client=None: [
            {"error": None, "status": "ok"},
            {"error": "RuntimeError: boom", "status": "error"},
        ],
    )
    with pytest.raises(RuntimeError, match="fake_stage"):
        function_app._run_stage(fake, client=None, openai_client=None)


def test_run_stage_error_message_includes_first_errors_text():
    fake = _fake_module(
        "fake_stage",
        lambda client, openai_client=None: [{"error": "RuntimeError: boom", "status": "error"}],
    )
    with pytest.raises(RuntimeError, match="boom"):
        function_app._run_stage(fake, client=None, openai_client=None)


def test_run_stage_calls_ingest_without_openai_client_kwarg(monkeypatch):
    captured = {}

    def fake_ingest_run(client):
        captured["called_with_client_only"] = True
        return [{"error": None, "status": "ok"}]

    monkeypatch.setattr(ingest, "run", fake_ingest_run)
    monkeypatch.setattr(ingest, "format_line", lambda r: "")
    function_app._run_stage(ingest, client="fake-client", openai_client="fake-openai-client")
    assert captured.get("called_with_client_only") is True


def test_run_stage_calls_non_ingest_modules_with_openai_client_kwarg():
    captured = {}

    def fake_run(client, openai_client=None):
        captured["openai_client"] = openai_client
        return [{"error": None, "status": "ok"}]

    fake = _fake_module("not_ingest", fake_run)
    function_app._run_stage(fake, client="fake-client", openai_client="fake-openai-client")
    assert captured["openai_client"] == "fake-openai-client"


def test_daily_pipeline_runs_stages_in_order_and_stops_on_first_failure(monkeypatch):
    calls = []

    def fake_run_stage(module, client, openai_client=None):
        calls.append(module)
        if module == "classify":
            raise RuntimeError("boom")

    monkeypatch.setattr(function_app, "_run_stage", fake_run_stage)
    monkeypatch.setattr(function_app, "ingest", "ingest")
    monkeypatch.setattr(function_app, "classify", "classify")
    monkeypatch.setattr(function_app, "correlate", "correlate")
    monkeypatch.setattr(function_app, "score", "score")
    monkeypatch.setattr(function_app, "get_supabase_client", lambda: "supabase-client")

    closed = []

    class _FakeOpenAIClient:
        def close(self):
            closed.append(1)

    monkeypatch.setattr(function_app, "get_openai_client", lambda: _FakeOpenAIClient())

    with pytest.raises(RuntimeError, match="boom"):
        function_app.daily_pipeline(timer=None)

    assert calls == ["ingest", "classify"]  # stopped before correlate/score ever ran
    assert closed == [1]  # cleanup still ran despite the exception


def test_daily_pipeline_runs_all_four_stages_when_nothing_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(function_app, "_run_stage", lambda module, client, openai_client=None: calls.append(module))
    monkeypatch.setattr(function_app, "ingest", "ingest")
    monkeypatch.setattr(function_app, "classify", "classify")
    monkeypatch.setattr(function_app, "correlate", "correlate")
    monkeypatch.setattr(function_app, "score", "score")
    monkeypatch.setattr(function_app, "get_supabase_client", lambda: "supabase-client")

    class _FakeOpenAIClient:
        def close(self):
            pass

    monkeypatch.setattr(function_app, "get_openai_client", lambda: _FakeOpenAIClient())

    function_app.daily_pipeline(timer=None)

    assert calls == ["ingest", "classify", "correlate", "score"]
