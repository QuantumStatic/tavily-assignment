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
    NEWS = "news"


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
    # Short, common name the press/public actually use (e.g. "Voith Hydro", not
    # "Voith Hydro Holding GmbH & Co. KG"). Used to build search queries and filter
    # results; the full legal `name` never appears in news coverage. Falls back to
    # `name` when the model omits it.
    search_name: str | None = None
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
