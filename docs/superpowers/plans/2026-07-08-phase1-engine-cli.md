# Vendor Due-Diligence — Phase 1: Engine + CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable CLI that takes a vendor name and prints a structured, cited, scored due-diligence report, powered by a surface-agnostic engine (entity resolution → per-dimension Tavily retrieval → filter → LLM synthesis → verdict), with an SQLite per-section TTL cache and the deterministic Motley Fool backlog path.

**Architecture:** One `engine` package with focused modules (schemas, config, cache, retrieval, backlog, synthesis, pipeline) and thin `surfaces` (CLI here). Pure logic (schemas, config selection, URL/regex, score filter, TTL, verdict) is unit-tested with TDD; I/O components (Tavily, LLM) are tested with fakes injected via constructor params.

**Tech Stack:** Python 3.11+, Pydantic v2, `tavily-python`, `langchain` + `langchain-nebius` (LLM structured output), `httpx` + `trafilatura` (backlog), SQLite (stdlib `sqlite3`), Typer + Rich (CLI), pytest.

Spec: `docs/superpowers/specs/2026-07-08-vendor-due-diligence-design.md`

---

## File structure (Phase 1)

```
backend/
  pyproject.toml                     # add httpx, trafilatura, tavily-python (already has langchain, etc.)
  src/vendor_dd/
    engine/
      schemas.py      # Pydantic models + enums (Dimension, SourceType, Citation, Finding, Section, EntityCard, Report)
      config.py       # DIMENSION_CONFIGS (per-dimension retrieval params) + TTL policy dict
      cache.py        # SQLiteCache: get/put by (vendor_key, section_type) with code-side TTL
      backlog.py      # pure: build_quote_url(), parse_transcript_path(); io: fetch_transcript_text()
      filtering.py    # pure: passes_filter() (score threshold + entity name verification)
      tavily_client.py# SearchClient protocol + TavilySearchClient wrapper (search per dimension)
      llm.py          # LLMClient protocol + NebiusLLM (structured output) factory
      entity.py       # resolve_entity(name, search, llm) -> EntityCard
      retrieval.py    # retrieve_dimension(...) + retrieve_all(...) (parallel), applies filtering
      synthesis.py    # synthesize_section(...) -> Section ; assemble_verdict(sections) -> (score, reasoning)
      pipeline.py     # run_report(vendor, deps) -> Report  (orchestrates everything + cache)
    surfaces/
      cli.py          # Typer app: `vendor-dd "Boeing"` -> render Report with Rich
  tests/
    test_schemas.py  test_config.py  test_cache.py  test_backlog.py
    test_filtering.py  test_entity.py  test_retrieval.py  test_synthesis.py
    test_pipeline.py
```

**Runnable milestones:**
- After Task 8 (synthesis + verdict): engine produces a Report from faked deps (fully unit-tested).
- After Task 10 (pipeline): `run_report` works end-to-end with fakes.
- After Task 11 (CLI): `uv run vendor-dd "Boeing"` prints a real report.

---

## Task 1: Schemas (enums + Pydantic models)

**Files:**
- Create: `backend/src/vendor_dd/engine/schemas.py`
- Test: `backend/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_schemas.py
import pytest
from pydantic import ValidationError
from vendor_dd.engine.schemas import (
    Dimension, SourceType, Citation, Finding, Section, EntityCard, Report,
)


def test_citation_roundtrips():
    c = Citation(url="https://x.com", title="T", source_type=SourceType.INDEPENDENT,
                 score=0.8, as_of="2026-01-01")
    assert c.source_type is SourceType.INDEPENDENT
    assert c.score == 0.8


def test_section_score_bounds_enforced():
    findings = [Finding(claim="c", citation=Citation(
        url="u", title="t", source_type=SourceType.SELF_REPORTED, score=0.5))]
    with pytest.raises(ValidationError):
        Section(dimension=Dimension.LEGAL, findings=findings, reasoning="r", score=11)


def test_report_holds_sections_and_verdict():
    entity = EntityCard(name="Acme", domain="acme.com", country="united states",
                        industry="steel", is_public=False)
    r = Report(vendor_input="Acme", entity=entity, sections=[],
               verdict_score=7, verdict_reasoning="ok")
    assert r.verdict_score == 7
    assert r.entity.ticker is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.schemas`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/schemas.py
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Dimension(str, Enum):
    SNAPSHOT = "snapshot"
    LEGAL = "legal"
    SAFETY = "safety"
    FINANCIAL = "financial"
    BACKLOG = "backlog"
    CERTIFICATIONS = "certifications"
    NEWS_POSITIVE = "news_positive"
    NEWS_NEGATIVE = "news_negative"


