from datetime import timedelta
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import DIMENSION_CONFIGS, TTL, DimensionConfig


def test_every_dimension_has_config():
    for dim in Dimension:
        assert dim in DIMENSION_CONFIGS
        assert isinstance(DIMENSION_CONFIGS[dim], DimensionConfig)


def test_news_uses_general_topic_with_a_news_keyword_and_120d_window():
    # NOT the news topic — it returns recency-broad noise for low-coverage vendors.
    cfg = DIMENSION_CONFIGS[Dimension.NEWS]
    assert cfg.topic == "general"
    assert cfg.query_template == "{name} news"
    assert cfg.recency_days == 120
    assert cfg.exclude_own_domain is False   # a vendor's own press releases are news too


def test_financial_uses_general_topic_with_financial_keywords():
    # NOT the finance topic — like news, it returns broad market noise. general topic
    # + financial keywords does real relevance matching.
    cfg = DIMENSION_CONFIGS[Dimension.FINANCIAL]
    assert cfg.topic == "general"
    assert "financial" in cfg.query_template


def test_backlog_uses_finance_topic():
    # backlog never runs its own Tavily search (transcript / news reuse), but its
    # config topic stays finance for provenance.
    assert DIMENSION_CONFIGS[Dimension.BACKLOG].topic == "finance"


def test_country_only_on_general_topics():
    # country param is incompatible with news/finance topics
    for dim, cfg in DIMENSION_CONFIGS.items():
        if cfg.use_country:
            assert cfg.topic == "general", f"{dim} uses country but topic={cfg.topic}"


def test_independent_dims_exclude_own_domain_certs_do_not_restrict():
    assert DIMENSION_CONFIGS[Dimension.LEGAL].exclude_own_domain is True
    # certs are unrestricted: neither forced to nor away from the vendor's own domain,
    # so independent registrar listings can corroborate self-reported certs.
    assert DIMENSION_CONFIGS[Dimension.CERTIFICATIONS].exclude_own_domain is False


def test_ttl_covers_all_section_types():
    for dim in Dimension:
        assert dim in TTL
    assert TTL[Dimension.NEWS] == timedelta(days=1)
    assert TTL[Dimension.SNAPSHOT] == timedelta(days=30)
