from __future__ import annotations

from pydantic import BaseModel, Field

from vendor_dd.engine.schemas import Dimension, EntityCard, Section


class ProjectIn(BaseModel):
    name: str


class VendorIn(BaseModel):
    name: str


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


class ProjectDetail(BaseModel):
    id: int
    name: str
    created_at: str
    vendors: list[VendorSummary]


class VendorReport(BaseModel):
    generated: bool
    vendor_key: str | None = None
    entity: EntityCard | None = None
    verdict_score: int | None = Field(default=None, ge=0, le=10)
    verdict_reasoning: str | None = None
    sections: list[Section]
