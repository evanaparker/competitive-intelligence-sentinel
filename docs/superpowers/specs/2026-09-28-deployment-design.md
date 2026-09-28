# Deployment Design (PoC)

Status: Approved
Date: 2026-09-28
Scope: Sub-project 7 of the Competitive Intelligence Sentinel PoC — automates the existing pipeline (`ingest.py` → `classify.py` → `correlate.py` → `score.py`) to run on a daily schedule instead of manually, and hosts the review app (`review.py`) at a persistent, access-controlled URL instead of running it locally. Does not add or change any pipeline logic — every script's behavior is exactly what sub-projects 2-6 already built and merged.

## Context

Every prior sub-project's pipeline stage has only ever been run manually, one command at a time, in this development sandbox. That was sufficient to build and verify each stage in isolation, but it means the project has never accumulated real data on its own — every signal, insight, and correlation seen so far was either a real one-off manual run or scratch data seeded and cleaned up for live verification. This sub-project changes that: once deployed, the pipeline runs itself daily against the live Sonar pricing page, and a human can review and approve/reject what it finds from anywhere, without needing this sandbox at all.

This is infrastructure, not a new pipeline stage — it doesn't correspond to a PRD pipeline number the way sub-projects 2-6 did. PRD stage 7 (Delivery & Distribution) remains a separate, not-yet-built sub-project: this sub-project makes insights reachable for a human to approve; Delivery & Distribution is what would push an *approved* insight out to a channel like Slack.

## Goals

- `ingest.py` → `classify.py` → `correlate.py` → `score.py` run once daily, in that order, without manual triggering, via a single Azure Functions Timer Trigger.
- A stage's failure stops the pipeline before the next stage runs, and is visible without the operator having to go looking for it: Application Insights captures it, an Azure Monitor alert fires, and an email is sent.
- The review app (`review.py`) is reachable at a stable URL (Azure App Service) instead of only via a locally-run Streamlit process, so the human approval step in the pipeline doesn't require this sandbox to be open.
- Only the project's own operator can reach the review app and act on insights — a public, unauthenticated URL that can approve/reject real insights is not acceptable once it's off a single local machine.
- No pipeline script's logic, tests, or CLI behavior changes. `function_app.py` calls the same `run()`/`format_line()` functions the scripts already expose; it doesn't reimplement or wrap their internals.

## Non-goals (explicitly out of scope for this sub-project)

- **Any change to what the pipeline does** — ingestion, classification, correlation, scoring, and review logic are exactly what sub-projects 2-6 already built. This is packaging and scheduling, not new pipeline behavior.
- **Delivery & Distribution** (pushing approved insights to Slack/email/CRM) — PRD stage 7, a separate, later sub-project. This sub-project's email alert is an *operational* failure notification, not a business-facing insight delivery channel, and reuses none of the same code path.
- **CI/CD** (GitHub Actions or similar auto-deploying on push) — deployment is a manual `func azure functionapp publish` / `az webapp up` (or equivalent) run by the operator when there's something new to ship. Acceptable for a PoC with one operator and infrequent deploys.
- **A staging environment** — one Azure Function App, one App Service, both production from day one. There's no second environment to promote through.
- **Azure Key Vault** — credentials live in each resource's Application Settings (Azure's built-in encrypted-at-rest app configuration), not a separate Key Vault resource. Revisit only if this ever needs secret rotation or multi-service secret sharing that Application Settings can't express.
- **Multiple competitors or sources** — deployment doesn't change what's tracked. It's still the one Sonar pricing page seeded in sub-project 2; running daily just means real snapshots accumulate over time instead of only during manual test runs.
- **Retrying a failed pipeline stage automatically.** A failed run waits for the next day's scheduled trigger (or a manual re-run) — see Known Limitations.

## Architecture

