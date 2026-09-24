import httpx
import pytest

from fetch import fetch


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