class SourceType(str, Enum):
    INDEPENDENT = "independent"
    SELF_REPORTED = "self_reported"


class Citation(BaseModel):
    url: str
    title: str
    source_type: SourceType
    score: float
    as_of: str | None = None  # ISO date; None when unknown


class Finding(BaseModel):
    claim: str
    citation: Citation


class Section(BaseModel):
    dimension: Dimension
    findings: list[Finding]
    reasoning: str                       # hover text
    score: int = Field(ge=0, le=10)      # 10 = all good, 0 = problematic


class EntityCard(BaseModel):
    name: str
    domain: str | None = None
    country: str | None = None
    industry: str | None = None
    parent: str | None = None
    is_public: bool = False
    ticker: str | None = None
    exchange: str | None = None


class Report(BaseModel):
    vendor_input: str
    entity: EntityCard
    sections: list[Section]
    verdict_score: int = Field(ge=0, le=10)
    verdict_reasoning: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/schemas.py backend/tests/test_schemas.py
git commit -m "feat(engine): pydantic schemas for report/section/finding/entity"
```

---

## Task 2: Per-dimension config + TTL policy

**Files:**
- Create: `backend/src/vendor_dd/engine/config.py`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
from datetime import timedelta
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import DIMENSION_CONFIGS, TTL, DimensionConfig


def test_every_dimension_has_config():
    for dim in Dimension:
        assert dim in DIMENSION_CONFIGS
        assert isinstance(DIMENSION_CONFIGS[dim], DimensionConfig)


def test_news_uses_news_topic_and_90d_window():
    for dim in (Dimension.NEWS_POSITIVE, Dimension.NEWS_NEGATIVE):
        cfg = DIMENSION_CONFIGS[dim]
        assert cfg.topic == "news"
        assert cfg.recency_days == 90


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
    assert TTL[Dimension.NEWS_POSITIVE] == timedelta(days=1)
    assert TTL[Dimension.SNAPSHOT] == timedelta(days=30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.config`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/config.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from vendor_dd.engine.schemas import Dimension


@dataclass(frozen=True)
class DimensionConfig:
    query_template: str        # "{name} {geo} lawsuit litigation" — geo filled at runtime
    topic: str                 # "general" | "news" | "finance"
    search_depth: str          # "basic" | "advanced"
    max_results: int
    recency_days: int | None   # None = no time filter; else start_date = today - N days
    use_country: bool          # country param (general topic only)
    exact_match: bool          # wrap canonical name in quotes
    include_own_domain: bool   # certs: self-reported is the answer
    exclude_own_domain: bool   # independent dims: force third-party sources


