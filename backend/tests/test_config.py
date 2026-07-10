from datetime import timedelta
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import DIMENSION_CONFIGS, TTL, DimensionConfig


def test_every_dimension_has_config():
    for dim in Dimension:
        assert dim in DIMENSION_CONFIGS
        assert isinstance(DIMENSION_CONFIGS[dim], DimensionConfig)


def test_news_uses_general_topic_with_a_news_keyword_and_90d_window():
    # NOT the news topic — it returns recency-broad noise for low-coverage vendors.
    cfg = DIMENSION_CONFIGS[Dimension.NEWS]
    assert cfg.topic == "general"
    assert cfg.query_template == "{name} news"
    assert cfg.recency_days == 90


def test_finance_dimension_drops_keyword_stuffing():
    # finance topic scopes the search itself — the query is just the name.
    assert DIMENSION_CONFIGS[Dimension.FINANCIAL].query_template == "{name}"


def test_financial_and_backlog_use_finance_topic():
    assert DIMENSION_CONFIGS[Dimension.FINANCIAL].topic == "finance"
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