```
Azure Function App (Timer Trigger, daily)
   │
   ▼
function_app.py: daily_pipeline()
   │
   ├─ _run_stage(ingest, supabase_client)                              — stop here if it raises
   ├─ _run_stage(classify, supabase_client, openai_client)             — stop here if it raises
   ├─ _run_stage(correlate, supabase_client, openai_client)            — stop here if it raises
   └─ _run_stage(score, supabase_client, openai_client)
        │
        ▼
   Supabase (unchanged — same tables every prior sub-project already uses)

Azure App Service (always-on, Easy Auth via Microsoft Entra ID)
   │
   ▼
review.py (unchanged) — reads/writes the same Supabase tables directly,
independent of whether the Function App has run yet today
```

The Function App and the App Service are two separate Azure resources, both reading/writing the same Supabase project. Neither depends on the other being deployed first, and either can be redeployed independently.

## Module Interfaces

```python
# function_app.py (new, repo root — same directory as ingest.py/classify.py/etc.
# so it can import them directly with no path manipulation)
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
    """Runs one pipeline stage's existing run(), logs its existing
    format_line() output for every result, and raises if any result
    carries a non-None error — this is what makes the Functions runtime
    mark the whole execution Failed (triggering the Azure Monitor alert)
    and is what stops daily_pipeline from calling the next stage.
    ingest.run(client) takes no openai_client; every other stage's
    run(client, openai_client=...) does — module is ingest is checked
    explicitly rather than branching on whether openai_client is set,
    since openai_client is always constructed before any stage runs."""
    results = module.run(client) if module is ingest else module.run(client, openai_client=openai_client)
    for r in results:
        logging.info(module.format_line(r))
    errors = [r for r in results if r["error"] is not None]
    if errors:
        raise RuntimeError(f"{module.__name__} reported {len(errors)} error(s): {errors[0]['error']}")


@app.timer_trigger(schedule="0 0 6 * * *", arg_name="timer", run_on_startup=False)
def daily_pipeline(timer: func.TimerRequest) -> None:
    """Runs ingest -> classify -> correlate -> score in that order, once
    daily at 06:00 UTC. Stops at the first stage that raises (via
    _run_stage) — later stages never run against a pipeline stage that
    just failed. Uses the exact get_client()/get_client() precondition
    checks every script's own __main__ block already uses; a missing
    credential fails the whole run immediately, before ingest.run() is
    ever called, exactly as it does when a script is run manually."""
    supabase_client = get_supabase_client()
    openai_client = get_openai_client()
    try:
        _run_stage(ingest, supabase_client)
        _run_stage(classify, supabase_client, openai_client)
        _run_stage(correlate, supabase_client, openai_client)
        _run_stage(score, supabase_client, openai_client)
    finally:
        openai_client.close()
```

