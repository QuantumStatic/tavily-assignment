from __future__ import annotations

from pydantic import BaseModel, Field

from vendor_dd.engine.schemas import Dimension, EntityCard, Section


class ProjectIn(BaseModel):
    name: str


class VendorIn(BaseModel):
    name: str


class ChosenIn(BaseModel):
    chosen: bool


class ProjectOut(BaseModel):
    id: int
    name: str
    created_at: str


class VendorOut(BaseModel):
    id: int
    project_id: int
    name: str
    vendor_key: str | None = None
    created_at: str
    existed: bool = False   # True when POST /vendors returned an already-present row
    chosen: bool = False


class DimensionScore(BaseModel):
    dimension: Dimension
    score: int = Field(ge=0, le=10)
    as_of: str  # ISO datetime (fetched_at)


class VendorSummary(BaseModel):
    vendor_id: int
    name: str
    vendor_key: str | None = None
    generated: bool
    verdict_score: int | None = Field(default=None, ge=0, le=10)
    verdict_reasoning: str | None = None
    dimensions: list[DimensionScore]
    sections_present: int = 0
    sections_expected: int = 7  # keep in sync with routes.EXPECTED_SECTIONS; every route sets this explicitly
    duplicate_of: str | None = None   # earlier same-project vendor resolving to the same domain
    chosen: bool = False


class ProjectDetail(BaseModel):
    id: int
    name: str
    created_at: str
    vendors: list[VendorSummary]


class DimensionDelta(BaseModel):
    score: int
    recorded_on: str


class VendorReport(BaseModel):
    generated: bool
    vendor_key: str | None = None
    entity: EntityCard | None = None
    verdict_score: int | None = Field(default=None, ge=0, le=10)
    verdict_reasoning: str | None = None
    sections: list[Section]
    sections_present: int = 0
    sections_expected: int = 7  # keep in sync with routes.EXPECTED_SECTIONS; every route sets this explicitly
    chosen_count: int = 0
    projects_count: int = 0
    dimension_deltas: dict[str, DimensionDelta | None] = {}
