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
