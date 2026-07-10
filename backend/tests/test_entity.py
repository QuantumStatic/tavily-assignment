import pytest

from vendor_dd.engine.llm import LLMError
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


class _FlakyOnceLLM:
    """Raises LLMError on the first call, succeeds on the second."""

    def __init__(self):
        self.calls = 0

    def structured(self, prompt, schema):
        self.calls += 1
        if self.calls == 1:
            raise LLMError("transient")
        return schema(name="Cives Steel Company", domain="cives.com",
                      country="united states", industry="structural steel",
                      is_public=False, ticker=None, exchange=None)


class _AlwaysFailsLLM:
    def structured(self, prompt, schema):
        raise LLMError("persistent")


def test_resolve_entity_returns_entitycard():
    card = resolve_entity("Cives Steel", search=FakeSearch(), llm=FakeLLM())
    assert isinstance(card, EntityCard)
    assert card.domain == "cives.com"
    assert card.is_public is False


def test_resolve_entity_retries_once_after_llm_error():
    llm = _FlakyOnceLLM()
    card = resolve_entity("Cives Steel", search=FakeSearch(), llm=llm)
    assert isinstance(card, EntityCard)
    assert llm.calls == 2


def test_resolve_entity_propagates_llm_error_after_second_failure():
    with pytest.raises(LLMError):
        resolve_entity("Cives Steel", search=FakeSearch(), llm=_AlwaysFailsLLM())
