import json
from vendor_dd.engine.schemas import Dimension, EntityCard, Section
from vendor_dd.engine.events import EntityResolved, SectionComplete, SectionError
from vendor_dd.surfaces.api.sse import to_sse_frame


def test_frame_has_event_name_and_json_data():
    ev = SectionComplete(section=Section(dimension=Dimension.LEGAL, findings=[],
                                         reasoning="ok", score=8), cached=True)
    frame = to_sse_frame(ev)
    assert frame["event"] == "section_complete"
    payload = json.loads(frame["data"])
    assert payload["section"]["dimension"] == "legal"
    assert payload["cached"] is True


def test_frame_event_name_matches_type_for_each_event():
    entity = EntityCard(name="Cives", domain="cives.com", is_public=False)
    assert to_sse_frame(EntityResolved(entity=entity))["event"] == "entity_resolved"
    assert to_sse_frame(SectionError(dimension=Dimension.FINANCIAL, message="x"))["event"] \
        == "section_error"