DIMENSION_CONFIGS: dict[Dimension, DimensionConfig] = {
    Dimension.SNAPSHOT: DimensionConfig(
        "{name} company overview headquarters industry", "general", "advanced",
        5, None, True, False, False, False),
    Dimension.LEGAL: DimensionConfig(
        "{name} {geo} lawsuit litigation legal action", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.SAFETY: DimensionConfig(
        "{name} {geo} product recall safety defect investigation", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.FINANCIAL: DimensionConfig(
        "{name} {geo} layoffs bankruptcy financial trouble downgrade", "finance", "advanced",
        6, 365, False, True, False, True),
    Dimension.BACKLOG: DimensionConfig(
        "{name} {geo} backlog order book project pipeline", "finance", "advanced",
        5, 365, False, True, False, False),
    Dimension.CERTIFICATIONS: DimensionConfig(
        "{name} ISO AISC certification compliance quality", "general", "basic",
        3, None, True, False, True, False),
    Dimension.NEWS_POSITIVE: DimensionConfig(
        "{name} {geo} contract award partnership expansion", "news", "basic",
        8, 90, False, True, False, True),
    Dimension.NEWS_NEGATIVE: DimensionConfig(
        "{name} {geo} controversy incident dispute closure", "news", "basic",
        8, 90, False, True, False, True),
}

# TTL policy lives in code, not in the cache row (tunable without migration).
TTL: dict[Dimension, timedelta] = {
    Dimension.SNAPSHOT: timedelta(days=30),
    Dimension.CERTIFICATIONS: timedelta(days=30),
    Dimension.FINANCIAL: timedelta(days=7),
    Dimension.BACKLOG: timedelta(days=7),
    Dimension.LEGAL: timedelta(days=3),
    Dimension.SAFETY: timedelta(days=3),
    Dimension.NEWS_POSITIVE: timedelta(days=1),
    Dimension.NEWS_NEGATIVE: timedelta(days=1),
}

SCORE_THRESHOLD = 0.40  # drop Tavily results below this (anti-contamination)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/config.py backend/tests/test_config.py
git commit -m "feat(engine): per-dimension retrieval config + TTL policy"
```

---

## Task 3: SQLite TTL cache

**Files:**
- Create: `backend/src/vendor_dd/engine/cache.py`
- Test: `backend/tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_cache.py
from datetime import datetime, timedelta, timezone
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.cache import SQLiteCache


def _now():
    return datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def test_put_then_get_fresh_returns_payload(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5})
    got = cache.get("acme.com", Dimension.LEGAL)
    assert got == {"score": 5}


def test_get_expired_returns_none(tmp_path):
    t = {"now": _now()}
    cache = SQLiteCache(tmp_path / "c.db", clock=lambda: t["now"])
    cache.put("acme.com", Dimension.NEWS_POSITIVE, {"score": 9})  # TTL = 1 day
    t["now"] = _now() + timedelta(days=2)                          # advance past TTL
    assert cache.get("acme.com", Dimension.NEWS_POSITIVE) is None


def test_get_missing_returns_none(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    assert cache.get("acme.com", Dimension.LEGAL) is None


def test_fetched_at_exposed_for_as_of(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.SNAPSHOT, {"x": 1})
    assert cache.fetched_at("acme.com", Dimension.SNAPSHOT) == _now()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.cache`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/cache.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import TTL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteCache:
    """Per-(vendor_key, section_type) cache. TTL is applied on read from config.TTL."""

    def __init__(self, path: str | Path, clock: Callable[[], datetime] = _utcnow):
        self._clock = clock
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS report_cache (
                 vendor_key TEXT NOT NULL,
                 section_type TEXT NOT NULL,
                 content TEXT NOT NULL,
                 fetched_at TEXT NOT NULL,
                 PRIMARY KEY (vendor_key, section_type)
               )"""
        )
        self._conn.commit()

    def put(self, vendor_key: str, section: Dimension, content: dict[str, Any]) -> None:
        self._conn.execute(
            "REPLACE INTO report_cache VALUES (?,?,?,?)",
            (vendor_key, section.value, json.dumps(content), self._clock().isoformat()),
        )
        self._conn.commit()

    def _row(self, vendor_key: str, section: Dimension) -> tuple[str, datetime] | None:
        cur = self._conn.execute(
            "SELECT content, fetched_at FROM report_cache WHERE vendor_key=? AND section_type=?",
            (vendor_key, section.value),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row[0], datetime.fromisoformat(row[1])

    def get(self, vendor_key: str, section: Dimension) -> dict[str, Any] | None:
        row = self._row(vendor_key, section)
        if row is None:
            return None
        content, fetched_at = row
        if self._clock() - fetched_at >= TTL[section]:
            return None  # stale
        return json.loads(content)

    def fetched_at(self, vendor_key: str, section: Dimension) -> datetime | None:
        row = self._row(vendor_key, section)
        return row[1] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_cache.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/cache.py backend/tests/test_cache.py
git commit -m "feat(engine): sqlite per-section TTL cache"
```

---

## Task 4: Backlog — quote URL + transcript path (pure logic first)

**Files:**
- Create: `backend/src/vendor_dd/engine/backlog.py`
- Test: `backend/tests/test_backlog.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_backlog.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_backlog.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.backlog`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/backlog.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_backlog.py -v`
Expected: PASS (5 tests). `fetch_transcript_text` is not unit-tested (network I/O); it is exercised via the pipeline with a fake client in Task 10.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/backlog.py backend/tests/test_backlog.py
git commit -m "feat(engine): deterministic motley-fool backlog url + transcript parsing"
```

---

## Task 5: Result filtering (score threshold + entity verification)

**Files:**
- Create: `backend/src/vendor_dd/engine/filtering.py`
- Test: `backend/tests/test_filtering.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_filtering.py
from vendor_dd.engine.filtering import passes_filter, verify_entity


def _result(score=0.9, title="Cives Steel wins award", content="Cives Steel Company ..."):
    return {"score": score, "title": title, "content": content, "url": "https://x.com"}


def test_low_score_is_dropped():
    assert passes_filter(_result(score=0.2), entity_name="Cives Steel") is False


def test_high_score_with_entity_mention_passes():
    assert passes_filter(_result(score=0.7), entity_name="Cives Steel") is True


def test_entity_not_mentioned_is_dropped_even_if_high_score():
    r = _result(score=0.9, title="U.S. Steel EEOC lawsuit", content="U.S. Steel violated ...")
    assert passes_filter(r, entity_name="Cives Steel") is False


def test_verify_entity_is_case_insensitive_and_checks_title_or_content():
    assert verify_entity({"title": "CIVES STEEL", "content": ""}, "Cives Steel") is True
    assert verify_entity({"title": "", "content": "about cives steel co"}, "Cives Steel") is True
    assert verify_entity({"title": "Nucor", "content": "steel"}, "Cives Steel") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_filtering.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.filtering`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/filtering.py
from __future__ import annotations

from typing import Any

from vendor_dd.engine.config import SCORE_THRESHOLD


def verify_entity(result: dict[str, Any], entity_name: str) -> bool:
    """True if the result's title or content actually names the entity (case-insensitive)."""
    hay = f"{result.get('title', '')} {result.get('content', '')}".lower()
    return entity_name.lower() in hay


def passes_filter(result: dict[str, Any], entity_name: str,
                  threshold: float = SCORE_THRESHOLD) -> bool:
    """Anti-contamination gate: drop weak scores and wrong-company results."""
    if result.get("score", 0.0) < threshold:
        return False
    return verify_entity(result, entity_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_filtering.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/filtering.py backend/tests/test_filtering.py
git commit -m "feat(engine): score-threshold + entity-verification filtering"
```

---

## Task 6: Tavily search client wrapper

**Files:**
- Create: `backend/src/vendor_dd/engine/tavily_client.py`
- Test: `backend/tests/test_retrieval.py` (shared with Task 7; this task adds `test_build_search_kwargs_*`)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_retrieval.py
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
    assert kw["start_date"] == "2024-07-09"          # today - 730 days
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_retrieval.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.tavily_client`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/tavily_client.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from tavily import TavilyClient

from vendor_dd.engine.config import DIMENSION_CONFIGS
from vendor_dd.engine.schemas import Dimension, EntityCard


class SearchClient(Protocol):
    def search(self, **kwargs: Any) -> dict[str, Any]: ...


def build_search_kwargs(dim: Dimension, entity: EntityCard, *, today: date) -> dict[str, Any]:
    """Translate a dimension + entity card into Tavily search params (docs-backed)."""
    cfg = DIMENSION_CONFIGS[dim]
    geo = entity.country or "" if not cfg.use_country else ""  # geo in query only when country unusable
    name = f'"{entity.name}"' if cfg.exact_match else entity.name
    query = cfg.query_template.format(name=name, geo=geo).replace("  ", " ").strip()

    kwargs: dict[str, Any] = {
        "query": query,
        "topic": cfg.topic,
        "search_depth": cfg.search_depth,
        "max_results": cfg.max_results,
    }
    if cfg.use_country and entity.country:
        kwargs["country"] = entity.country
    if cfg.recency_days is not None:
        kwargs["start_date"] = (today - timedelta(days=cfg.recency_days)).isoformat()
    if cfg.exclude_own_domain and entity.domain:
        kwargs["exclude_domains"] = [entity.domain]
    if cfg.include_own_domain and entity.domain:
        kwargs["include_domains"] = [entity.domain]
    return kwargs


class TavilySearchClient:
    """Thin wrapper implementing SearchClient over tavily-python."""

    def __init__(self, api_key: str):
        self._client = TavilyClient(api_key=api_key)

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.search(**kwargs)
```

Note: for news/finance dims `use_country` is False, so `geo` = `entity.country` and gets folded into the query; for general dims `use_country` is True, so `geo` = "" and the country param is used instead. This matches the spec's country/topic split.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_retrieval.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/tavily_client.py backend/tests/test_retrieval.py
git commit -m "feat(engine): tavily client wrapper + per-dimension search kwargs"
```

---

## Task 7: LLM client + entity resolution

**Files:**
- Create: `backend/src/vendor_dd/engine/llm.py`, `backend/src/vendor_dd/engine/entity.py`
- Test: `backend/tests/test_entity.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_entity.py
from vendor_dd.engine.schemas import EntityCard
from vendor_dd.engine.entity import resolve_entity


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [
            {"title": "Cives Steel Company", "url": "https://cives.com",
             "content": "Cives Corporation, Alpharetta GA, structural steel", "score": 0.9},
        ]}


class FakeLLM:
    def structured(self, prompt, schema):
        return schema(name="Cives Steel Company", domain="cives.com",
                      country="united states", industry="structural steel",
                      is_public=False, ticker=None, exchange=None)


def test_resolve_entity_returns_entitycard():
    card = resolve_entity("Cives Steel", search=FakeSearch(), llm=FakeLLM())
    assert isinstance(card, EntityCard)
    assert card.domain == "cives.com"
    assert card.is_public is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_entity.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.entity`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/llm.py
from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


class NebiusLLM:
    """LangChain + Nebius structured-output client."""

    def __init__(self, model: str = "moonshotai/Kimi-K2.6"):
        from langchain_nebius import ChatNebius
        self._model = ChatNebius(model=model)

    def structured(self, prompt: str, schema: type[T]) -> T:
        return self._model.with_structured_output(schema).invoke(prompt)
```

```python
# backend/src/vendor_dd/engine/entity.py
from __future__ import annotations

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import EntityCard
from vendor_dd.engine.tavily_client import SearchClient

_PROMPT = """Given these web search results about a company called "{name}", extract its
canonical identity. Set is_public=true only if it is publicly traded; if so include ticker
and exchange (e.g. "NYSE"), else ticker/exchange null.

Search results:
{results}
"""


def resolve_entity(name: str, *, search: SearchClient, llm: LLMClient) -> EntityCard:
    resp = search.search(query=f"{name} company overview headquarters industry ticker",
                         topic="general", search_depth="advanced", max_results=5)
    results = "\n".join(
        f"- {r.get('title','')}: {r.get('content','')} ({r.get('url','')})"
        for r in resp.get("results", [])
    )
    return llm.structured(_PROMPT.format(name=name, results=results), EntityCard)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_entity.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/llm.py backend/src/vendor_dd/engine/entity.py backend/tests/test_entity.py
git commit -m "feat(engine): llm client protocol + entity resolution"
```

---

## Task 8: Retrieval orchestration (per-dimension, with filtering)

**Files:**
- Modify: `backend/src/vendor_dd/engine/retrieval.py` (create)
- Test: `backend/tests/test_retrieval.py` (append)

- [ ] **Step 1: Write the failing test (append to test_retrieval.py)**

```python
# append to backend/tests/test_retrieval.py
from datetime import date
from vendor_dd.engine.retrieval import retrieve_dimension


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_retrieval.py::test_retrieve_dimension_filters_contamination -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.retrieval`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/retrieval.py
from __future__ import annotations

from datetime import date
from typing import Any

from vendor_dd.engine.filtering import passes_filter
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import SearchClient, build_search_kwargs


def retrieve_dimension(dim: Dimension, entity: EntityCard, *,
                       search: SearchClient, today: date) -> list[dict[str, Any]]:
    kwargs = build_search_kwargs(dim, entity, today=today)
    resp = search.search(**kwargs)
    return [r for r in resp.get("results", []) if passes_filter(r, entity.name)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_retrieval.py -v`
Expected: PASS (4 tests total in file)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/retrieval.py backend/tests/test_retrieval.py
git commit -m "feat(engine): per-dimension retrieval with contamination filtering"
```

---

## Task 9: Synthesis (results → Section) + verdict assembly

**Files:**
- Create: `backend/src/vendor_dd/engine/synthesis.py`
- Test: `backend/tests/test_synthesis.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_synthesis.py
from vendor_dd.engine.schemas import Dimension, Section, Finding, Citation, SourceType
from vendor_dd.engine.synthesis import synthesize_section, assemble_verdict


class FakeLLM:
    def structured(self, prompt, schema):
        return schema(
            dimension=Dimension.FINANCIAL,
            findings=[Finding(claim="Closed a plant in 2020",
                              citation=Citation(url="https://news.com/a", title="closure",
                                                source_type=SourceType.INDEPENDENT, score=0.59,
                                                as_of="2020-09-18"))],
            reasoning="Plant closure indicates distress",
            score=3,
        )


def test_synthesize_section_returns_section():
    results = [{"title": "closure", "content": "Cives closed", "url": "https://news.com/a", "score": 0.59}]
    sec = synthesize_section(Dimension.FINANCIAL, results, llm=FakeLLM())
    assert isinstance(sec, Section)
    assert sec.score == 3
    assert sec.findings[0].citation.url == "https://news.com/a"


def test_assemble_verdict_averages_and_explains():
    secs = [
        Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=9),
        Section(dimension=Dimension.FINANCIAL, findings=[], reasoning="distress", score=3),
    ]
    score, reasoning = assemble_verdict(secs)
    assert score == 6            # round((9+3)/2)
    assert "financial" in reasoning.lower()   # calls out the weakest dimension
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_synthesis.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.synthesis`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/synthesis.py
from __future__ import annotations

from typing import Any

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import Dimension, Section

_PROMPT = """You are a due-diligence analyst. From these search results about the vendor,
produce the "{dimension}" section. Rules:
- Emit findings FIRST (each a claim + its citation url/title/score), THEN reasoning, THEN score.
- score is 0-10 where 10 = all good / confident to use, 0 = serious problems.
- Only use claims supported by a result; set source_type=self_reported if the source is the
  vendor's own site, else independent.
- If coverage is thin, do NOT award a confident high score; say so in reasoning.

Results:
{results}
"""


def synthesize_section(dim: Dimension, results: list[dict[str, Any]], *, llm: LLMClient) -> Section:
    rendered = "\n".join(
        f"- score={r.get('score')} | {r.get('title','')} | {r.get('url','')} | {r.get('content','')}"
        for r in results
    ) or "(no results found)"
    return llm.structured(_PROMPT.format(dimension=dim.value, results=rendered), Section)


def assemble_verdict(sections: list[Section]) -> tuple[int, str]:
    if not sections:
        return 5, "Insufficient data to assess."
    avg = round(sum(s.score for s in sections) / len(sections))
    weakest = min(sections, key=lambda s: s.score)
    reasoning = (
        f"Overall {avg}/10. Weakest area: {weakest.dimension.value} "
        f"({weakest.score}/10) — {weakest.reasoning}"
    )
    return avg, reasoning
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_synthesis.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/synthesis.py backend/tests/test_synthesis.py
git commit -m "feat(engine): llm section synthesis + verdict assembly"
```

---

## Task 10: Pipeline orchestration (entity → retrieve → backlog → synth → cache)

**Files:**
- Create: `backend/src/vendor_dd/engine/pipeline.py`
- Test: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pipeline.py
from datetime import date
from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.pipeline import run_report, Deps


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class FakeLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_run_report_returns_report_with_all_dimensions(tmp_path):
    deps = Deps(search=FakeSearch(), llm=FakeLLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    report = run_report("Cives Steel", deps)
    assert isinstance(report, Report)
    assert report.entity.domain == "cives.com"
    # one section per non-snapshot dimension
    assert {s.dimension for s in report.sections} == {
        d for d in Dimension if d is not Dimension.SNAPSHOT}
    assert 0 <= report.verdict_score <= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.pipeline`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from vendor_dd.engine.backlog import build_quote_url
from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.entity import resolve_entity
from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.retrieval import retrieve_dimension
from vendor_dd.engine.schemas import Dimension, Report, Section
from vendor_dd.engine.synthesis import assemble_verdict, synthesize_section
from vendor_dd.engine.tavily_client import SearchClient

# dimensions retrieved via Tavily (snapshot is entity resolution; backlog is special-cased)
_TAVILY_DIMS = [d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)]


@dataclass
class Deps:
    search: SearchClient
    llm: LLMClient
    cache_path: Path
    today: date
    fetch_transcript: Callable[[str], tuple[str | None, str | None]]


def run_report(vendor: str, deps: Deps) -> Report:
    entity = resolve_entity(vendor, search=deps.search, llm=deps.llm)
    vendor_key = entity.domain or entity.name.lower()
    cache = SQLiteCache(deps.cache_path)
    sections: list[Section] = []

    for dim in _TAVILY_DIMS:
        cached = cache.get(vendor_key, dim)
        if cached is not None:
            sections.append(Section.model_validate(cached))
            continue
        results = retrieve_dimension(dim, entity, search=deps.search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        cache.put(vendor_key, dim, section.model_dump(mode="json"))
        sections.append(section)

    sections.append(_backlog_section(entity, deps, cache, vendor_key))

    score, reasoning = assemble_verdict(sections)
    return Report(vendor_input=vendor, entity=entity, sections=sections,
                  verdict_score=score, verdict_reasoning=reasoning)


def _backlog_section(entity, deps, cache, vendor_key) -> Section:
    cached = cache.get(vendor_key, Dimension.BACKLOG)
    if cached is not None:
        return Section.model_validate(cached)

    results: list[dict] = []
    if entity.is_public and entity.ticker and entity.exchange:
        quote_url = build_quote_url(entity.exchange, entity.ticker)
        if quote_url:
            text, call_date = deps.fetch_transcript(quote_url)
            if text:
                results = [{"title": "Earnings call transcript", "url": quote_url,
                            "content": text[:6000], "score": 1.0, "as_of": call_date}]
    if not results:  # private or no transcript -> reuse positive-news as a backlog proxy
        results = retrieve_dimension(Dimension.NEWS_POSITIVE, entity,
                                     search=deps.search, today=deps.today)
    section = synthesize_section(Dimension.BACKLOG, results, llm=deps.llm)
    cache.put(vendor_key, Dimension.BACKLOG, section.model_dump(mode="json"))
    return section
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: PASS (1 test). Run full suite: `uv run pytest -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat(engine): pipeline orchestration with cache + backlog routing"
```

---

## Task 11: CLI surface

**Files:**
- Create: `backend/src/vendor_dd/surfaces/cli.py`
- Modify: `backend/pyproject.toml` (add `httpx`, `trafilatura`; add `[project.scripts]`)
- Test: manual run (CLI wiring; engine already covered by unit tests)

- [ ] **Step 1: Add deps + script entry to pyproject.toml**

Add to `[project].dependencies`: `"httpx>=0.27.0"`, `"trafilatura>=1.8.0"`.
Add section:
```toml
[project.scripts]
vendor-dd = "vendor_dd.surfaces.cli:app"
```

- [ ] **Step 2: Write the CLI**

```python
# backend/src/vendor_dd/surfaces/cli.py
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vendor_dd.engine.backlog import fetch_transcript_text
from vendor_dd.engine.llm import NebiusLLM
from vendor_dd.engine.pipeline import Deps, run_report
from vendor_dd.engine.tavily_client import TavilySearchClient

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(vendor: str) -> None:
    """Generate a due-diligence report for VENDOR."""
    tavily_key, nebius_key = os.getenv("TAVILY_API_KEY"), os.getenv("NEBIUS_API_KEY")
    if not tavily_key or not nebius_key:
        console.print("[red]Set TAVILY_API_KEY and NEBIUS_API_KEY in .env[/red]")
        raise typer.Exit(1)

    deps = Deps(
        search=TavilySearchClient(tavily_key),
        llm=NebiusLLM(),
        cache_path=Path(".vendor_dd_cache.db"),
        today=date.today(),
        fetch_transcript=fetch_transcript_text,
    )
    with console.status(f"Researching {vendor}..."):
        report = run_report(vendor, deps)

    e = report.entity
    console.print(Panel.fit(
        f"[bold]{e.name}[/bold]  ·  {e.industry or '?'}  ·  {e.country or '?'}  "
        f"·  {'public ' + (e.ticker or '') if e.is_public else 'private'}",
        title=f"Verdict {report.verdict_score}/10", border_style="cyan"))
    console.print(report.verdict_reasoning + "\n")

    table = Table("Dimension", "Score", "Reasoning")
    for s in sorted(report.sections, key=lambda s: s.score):  # riskiest first
        color = "green" if s.score >= 7 else "yellow" if s.score >= 4 else "red"
        table.add_row(s.dimension.value, f"[{color}]{s.score}/10[/{color}]", s.reasoning[:80])
    console.print(table)

    for s in report.sections:
        for f in s.findings:
            c = f.citation
            console.print(f"  [{s.dimension.value}] {f.claim}")
            console.print(f"     [dim]{c.url} · {c.source_type.value} · as of {c.as_of or 'n/a'}[/dim]")


if __name__ == "__main__":
    app()
```

- [ ] **Step 3: Run it end-to-end (real keys)**

Run: `cd backend && uv run vendor-dd "Boeing"`
Expected: a verdict panel, a sorted dimension table (riskiest first, color-coded), and cited findings. ~13 credits on first run; a re-run within a day is faster/cheaper (cache hits on long-TTL sections).

- [ ] **Step 4: Verify cache works**

Run the same command again immediately.
Expected: noticeably faster; only short-TTL dims (news) re-fetch. Confirm `.vendor_dd_cache.db` exists.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/cli.py backend/pyproject.toml
git commit -m "feat(cli): vendor-dd command renders cited scored report"
```

---

## Phase 1 done — what's next (separate plans)

- **Phase 2:** FastAPI + SSE API over `run_report` (stream sections as they complete) + project/vendor persistence.
- **Phase 3:** React frontend — project comparison table (scores, hover=reasoning, click=report), chat over cache.
- **Phase 4:** Eval harness (gold set + citation-support judge + contamination-rate) and LangSmith/OTel tracing.
- **Phase 5:** MCP server exposing `check_vendor(name)`.

---

## Self-review notes

- **Spec coverage (Phase 1 scope):** schemas ✓, per-dimension config incl. country/topic split ✓, exact_match ✓,
  score threshold + entity verification ✓, finance/news topics + recency ✓, deterministic backlog (quote URL →
  regex → GET → trafilatura) ✓, public/private backlog routing ✓, snippets-only synthesis with cited claims +
  source_type + 0–10 score + evidence-first prompt + thin-coverage honesty ✓, verdict assembly ✓, SQLite
  per-section TTL cache keyed by domain with fetched_at ✓, CLI render (sorted riskiest-first, colors) ✓.
  Deferred to later phases (documented): API/SSE, frontend, chat, evals, tracing, MCP, Extract grounding,
  adaptive follow-up, `session_id` correlation, `published_date`→as_of for news (synthesis can pass it later).
- **Type consistency:** `Deps`, `SearchClient.search(**kwargs)`, `LLMClient.structured(prompt, schema)`,
  `Section.model_dump(mode="json")`/`model_validate`, `build_quote_url`, `parse_transcript_path`,
  `fetch_transcript_text` used consistently across tasks 6–11.
- **No placeholders:** every code step is complete and runnable.
