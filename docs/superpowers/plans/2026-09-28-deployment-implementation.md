# Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate the existing pipeline (`ingest.py` → `classify.py` → `correlate.py` → `score.py`) to run daily on Azure Functions, and host the review app (`review.py`) at a persistent, private URL — without changing any pipeline logic.

**Architecture:** One new file, `function_app.py`, wraps the four existing scripts' `run()`/`format_line()` functions in a single Timer-Triggered Azure Function that stops at the first stage reporting an error. `review.py` deploys unchanged to Streamlit Community Cloud (see Task 4 for why this replaced the original Azure App Service design). The Function App reads credentials from its Application Settings; the review app reads the same-shaped credentials from Streamlit Cloud's Secrets, which Streamlit exposes as `os.environ` — neither needs `.env`.

**Tech Stack:** Azure Functions (Python v2 programming model, Consumption plan), Azure Monitor + Application Insights, Streamlit Community Cloud, `pytest`.

**Spec:** [docs/superpowers/specs/2026-09-28-deployment-design.md](../specs/2026-09-28-deployment-design.md)

## Global Constraints

- `function_app.py`/`host.json`/`.funcignore` live at the repo root, next to `ingest.py`/`classify.py`/etc. — no `src/` restructuring, no path manipulation to import them.
- No changes to `ingest.py`, `classify.py`, `correlate.py`, `score.py`, `review.py`, `review_data.py`, or `db.py`. This plan only adds files.
- `requirements.txt` gains exactly one line: `azure-functions==1.25.0` (confirmed current stable release).
- Azure resources: Function App on the classic **Consumption** plan with connection-string-based storage auth (not Flex Consumption / managed identity) — chosen explicitly to minimize the number of resources (no user-assigned identity, no role assignments) for this PoC's scale. The review app does not use an Azure resource at all — see Task 4 for why (App Service's F1 tier hit a subscription-level quota of 0, and the paid B1 tier was declined to avoid ongoing cost).
- All resources go in one new resource group: `cisentinel-deploy-rg`, region `eastus` (adjust the region in every command below together if a different one is preferred — there's no per-resource reason to split them across regions).
- Credentials live in each resource's **Application Settings** — no Key Vault.
- **Execution model for this plan only, different from every prior sub-project's plan:** this session has no Azure CLI or Azure credentials of its own. Steps marked **(you, in your terminal)** are commands the user runs themselves, having already run `az login`; steps marked **(Claude)** are ones executed normally in this session (writing/testing code, or an external check like `curl` against a now-public URL that needs no Azure credentials). Every **(you, in your terminal)** step has an `Expected:` line — paste back what you actually see so the step can be confirmed before moving on, exactly like any other step in this plan.
- Global resource-name uniqueness: storage account, Function App, and App Service names must each be globally unique across all of Azure. The names below are this plan's first choice; if `az` reports a name is already taken, pick a different suffix (e.g. append your initials or a few digits) and use that same substituted name in every subsequent command in that task — this is a real, unavoidable Azure constraint, not a placeholder to resolve later.

## Review Focus

- A pipeline stage's `run()` reporting even one per-item error must stop `daily_pipeline` before the next stage runs — not just log the error and continue. This is the plan's central behavior change from "run manually and read the output" to "run unattended and trust it stopped when it should have."
- `_run_stage` must call `ingest.run(client)` (no `openai_client` kwarg) specifically because the module *is* `ingest`, not because `openai_client` happens to be falsy or absent — a wrong branch here is a `TypeError` in production the first time the real pipeline runs, not a test failure caught locally, since `ingest.run()` only accepts one positional argument.
- `openai_client.close()` must still run even when a stage raises partway through `daily_pipeline` — an oversight here leaks an HTTP client on every failed run, and failed runs are exactly the case this plan is built to make routine and visible (not rare).
- The review app's privacy setting must be the very next action after its first deploy completes, before anything else — Task 4's own completion contract requires confirming an unauthenticated request is actually blocked before the task is considered done. (Streamlit Community Cloud's deploy flow doesn't support enabling privacy before the app is live the way Azure App Service's Easy Auth did; see Task 4's stated residual-risk note.)
- A deliberately-failed pipeline run (Task 3's verification) must actually produce a confirmed-firing alert, not just an `Alert Rule` that looks correctly configured in the portal — the difference between "configured" and "verified" matters here specifically because there is no unit test that can substitute for it. (Execution note: the alert's *firing* was confirmed live via the Alerts Management API and the Portal; the alert *email* was never confirmed to arrive — see Task 3's ledger ruling and ledger completion line below, which record this gap as accepted rather than resolved.)

---

### Task 1: `function_app.py`, `host.json`, `.funcignore`

**Files:**
- Create: `function_app.py`, `host.json`, `.funcignore` (all repo root)
- Modify: `requirements.txt` (add `azure-functions==1.25.0`)
- Test: `tests/test_function_app.py`

**Interfaces:**
- Consumes: `ingest.run(client)`, `classify.run(client, openai_client=None)`, `correlate.run(client, openai_client=None)`, `score.run(client, openai_client=None)`, and each module's `format_line(r)` — all already merged, unchanged. `db.get_client` (as `get_supabase_client`), `llm.get_client` (as `get_openai_client`) — already merged, unchanged.
- Produces: `function_app.app` (the `func.FunctionApp()` instance Azure's runtime discovers), `function_app._run_stage`, `function_app.daily_pipeline` — consumed only by Azure's Functions runtime after deployment (Task 2), not imported by any other module in this repo.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_function_app.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_function_app.py -v`
Expected: `ModuleNotFoundError: No module named 'function_app'` (or an `azure.functions` import error if that dependency isn't installed yet — install it first: `pip3 install --target=.deps azure-functions==1.25.0`, then re-run to confirm the failure is specifically the missing `function_app` module).

- [ ] **Step 3: Write `function_app.py`**

```python
import logging

import azure.functions as func

import ingest
import classify
import correlate
import score
from db import get_client as get_supabase_client
from llm import get_client as get_openai_client

app = func.FunctionApp()


def _run_stage(module, client, openai_client=None) -> None:
    results = module.run(client) if module is ingest else module.run(client, openai_client=openai_client)
    for r in results:
        logging.info(module.format_line(r))
    errors = [r for r in results if r["error"] is not None]
    if errors:
        raise RuntimeError(f"{module.__name__} reported {len(errors)} error(s): {errors[0]['error']}")


@app.timer_trigger(schedule="0 0 6 * * *", arg_name="timer", run_on_startup=False)
def daily_pipeline(timer: func.TimerRequest) -> None:
    supabase_client = get_supabase_client()
    openai_client = get_openai_client()
    try:
        _run_stage(ingest, supabase_client)
        _run_stage(classify, supabase_client, openai_client=openai_client)
        _run_stage(correlate, supabase_client, openai_client=openai_client)
        _run_stage(score, supabase_client, openai_client=openai_client)
    finally:
        openai_client.close()
```

- [ ] **Step 4: Write `host.json`**

```json
{
  "version": "2.0",
  "logging": {
    "applicationInsights": {
      "samplingSettings": {
        "isEnabled": true
      }
    }
  }
}
```

- [ ] **Step 5: Write `.funcignore`**

Excludes everything from the deployment package that the Function App doesn't need — the local `.deps/` install (Azure's own remote build does its own `pip install` from `requirements.txt`, in a build environment matched to the target host, so bundling this repo's local install is both unnecessary and a needless few hundred MB), the test suite, git internals, worktrees, docs, the Supabase migrations folder, and the review app (deployed separately in Task 4, not via this Function App's package):

```
.git*
.deps/
.venv/
__pycache__/
*.pyc
tests/
.worktrees/
docs/
supabase/
review.py
review_data.py
run_review_app.sh
.superpowers/
```

- [ ] **Step 6: Add the new dependency to `requirements.txt`**

Append `azure-functions==1.25.0` as a new line.

- [ ] **Step 7: Run to verify it passes**

Run: `PYTHONPATH=.deps python3 -m pytest tests/test_function_app.py -v`
Expected: 7 passed.

- [ ] **Step 8: Run the whole suite**

Run: `PYTHONPATH=.deps python3 -m pytest tests/ -v`
Expected: all tests pass (110 pre-existing plus this task's 7 new ones = 117).

- [ ] **Step 9: Commit**

```bash
git add function_app.py host.json .funcignore requirements.txt tests/test_function_app.py
git commit -m "feat: add function_app.py orchestrating the daily pipeline"
```

---

### Task 2: Provision and deploy the Azure Function App

**Files:** none — this task provisions live Azure resources and deploys Task 1's code to them.

**Interfaces:**
- Consumes: `function_app.py`, `host.json`, `.funcignore`, `requirements.txt` (Task 1) — deployed as-is, unmodified.

- [ ] **Step 1 (you, in your terminal): Install Azure Functions Core Tools**, if not already installed (needed for `func azure functionapp publish`):

macOS: `brew tap azure/functions && brew install azure-functions-core-tools@4`
Linux (Debian/Ubuntu):
```bash
curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > microsoft.gpg
sudo mv microsoft.gpg /etc/apt/trusted.gpg.d/microsoft.gpg
sudo sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-ubuntu-$(lsb_release -cs)-prod $(lsb_release -cs) main" > /etc/apt/sources.list.d/dotnetdev.list'
sudo apt-get update
sudo apt-get install azure-functions-core-tools-4
```
Windows: `winget install Microsoft.Azure.FunctionsCoreTools`

Expected: `func --version` prints a `4.x.x` version.

- [ ] **Step 2 (you, in your terminal): Create the resource group**

```bash
az group create --name cisentinel-deploy-rg --location eastus
```

Expected: JSON output with `"provisioningState": "Succeeded"`.

- [ ] **Step 3 (you, in your terminal): Create the storage account the Function App needs internally**

```bash
az storage account create \
  --name cisentinelfuncst \
  --resource-group cisentinel-deploy-rg \
  --location eastus \
  --sku Standard_LRS
```

Expected: JSON output with `"provisioningState": "Succeeded"`. If it fails with a name-uniqueness error, pick a different name (e.g. `cisentinelfuncst2`) and use that name in this and every following command that references it.

- [ ] **Step 4 (you, in your terminal): Create the Function App** (classic Consumption plan, Python 3.12, Linux — Application Insights is created automatically as part of this command unless explicitly disabled, which this doesn't do)

```bash
az functionapp create \
  --resource-group cisentinel-deploy-rg \
  --name cisentinel-pipeline \
  --consumption-plan-location eastus \
  --runtime python \
  --runtime-version 3.12 \
  --os-type Linux \
  --storage-account cisentinelfuncst \
  --functions-version 4
```

Expected: JSON output with `"state": "Running"`. Same name-uniqueness caveat as Step 3 — the Function App's name becomes part of a public hostname (`<name>.azurewebsites.net`), so it must be globally unique; substitute and reuse a new name if this errors.

- [ ] **Step 5 (you, in your terminal): Set Application Settings** (the four credentials `function_app.py` needs — paste your real values, the same ones already in your local `.env`)

```bash
az functionapp config appsettings set \
  --name cisentinel-pipeline \
  --resource-group cisentinel-deploy-rg \
  --settings \
    SUPABASE_URL="<your SUPABASE_URL>" \
    SUPABASE_SERVICE_ROLE_KEY="<your SUPABASE_SERVICE_ROLE_KEY>" \
    AZURE_OPENAI_ENDPOINT="<your AZURE_OPENAI_ENDPOINT>" \
    AZURE_OPENAI_API_KEY="<your AZURE_OPENAI_API_KEY>"
```

Expected: JSON array listing all four settings back (values shown, since this is your own terminal — this output never needs to be pasted back to Claude).

- [ ] **Step 6 (you, in your terminal): Deploy the code**, run from the repo root (the same directory as `function_app.py`)

```bash
func azure functionapp publish cisentinel-pipeline
```

Expected: ends with `Deployment successful.` and `Functions in cisentinel-pipeline:` listing `daily_pipeline - [timerTrigger]`. Paste back the full output.

- [ ] **Step 7 (you, in the Azure Portal): Manually trigger the function once to verify a real end-to-end run**

Portal → your Function App (`cisentinel-pipeline`) → Functions → `daily_pipeline` → Code + Test → Test/Run → Run. Wait ~10-30 seconds (this run hits the real Sonar pricing page, the real Supabase project, and real Azure OpenAI calls — the exact same side effects a manual `python3 ingest.py && python3 classify.py && python3 correlate.py && python3 score.py` would have).

Expected: the Test/Run panel shows the invocation completed; the Logs pane (or Application Insights → Logs → `traces`) shows the `logging.info(...)` lines from `_run_stage` — one `SAME <url>` or `CHANGED <url>` line from `ingest`, then classify/correlate/score's own lines, ending without an exception. Paste back what the Logs pane shows.

- [ ] **Step 8: Verify in Supabase that the run actually did something**

Run via the Supabase MCP `execute_sql` tool:

```sql
select count(*) from snapshots where fetched_at > now() - interval '5 minutes';
```

Expected: `1` (the snapshot Step 7's `ingest` stage just inserted) — confirms the deployed function reached the real database, not just that Azure reported success.

- [ ] **Step 9: Record this task's completion**

No pytest command applies (this task provisioned and live-verified real infrastructure). Ledger: `Task 2: complete (Function App cisentinel-pipeline deployed and live-verified — manual trigger completed successfully, Supabase shows a new snapshot from the run)`.

---

### Task 3: Configure and verify failure alerting

**Files:** none — Azure Monitor configuration only.

**Interfaces:**
- Consumes: the Function App and its Application Insights instance from Task 2.

- [ ] **Step 1 (you, in the Azure Portal): Create the Action Group**

Portal → Monitor → Alerts → Action Groups → Create. Resource group `cisentinel-deploy-rg`, region `Global`, name `cisentinel-ops-alerts`, short name (12 chars max) `cisentops`. Under Notifications, add an Email notification with your own address. Save.

Expected: the Action Group appears in the list with your email listed under it.

- [ ] **Step 2 (you, in the Azure Portal): Create the Alert Rule**

Portal → your Function App (`cisentinel-pipeline`) → Monitoring → Alerts → Create → Alert rule. Scope is already the Function App. Condition → Signal name: **"Failed function executions"** (a built-in metric, no query to write) → threshold: Greater than 0, aggregation over a 5-minute window (or the smallest window the portal offers). Actions → select the `cisentinel-ops-alerts` Action Group from Step 1. Name the rule `cisentinel-pipeline-failure-alert`. Create.

Expected: the Alert Rule appears as Enabled in the Function App's Alerts list.

- [ ] **Step 3: Seed a source that will deliberately fail `ingest.py`'s fetch step**

Via the Supabase MCP `execute_sql` tool:

```sql
insert into sources (competitor_id, source_type, url, is_active)
values ((select id from competitors where name = 'Sonar'), 'other', 'https://this-domain-does-not-exist-cis-test.invalid', true)
returning id;
```

- [ ] **Step 4 (you, in the Azure Portal): Manually trigger `daily_pipeline` again** (same Test/Run panel as Task 2 Step 7)

Expected: this time the run fails — the Test/Run panel (or Application Insights → Failures) shows an exception whose message names the fake source's URL and an `error()`/connection-type failure, matching `ingest.py`'s existing per-item error format (`ERROR   <url>: <message>`) wrapped in `_run_stage`'s `RuntimeError`. Paste back what you see.

- [ ] **Step 5: Wait for the alert email, then confirm it arrived**

The Alert Rule's evaluation window (Step 2) determines how long this takes — allow up to that window's length plus a few minutes for the Action Group to fire. Confirm you received an email referencing `cisentinel-pipeline-failure-alert` or `cisentinel-pipeline`.

Expected: an email arrived. This is the one step in this whole plan that cannot be verified any other way — if it doesn't arrive within a reasonable margin past the evaluation window, check the Action Group's email address and the Alert Rule's condition/scope before re-triggering.

- [ ] **Step 6: Clean up the deliberately-broken source**

```sql
delete from sources where url = 'https://this-domain-does-not-exist-cis-test.invalid';
```

Verify via `execute_sql`: `select count(*) from sources where url = 'https://this-domain-does-not-exist-cis-test.invalid';` → `0`.

- [ ] **Step 7: Record this task's completion**

Ledger: `Task 3: complete (Alert Rule + Action Group configured and live-verified — a deliberately failing source caused daily_pipeline to fail as expected, and the alert's firing was confirmed via the Azure Portal and the Alerts Management API; the alert email's delivery was not confirmed — see ruling; scratch source cleaned up)`.

---

### Task 4: Deploy the review app to Streamlit Community Cloud, private from the first moment it's checkable

**Files:** none — `review.py`/`review_data.py`/`db.py` deploy unchanged; no Azure resource involved in this task.

**Interfaces:**
- Consumes: `review.py`, `review_data.py`, `db.py`, `requirements.txt` — all already merged, unchanged.

**Why this replaces the original Azure App Service design:** this subscription's App Service F1 (Free) tier has a VM quota of 0 in every region tried, and B1 (~$13/month) was declined to avoid ongoing cost. Streamlit Community Cloud deploys directly from this repo's GitHub source (already public), is free with no VM quota of any kind, and — confirmed live against Streamlit's own docs before writing this task — supports both private apps ("Only specific people can view this app") and secrets that are automatically exposed as `os.environ` variables, so `db.py`'s existing `os.environ.get("SUPABASE_URL")`/`os.environ.get("SUPABASE_SERVICE_ROLE_KEY")` calls need no code change.

**A real difference from the Azure design, stated plainly:** Azure App Service let Easy Auth be enabled *before* the first deploy, so the app was never reachable unauthenticated even for a moment. Streamlit Community Cloud's deploy flow doesn't offer an equivalent — the app goes live first, and privacy is set as a separate action immediately after. This task's steps minimize that window (privacy is the very next action after deploy, before anything else, and is verified before the task is considered done) but — unlike Task 4's original design — cannot make the guarantee zero-width. This repo being public means anyone who somehow captured the exact freshly-generated URL during that short window could have reached the app; the deployed content only ever names things already visible in this public repo (no secrets in the app's own output), and the window closes as soon as Step 4 completes.

- [ ] **Step 1 (you, in a browser): Sign in to Streamlit Community Cloud**

Go to [share.streamlit.io](https://share.streamlit.io) and sign in with the GitHub account that owns `evanaparker/competitive-intelligence-sentinel` (the same account already used for this repo).

Expected: you land on the Community Cloud workspace/dashboard.

- [ ] **Step 2 (you, in a browser): Start deploying the app, but do not click the final Deploy button yet**

Click "Create app" (or "New app"). Choose to deploy from an existing repo. Set:
- Repository: `evanaparker/competitive-intelligence-sentinel`
- Branch: `main`
- Main file path: `review.py`

- [ ] **Step 3 (you, in a browser): Set secrets via Advanced settings, before deploying**

In the same deploy dialog, open "Advanced settings" → "Secrets", and paste:

```toml
SUPABASE_URL = "<your SUPABASE_URL>"
SUPABASE_SERVICE_ROLE_KEY = "<your SUPABASE_SERVICE_ROLE_KEY>"
```

Save the advanced settings, then click Deploy.

Expected: a build log appears, installing `requirements.txt` (this takes a minute or two — Streamlit Cloud's build environment installs everything itself, same as App Service's Oryx would have, no `.deps`/`PYTHONPATH` workaround needed here either). Note the app's URL once the build finishes (`https://<something>.streamlit.app`) — use that exact URL in every step below.

- [ ] **Step 4 (you, in a browser): Immediately set the app to private — the very next action after the build finishes, before anything else**

From the app's page, open its settings (the "⋮" menu or "Settings") → "Sharing". Under "Who can view this app", select **"Only specific people can view this app"**. Add your own email as a viewer. Save.

Expected: the Sharing section confirms the app is private and lists your email as the only (or first) viewer.

- [ ] **Step 5: Verify unauthenticated access is actually blocked**

Streamlit serves a static shell and renders `review.py`'s content client-side over a websocket, so a title never appears in the raw HTML even for a fully public app — grepping the fetched body for app text passes regardless of the Sharing setting and proves nothing. Check the HTTP status/redirect instead:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<your-app>.streamlit.app/
```

Expected: `303` (a redirect to Streamlit's own auth flow, e.g. `share.streamlit.io/-/auth/app`) — not `200`. A `200` means Step 4 didn't take effect (check the Sharing setting again) before proceeding.

- [ ] **Step 6 (you, in a browser): Sign in and confirm the review app actually loads**

Visit the app's URL, sign in when prompted (Google OAuth or the emailed single-use link, per Streamlit's own flow for invited viewers), and confirm the Streamlit app loads (it will show "Nothing to review — no pending insights." unless there's a real pending insight, which is the correct, expected state).

Expected: the app loads after sign-in, showing the same UI verified live in the Human Feedback Loop sub-project.

- [ ] **Step 7: Record this task's completion**

Ledger: `Task 4: complete (review app deployed to Streamlit Community Cloud, set to private immediately after the build finished — unauthenticated access confirmed blocked, authenticated access confirmed working; no Azure resource used, no code changes needed)`.

---

### Task 5: Document deployment in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Deployment" section**

Insert after the existing "## Running the review app" section:

```markdown
## Deployment

The pipeline (`ingest.py` → `classify.py` → `correlate.py` → `score.py`) runs automatically once daily (06:00 UTC) on an Azure Function App (`cisentinel-pipeline`, resource group `cisentinel-deploy-rg`), via `function_app.py`'s `daily_pipeline` — the exact same `run()`/`format_line()` functions each script's own `__main__` block calls, wrapped in a Timer Trigger that stops at the first stage reporting an error rather than continuing to the next. A failed run triggers an Azure Monitor alert (`cisentinel-pipeline-failure-alert`) linked to an Action Group with an email notification (delivery of that email was not confirmed live — see the plan's ledger — the alert firing itself was confirmed via both the Azure Portal and the Alerts Management API).

The review app is hosted on **Streamlit Community Cloud** (deployed from this repo's `main` branch, `review.py`) at `<the app's actual *.streamlit.app URL — fill in after Task 4>`, set to private ("Only specific people can view this app") — only explicitly invited viewers can reach it. Chosen over Azure App Service because this subscription's App Service F1 (Free) tier has a quota of 0, and the paid B1 tier was declined to avoid ongoing cost.

Redeploying after a code change:

```bash
# Pipeline
func azure functionapp publish cisentinel-pipeline
```

The review app redeploys itself automatically on every push to `main` (Streamlit Community Cloud watches the connected GitHub repo) — no manual redeploy command for it. The pipeline's redeploy command runs from the repo root and requires the Azure CLI and Azure Functions Core Tools already authenticated via `az login`. Neither this nor the review app's auto-redeploy is CI/CD in the tested/gated sense — there's no test run before either goes live, matching this PoC's single-operator, infrequent-deploy scale.
```

- [ ] **Step 2: Verify**

Run: `grep -q "cisentinel-pipeline" README.md && grep -q "streamlit.app" README.md && echo "README documents deployment"`
Expected: `README documents deployment`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document deployment"
```
