from vendor_dd.engine.schemas import EntityCard
from vendor_dd.engine.entity import resolve_entity


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [
            {"title": "Cives Steel Company", "url": "https://cives.com",
             "content": "Cives Corporation, Alpharetta GA, structural steel", "score": 0.9},
        ]}


class FakeLLM:
    def structured(self, prompt, schema):
        return schema(name="Cives Steel Company", domain="cives.com",
                      country="united states", industry="structural steel",
                      is_public=False, ticker=None, exchange=None)


def test_resolve_entity_returns_entitycard():
    card = resolve_entity("Cives Steel", search=FakeSearch(), llm=FakeLLM())
    assert isinstance(card, EntityCard)
    assert card.domain == "cives.com"
    assert card.is_public is False
