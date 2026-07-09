from __future__ import annotations

import re

import httpx
import trafilatura

_TRANSCRIPT_RE = re.compile(r"/earnings/call-transcripts/(\d{4})/(\d{2})/(\d{2})/[a-z0-9-]+")


def build_quote_url(exchange: str, ticker: str) -> str | None:
    """Deterministic Motley Fool quote URL. None for private/non-US/missing (ports n8n logic)."""
    exch = (exchange or "").upper()
    ticker = (ticker or "").lower()
    if not ticker or ticker == "private":
        return None
    if "NASDAQ" in exch:
        slug = "nasdaq"
    elif "NYSE" in exch or "NEW YORK" in exch:
        slug = "nyse"
    elif exch:
        slug = exch.lower()
    else:
        return None
    return f"https://www.fool.com/quote/{slug}/{ticker}/"


def parse_transcript_path(html: str) -> tuple[str | None, str | None]:
    """Find the latest transcript path + call date (YYYY-MM-DD) from quote-page HTML."""
    m = _TRANSCRIPT_RE.search(html or "")
    if not m:
        return None, None
    return m.group(0), f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def fetch_transcript_text(quote_url: str, *, client: httpx.Client | None = None) -> tuple[str | None, str | None]:
    """GET quote page -> find transcript -> GET transcript -> clean text. Returns (text, call_date).

    Deterministic download (no Tavily): a single known static source. Returns (None, None) on any miss.
    """
    owns = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0 vendor-dd"})
    try:
        quote_html = client.get(quote_url).text
        path, call_date = parse_transcript_path(quote_html)
        if path is None:
            return None, None
        transcript_html = client.get(f"https://www.fool.com{path}/").text
        text = trafilatura.extract(transcript_html) or None
        return text, call_date
    except httpx.HTTPError:
        return None, None
    finally:
        if owns:
            client.close()
