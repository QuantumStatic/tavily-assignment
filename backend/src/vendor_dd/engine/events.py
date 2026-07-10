from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel

from vendor_dd.engine.schemas import Dimension, EntityCard, Report, Section


class EntityResolved(BaseModel):
    type: Literal["entity_resolved"] = "entity_resolved"
    entity: EntityCard


class SectionComplete(BaseModel):
    type: Literal["section_complete"] = "section_complete"
    section: Section
    cached: bool = False


class SectionError(BaseModel):
    type: Literal["section_error"] = "section_error"
    dimension: Dimension
    message: str


class ReportComplete(BaseModel):
    type: Literal["report_complete"] = "report_complete"
    report: Report


class ReportError(BaseModel):
    type: Literal["report_error"] = "report_error"
    message: str


ReportEvent = Union[
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
]
