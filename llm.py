import os

from openai import OpenAI


def get_client(timeout: float = 15.0) -> OpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT must be set (see .env.example)")
    if not key:
        raise RuntimeError("AZURE_OPENAI_API_KEY must be set (see .env.example)")
    base_url = endpoint.rstrip("/") + "/openai/v1/"
    return OpenAI(api_key=key, base_url=base_url, timeout=timeout)
