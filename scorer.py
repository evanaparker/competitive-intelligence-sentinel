import json

from openai import OpenAI

from llm import get_client

SCORING_SCHEMA = {
    "type": "object",
    "properties": {
        "materiality_score": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "needs_review"]},
        "rationale": {"type": "string"},
    },
    "required": ["materiality_score", "confidence", "rationale"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst writing a materiality "
    "assessment for a product marketing or sales team. You are given a "
    "signal — a change already classified as material — from a "
    "competitor's public webpage, including the exact diff and a brief "
    "summary.\n\n"
    "Score how materially this change affects sales conversations or "
    "product strategy, on a 1-10 scale:\n"
    "- 1-3: minor — worth having on record, low urgency\n"
    "- 4-6: moderate — sales/product should know this week\n"
    "- 7-8: significant — could affect active deals or roadmap decisions, "
    "notify soon\n"
    "- 9-10: urgent — major pricing/positioning/feature shift, likely to "
    "come up in live sales calls\n\n"
    'Set confidence to "needs_review" if the evidence is ambiguous, '
    "incomplete, or you're not confident in your assessment — never "
    'invent detail the diff doesn\'t support. Otherwise use "high", '
    '"medium", or "low" based on how clear-cut the signal is.\n\n'
    "Write a one-to-two sentence rationale in plain language that a PMM "
    "or sales rep could read directly, citing the specific evidence from "
    "the diff."
)


def score_signal(signal_context: dict, client: OpenAI | None = None) -> dict:
    if client is None:
        client = get_client()
    user_content = (
        f"Competitor: {signal_context['competitor_name']}\n"
        f"Source type: {signal_context['source_type']}\n"
        f"Theme: {signal_context['theme']}\n"
        f"Summary: {signal_context['summary']}\n\n"
        f"Diff:\n{signal_context['diff_text']}"
    )
    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "materiality_assessment",
                "strict": True,
                "schema": SCORING_SCHEMA,
            },
        },
    )
    message = response.choices[0].message
    if message.content is None:
        if message.refusal:
            raise RuntimeError(f"model refused to score: {message.refusal}")
        raise RuntimeError("model returned no content")
    return json.loads(message.content)
