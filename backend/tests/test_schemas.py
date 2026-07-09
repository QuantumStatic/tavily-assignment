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
