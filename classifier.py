import json
import os

from openai import OpenAI

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["cosmetic", "material"]},
        "theme": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["classification", "theme", "summary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst reviewing an automated diff "
    "of a competitor's public webpage. The diff marks removed text as "
    "[-removed-] and added text as {+added+}.\n\n"
    "Classify the change:\n"
    "- material: something a sales or product team would need to know — "
    "pricing or tier changes, new/removed features, roadmap signals, "
    "positioning/messaging shifts, hiring signals, partnership announcements.\n"
    "- cosmetic: everything else — rewording, dates, style fixes, changes "
    "with no business meaning.\n\n"
    'Give a short theme (e.g. "pricing", "features", "positioning", '
    '"hiring", "other") and a one-sentence summary in plain language.'
)


def get_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY must be set (see .env.example)")
    return OpenAI(api_key=key, timeout=15.0)


def classify_diff(diff_text: str, source_type: str, client: OpenAI | None = None) -> dict:
    if client is None:
        client = get_client()
    response = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Source type: {source_type}\n\nDiff:\n{diff_text}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "signal_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            },
        },
    )
    message = response.choices[0].message
    if message.content is None:
        if message.refusal:
            raise RuntimeError(f"model refused to classify: {message.refusal}")
        raise RuntimeError("model returned no content")
    return json.loads(message.content)
