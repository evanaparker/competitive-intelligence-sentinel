import httpx


def fetch(url: str, client: httpx.Client | None = None) -> str:
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=15.0)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.text
    finally:
        if owns_client:
            client.close()