```json
// host.json (new, repo root — required boilerplate for the Azure
// Functions Python v2 programming model; enables the Application
// Insights integration Error Handling below depends on)
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

`requirements.txt` gains one line: `azure-functions==1.25.0` (current stable release, confirmed via `pip index versions azure-functions` before writing this spec) — the only new dependency this sub-project introduces. No existing dependency changes version.

No changes to `ingest.py`, `classify.py`, `correlate.py`, `score.py`, `review.py`, `review_data.py`, or `db.py` — this sub-project only adds `function_app.py` and `host.json`, and deploys `review.py` (unchanged) to a second Azure resource.

## Error Handling

- **A stage reports one or more per-item errors** (e.g., `classify.py`'s `ERROR <url>: <message>` line): `_run_stage` raises `RuntimeError` naming the failing module and its first error. The Functions runtime marks the execution `Failed`. Later stages in `daily_pipeline` never run — `correlate.py` and `score.py` are not invoked if `classify.py` reported an error, matching the explicit choice to stop at the first failure rather than run every stage regardless.
- **A precondition fails** (missing `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_API_KEY`): `get_supabase_client()`/`get_openai_client()` raise before any stage runs, exactly as they do when a script is run manually — same `RuntimeError` messages, same precondition checks, no new code path.
- **Notification**: an Azure Monitor Alert Rule on the Function App's built-in "Failed function executions" metric, with an Action Group configured for email, notifies the operator. This requires no code in this repo — it's Azure resource configuration (Application Insights linked at Function App creation, then the alert rule and action group set up once in the Azure portal).
- **A single stage's own internal per-item error isolation is unchanged** — e.g., `classify.py` still classifies every other source even if one fails, per sub-project 3's design. `_run_stage` only raises *after* the stage's own `run()` has finished processing everything it could; it doesn't cut a stage short mid-run.

## Known Limitations

- **A failed run is not automatically retried.** The next opportunity to make progress is the following day's scheduled trigger (06:00 UTC) or a manual invocation (Azure Functions supports manually triggering a timer-triggered function from the portal or CLI for exactly this case). Acceptable for a PoC with one operator who receives the failure email and can act on it directly.
- **No lock against a manual run overlapping the scheduled one.** If the operator manually triggers `daily_pipeline` (or runs a script by hand against the same Supabase project) while the scheduled trigger is also mid-run, both share the same "no lock against concurrent runs" limitation every prior sub-project already accepted for its own script. Deployment doesn't add or remove this risk, just makes concurrent runs from two different places (a manual local run and the scheduled cloud run) newly possible.
- **The review app has no per-user audit trail beyond what `feedback`/`insights.updated_at` already record.** Easy Auth restricts *who* can reach the app; it doesn't attribute an individual approve/reject action to a specific authenticated identity inside the app's own data (the `feedback` table has no `reviewed_by` column). Acceptable for a single-operator PoC; would need a schema change to matter for a multi-reviewer team.
- **The review app runs on App Service's Free (F1) tier, without Always On.** Chosen deliberately over Basic (B1, ~$13/month) to spend no ongoing cost on an app opened occasionally to approve/reject insights — the tradeoff is a slow cold start on the first request after a period of inactivity, and (confirmed via Azure's published pricing) a **60 CPU-minute/day quota** shared across the whole app. At this PoC's usage (a human opening the app a few times a day to review a handful of insights), 60 CPU-minutes/day is not expected to bind, but if review sessions ever get long or frequent enough to hit it, the app stops responding for the rest of that day — the fix then is upgrading to Basic (B1), not a code change. Easy Auth itself has no tier restriction and works identically on Free.

## Testing

- `function_app.py`: unit tests for `_run_stage` with fake stage modules (dependency-injected via a small test double exposing `run`/`format_line`/`__name__`, not real `ingest`/`classify`/etc.) — covering: a stage with no errors doesn't raise; a stage with any error raises `RuntimeError` naming the module and the first error; `_run_stage` calls `ingest.run(client)` (no `openai_client` kwarg) specifically when the module *is* `ingest`, and `module.run(client, openai_client=...)` for every other module — following this project's existing pattern of monkeypatching a module's own imported names, not hitting real Supabase/OpenAI. `daily_pipeline` itself is not unit tested beyond confirming it calls the four stages in the correct order and stops after the first that raises (using fake stand-ins for `ingest`/`classify`/`correlate`/`score` monkeypatched onto the `function_app` module, mirroring how `score.py`'s own tests monkeypatch `db.py`'s functions) — no live Azure Functions runtime involved in the test suite.
- No local test can verify the actual Azure resources (Timer Trigger firing on schedule, Application Insights capturing a failure, the Alert Rule notifying, Easy Auth blocking an unauthenticated request, App Service serving Streamlit). These are verified live, once, after deployment — the implementation plan defines exactly how: manually trigger the deployed function once to confirm a real end-to-end run succeeds, then deliberately cause one stage to fail (e.g., a scratch source with an unreachable URL) to confirm the pipeline stops, Application Insights shows the failure, and the alert email arrives; separately, attempt to load the App Service URL unauthenticated to confirm Easy Auth blocks it, then sign in and confirm the review app loads and can approve/reject a real (or scratch, cleaned up after) insight.
