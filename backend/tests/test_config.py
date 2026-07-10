from datetime import timedelta
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import DIMENSION_CONFIGS, TTL, DimensionConfig


def test_every_dimension_has_config():
    for dim in Dimension:
        assert dim in DIMENSION_CONFIGS
        assert isinstance(DIMENSION_CONFIGS[dim], DimensionConfig)


def test_news_uses_news_topic_and_90d_window():
    cfg = DIMENSION_CONFIGS[Dimension.NEWS]
    assert cfg.topic == "news"
    assert cfg.recency_days == 90


def test_topic_scoped_dimensions_drop_keyword_stuffing():
    # finance/news topics scope the search themselves — the query is just the name.
    assert DIMENSION_CONFIGS[Dimension.FINANCIAL].query_template == "{name}"
    assert DIMENSION_CONFIGS[Dimension.NEWS].query_template == "{name}"


def test_financial_and_backlog_use_finance_topic():
    assert DIMENSION_CONFIGS[Dimension.FINANCIAL].topic == "finance"
    assert DIMENSION_CONFIGS[Dimension.BACKLOG].topic == "finance"


def test_country_only_on_general_topics():
    # country param is incompatible with news/finance topics
    for dim, cfg in DIMENSION_CONFIGS.items():
        if cfg.use_country:
            assert cfg.topic == "general", f"{dim} uses country but topic={cfg.topic}"


def test_certifications_includes_own_domain_others_exclude():
    assert DIMENSION_CONFIGS[Dimension.CERTIFICATIONS].include_own_domain is True
    assert DIMENSION_CONFIGS[Dimension.LEGAL].exclude_own_domain is True


def test_ttl_covers_all_section_types():
    for dim in Dimension:
        assert dim in TTL
    assert TTL[Dimension.NEWS] == timedelta(days=1)
    assert TTL[Dimension.SNAPSHOT] == timedelta(days=30)
