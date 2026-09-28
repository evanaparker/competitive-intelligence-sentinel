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
