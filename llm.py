import os

from openai import OpenAI


def get_client(timeout: float = 15.0) -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY must be set (see .env.example)")
    return OpenAI(api_key=key, timeout=timeout)
