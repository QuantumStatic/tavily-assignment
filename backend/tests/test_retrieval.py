from datetime import date
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import build_search_kwargs
from vendor_dd.engine.retrieval import retrieve_dimension


def _entity():
    return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                      industry="steel", is_public=False)


def test_legal_kwargs_use_country_exact_match_and_exclude_own_domain():
    kw = build_search_kwargs(Dimension.LEGAL, _entity(), today=date(2026, 7, 8))
    assert kw["topic"] == "general"
    assert kw["country"] == "united states"
    assert '"Cives Steel"' in kw["query"]            # exact_match wraps the name
    assert kw["exclude_domains"] == ["cives.com"]
    assert kw["start_date"] == "2024-07-08"          # today - 730 days
    assert "country" in kw


def test_news_kwargs_use_news_topic_no_country_90d_window():
    kw = build_search_kwargs(Dimension.NEWS_POSITIVE, _entity(), today=date(2026, 7, 8))
    assert kw["topic"] == "news"
    assert "country" not in kw                        # incompatible with news
    assert kw["start_date"] == "2026-04-09"           # today - 90 days


def test_certifications_include_own_domain():
    kw = build_search_kwargs(Dimension.CERTIFICATIONS, _entity(), today=date(2026, 7, 8))
    assert kw["include_domains"] == ["cives.com"]
    assert "start_date" not in kw                     # recency_days is None


class FakeSearchWithContamination:
    def search(self, **kwargs):
        return {"results": [
            {"title": "Cives Steel shutting down 130 jobs", "content": "Cives Steel Company closed",
             "url": "https://news.com/a", "score": 0.59},
            {"title": "Bayou Steel bankruptcy", "content": "Bayou Steel filed", "score": 0.18,
             "url": "https://news.com/b"},   # wrong company + low score -> dropped
        ]}


def test_retrieve_dimension_filters_contamination():
    kept = retrieve_dimension(Dimension.FINANCIAL, _entity(),
                              search=FakeSearchWithContamination(), today=date(2026, 7, 8))
    assert len(kept) == 1
    assert kept[0]["url"] == "https://news.com/a"


def test_tavily_client_logs_request_and_response(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    from vendor_dd.engine.tavily_client import TavilySearchClient
    configure_logging(tmp_path, level="INFO")

    class _FakeInner:
        def search(self, **kwargs):
            return {"results": [{"title": "t", "url": "u", "content": "c", "score": 0.5}]}

    client = TavilySearchClient.__new__(TavilySearchClient)   # bypass real API-key init
    client._client = _FakeInner()
    client.search(query="acme legal", topic="general", max_results=5)

    lines = [json.loads(l) for l in (tmp_path / "tavily.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "tavily.request" in events and "tavily.response" in events
    resp = next(o for o in lines if o["event"] == "tavily.response")
    assert resp["payload"]["result_count"] == 1
