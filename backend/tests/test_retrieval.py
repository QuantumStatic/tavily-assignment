from datetime import date
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import build_search_kwargs


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
