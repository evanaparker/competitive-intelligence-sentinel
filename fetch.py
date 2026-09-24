import httpx

# Matches the User-Agent used to capture tests/fixtures/sonar_pricing.html
# (see tests/test_extract.py) so production fetches and that fixture see
# the same document.
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _build_default_client() -> httpx.Client:
    return httpx.Client(timeout=15.0, follow_redirects=True, headers=DEFAULT_HEADERS)


def fetch(url: str, client: httpx.Client | None = None) -> str:
    owns_client = client is None
    if owns_client:
        client = _build_default_client()
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.text
    finally:
        if owns_client:
            client.close()
