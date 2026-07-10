from vendor_dd.engine.backlog import build_quote_url, parse_transcript_path


def test_build_quote_url_nyse():
    assert build_quote_url("NYSE", "BA") == "https://www.fool.com/quote/nyse/ba/"


def test_build_quote_url_nasdaq_and_new_york_variants():
    assert build_quote_url("NASDAQ Global Select", "MSFT") == "https://www.fool.com/quote/nasdaq/msft/"
    assert build_quote_url("New York Stock Exchange", "GE") == "https://www.fool.com/quote/nyse/ge/"


def test_build_quote_url_returns_none_when_private_or_missing():
    assert build_quote_url("NYSE", "private") is None
    assert build_quote_url("", "BA") is None
    assert build_quote_url("NYSE", "") is None


def test_parse_transcript_path_extracts_path_and_date():
    html = 'junk <a href="/earnings/call-transcripts/2026/04/16/prologis-pld-q1-2026-earnings/">x</a> junk'
    path, call_date = parse_transcript_path(html)
    assert path == "/earnings/call-transcripts/2026/04/16/prologis-pld-q1-2026-earnings"
    assert call_date == "2026-04-16"


def test_parse_transcript_path_returns_none_when_absent():
    assert parse_transcript_path("no links here") == (None, None)


import httpx

from vendor_dd.engine.backlog import fetch_transcript_text

_QUOTE_URL = "https://www.fool.com/quote/nyse/ba/"


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_transcript_text_happy_path():
    transcript_path = "/earnings/call-transcripts/2026/04/16/boeing-ba-q1-2026-earnings"
    quote_html = (f'<html><body><div class="latest-transcripts">'
                  f'<a href="{transcript_path}/">Boeing (BA) Q1 2026 Earnings Call Transcript</a>'
                  f'</div></body></html>')
    para = ("Boeing reported a strong quarter with backlog growth across all major programs, "
            "and management said demand remains robust. ") * 8
    transcript_html = (f"<html><head><title>Boeing (BA) Q1 2026 Earnings Call Transcript</title>"
                       f"</head><body><main><article>"
                       f"<h1>Boeing (BA) Q1 2026 Earnings Call Transcript</h1>"
                       f"<p>{para}</p>"
                       f"<p>Operator: Good morning and welcome to the Boeing first quarter 2026 "
                       f"earnings call. {para}</p>"
                       f"</article></main></body></html>")

    def handler(request):
        url = str(request.url)
        if url == _QUOTE_URL:
            return httpx.Response(200, text=quote_html)
        assert url == f"https://www.fool.com{transcript_path}/"
        return httpx.Response(200, text=transcript_html)

    with _mock_client(handler) as client:
        text, call_date = fetch_transcript_text(_QUOTE_URL, client=client)
    assert call_date == "2026-04-16"
    assert text is not None
    assert "backlog growth" in text


def test_fetch_transcript_text_no_transcript_link_returns_none():
    def handler(request):
        return httpx.Response(200, text="<html><body><p>No transcripts here.</p></body></html>")

    with _mock_client(handler) as client:
        assert fetch_transcript_text(_QUOTE_URL, client=client) == (None, None)


def test_fetch_transcript_text_http_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with _mock_client(handler) as client:
        assert fetch_transcript_text(_QUOTE_URL, client=client) == (None, None)
