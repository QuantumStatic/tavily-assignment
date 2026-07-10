from __future__ import annotations

from pydantic import BaseModel

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
    vendor_key: str | None
    created_at: str


class DimensionScore(BaseModel):
    dimension: Dimension
    score: int
    as_of: str  # ISO datetime (fetched_at)


class VendorSummary(BaseModel):
    vendor_id: int
    name: str
    vendor_key: str | None
    generated: bool
    verdict_score: int | None
    verdict_reasoning: str | None
    dimensions: list[DimensionScore]


class ProjectDetail(BaseModel):
    id: int
    name: str
    created_at: str
    vendors: list[VendorSummary]


class VendorReport(BaseModel):
    generated: bool
    vendor_key: str | None
    entity: EntityCard | None
    verdict_score: int | None
    verdict_reasoning: str | None
    sections: list[Section]
