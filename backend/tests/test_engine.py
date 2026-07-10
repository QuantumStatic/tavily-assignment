from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.events import (
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
)


def _entity():
    return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                      industry="steel", is_public=False)


def _section():
    return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_event_types_carry_discriminator_and_payload():
    assert EntityResolved(entity=_entity()).type == "entity_resolved"
    sc = SectionComplete(section=_section(), cached=True)
    assert sc.type == "section_complete" and sc.cached is True
    se = SectionError(dimension=Dimension.FINANCIAL, message="boom")
    assert se.type == "section_error" and se.dimension is Dimension.FINANCIAL
    rc = ReportComplete(report=Report(vendor_input="x", entity=_entity(), sections=[],
                                      verdict_score=5, verdict_reasoning="r"))
    assert rc.type == "report_complete"
    assert ReportError(message="fatal").type == "report_error"


def test_section_complete_defaults_cached_false():
    assert SectionComplete(section=_section()).cached is False
