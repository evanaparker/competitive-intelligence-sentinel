import json
from datetime import datetime, timedelta, timezone

from openai import OpenAI

from llm import get_client

WINDOW_DAYS = 7

CORRELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "correlated": {"type": "boolean"},
    },
    "required": ["correlated"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst deciding whether several "
    "signals about the same competitor, detected within a short time "
    "window, describe the same underlying business story — or are "
    "unrelated changes that happen to be close in time.\n\n"
    "You will be given each signal's source type, theme, a brief summary, "
    "and how many days apart it was detected from the others. Decide: do "
    "these signals, taken together, describe one coherent story (e.g., a "
    "price increase alongside a hiring push for enterprise sales; a new "
    "feature announcement alongside matching changelog and pricing-page "
    "updates)? Or are they unrelated coincidences?\n\n"
    "Answer true only if a reasonable analyst would write about these as "
    "one connected development. Answer false if they are plausibly "
    "unrelated, even if they involve the same competitor and theme."
)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def window_has_closed(signal: dict, window_days: int = WINDOW_DAYS) -> bool:
    return (datetime.now(timezone.utc) - _parse(signal["created_at"])) > timedelta(days=window_days)


def cluster_by_time_window(signals: list[dict], window_days: int = WINDOW_DAYS) -> list[list[dict]]:
    clusters = []
    remaining = list(signals)
    window = timedelta(days=window_days)
    while remaining:
        anchor_time = _parse(remaining[0]["created_at"])
        cluster = [s for s in remaining if _parse(s["created_at"]) - anchor_time <= window]
        clusters.append(cluster)
        remaining = [s for s in remaining if s not in cluster]
    return clusters


def judge_correlation(cluster: list[dict], client: OpenAI | None = None) -> bool:
    if client is None:
        client = get_client()
    anchor_time = _parse(cluster[0]["created_at"])
    blocks = []
    for s in cluster:
        days_apart = (_parse(s["created_at"]) - anchor_time).days
        blocks.append(
            f"Source type: {s['source_type']}\n"
            f"Theme: {s['theme']}\n"
            f"Summary: {s['summary']}\n"
            f"Days after the earliest signal: {days_apart}"
        )
    user_content = f"Competitor: {cluster[0]['competitor_name']}\n\n" + "\n\n".join(blocks)
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "correlation_judgment",
                "strict": True,
                "schema": CORRELATION_SCHEMA,
            },
        },
    )
    message = response.choices[0].message
    if message.content is None:
        if message.refusal:
            raise RuntimeError(f"model refused to judge correlation: {message.refusal}")
        raise RuntimeError("model returned no content")
    return json.loads(message.content)["correlated"]
