import httpx
import pytest

from fetch import _build_default_client, fetch


def test_fetch_returns_response_text_on_200():
    def handler(request):
        return httpx.Response(200, text="hello world")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch("https://example.test/page", client=client) == "hello world"


def test_fetch_raises_on_500():
    def handler(request):
        return httpx.Response(500, text="server error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://example.test/page", client=client)


def test_fetch_raises_on_404():
    def handler(request):
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://example.test/page", client=client)


def test_fetch_follows_redirects_when_client_configured_to():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "https://example.test/new"})
        return httpx.Response(200, text="final content")

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert fetch("https://example.test/old", client=client) == "final content"


def test_default_client_follows_redirects_and_sets_user_agent():
    with _build_default_client() as client:
        assert client.follow_redirects is True
        assert client.headers["user-agent"] == "Mozilla/5.0"
