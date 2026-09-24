from pathlib import Path

from extract import extract_text

FIXTURE = Path(__file__).parent / "fixtures" / "sonar_pricing.html"


def test_strips_script_and_style_tags():
    html = """
    <html><head><style>body { color: red; }</style></head>
    <body>
      <script>window.__NEXT_DATA__ = {"price": 999};</script>
      <p>Hello   world</p>
    </body></html>
    """
    result = extract_text(html)
    assert "999" not in result
    assert "color: red" not in result
    assert "Hello world" in result


def test_collapses_whitespace():
    html = "<p>Line one</p>\n\n<p>   Line   two   </p>"
    assert extract_text(html) == "Line one Line two"


def test_strips_leading_and_trailing_whitespace():
    html = "  <p>  padded  </p>  "
    result = extract_text(html)
    assert result == result.strip()


def test_real_sonar_pricing_page_has_no_script_json_leakage():
    html = FIXTURE.read_text()

    # Sanity-check the fixture itself still carries the script-embedded
    # JSON these assertions depend on catching — if a future re-capture
    # drops these markers, this fails loudly instead of the test below
    # silently passing for the wrong reason (this happened once already:
    # the fixture never contained "__NEXT_DATA__"/'"props":', the markers
    # this test originally checked for, since Sonar uses Next.js App
    # Router rather than Pages Router; that version passed vacuously).
    assert "self.__next_f" in html
    assert "AggregateOffer" in html
    assert "dataLayer" in html

    result = extract_text(html)
    assert "self.__next_f" not in result
    assert "AggregateOffer" not in result
    assert "dataLayer" not in result
    assert "1.25" in result
