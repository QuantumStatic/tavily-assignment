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


class WrongDimensionLLM:
    """Returns a section stamped with the wrong dimension, mimicking a loosely-prompted
    model that reuses a sample section verbatim instead of echoing the requested dimension."""

    def structured(self, prompt, schema):
        return schema(
            dimension=Dimension.LEGAL,
            findings=[],
            reasoning="clean",
            score=8,
        )


def test_synthesize_section_corrects_mismatched_dimension():
    results = [{"title": "n/a", "content": "n/a", "url": "https://x.com", "score": 0.5}]
    sec = synthesize_section(Dimension.FINANCIAL, results, llm=WrongDimensionLLM())
    assert sec.dimension is Dimension.FINANCIAL


def test_assemble_verdict_averages_and_explains():
    secs = [
        Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=9),
        Section(dimension=Dimension.FINANCIAL, findings=[], reasoning="distress", score=3),
    ]
    score, reasoning = assemble_verdict(secs)
    assert score == 6            # round((9+3)/2)
    assert "financial" in reasoning.lower()   # calls out the weakest dimension
