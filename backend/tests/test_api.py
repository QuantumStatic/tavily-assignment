from vendor_dd.engine.schemas import Dimension
from vendor_dd.surfaces.api.schemas import (
    ProjectIn, VendorIn, VendorOut, DimensionScore, VendorSummary, ProjectDetail, VendorReport,
)


def test_schemas_construct():
    assert ProjectIn(name="Bridge job").name == "Bridge job"
    assert VendorIn(name="Cives Steel").name == "Cives Steel"
    v = VendorOut(id=1, project_id=2, name="Cives", vendor_key=None, created_at="2026-07-09")
    assert v.vendor_key is None
    ds = DimensionScore(dimension=Dimension.LEGAL, score=8, as_of="2026-07-09T00:00:00+00:00")
    summ = VendorSummary(vendor_id=1, name="Cives", vendor_key="cives.com", generated=True,
                         verdict_score=6, verdict_reasoning="ok", dimensions=[ds])
    assert ProjectDetail(id=1, name="p", created_at="t", vendors=[summ]).vendors[0].generated
    assert VendorReport(generated=False, vendor_key=None, entity=None, verdict_score=None,
                        verdict_reasoning=None, sections=[]).generated is False
