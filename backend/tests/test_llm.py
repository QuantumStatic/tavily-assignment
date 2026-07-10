import json

from pydantic import BaseModel

from vendor_dd.engine.llm import NebiusLLM, _coerce_null_strings, _nullable_keys, _to_strict_schema
from vendor_dd.engine.schemas import Citation, Dimension, EntityCard, Finding, Section, SourceType


def test_to_strict_schema_marks_every_property_required_and_forbids_extra():
    schema = _to_strict_schema(Section.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_to_strict_schema_recurses_into_defs_and_nested_lists():
    schema = _to_strict_schema(Section.model_json_schema())
    finding_def = schema["$defs"]["Finding"]
    assert finding_def["additionalProperties"] is False
    citation_def = schema["$defs"]["Citation"]
    assert citation_def["additionalProperties"] is False
    assert set(citation_def["required"]) == set(citation_def["properties"])


def test_nullable_keys_finds_direct_and_ref_nullable_fields():
    schema = EntityCard.model_json_schema()
    keys = _nullable_keys(schema, schema.get("$defs", {}))
    assert keys == {"domain", "country", "industry", "parent", "ticker", "exchange"}
    assert "name" not in keys  # required str, not nullable
    assert "is_public" not in keys  # bool, not nullable


def test_coerce_null_strings_converts_literal_null_string_to_none():
    schema = EntityCard.model_json_schema()
    data = {"name": "Acme", "domain": "acme.com", "country": None, "industry": "steel",
            "parent": "null", "is_public": False, "ticker": "NULL", "exchange": " null "}
    out = _coerce_null_strings(dict(data), schema)
    assert out["parent"] is None
    assert out["ticker"] is None      # case-insensitive
    assert out["exchange"] is None    # whitespace-tolerant
    assert out["domain"] == "acme.com"  # untouched, not "null"


def test_coerce_null_strings_leaves_non_nullable_fields_alone():
    schema = EntityCard.model_json_schema()
    data = {"name": "null", "domain": None, "country": None, "industry": None,
            "parent": None, "is_public": False, "ticker": None, "exchange": None}
    out = _coerce_null_strings(dict(data), schema)
    assert out["name"] == "null"  # `name` isn't nullable in the schema; left as a literal string


class _FakeToolCall:
    def __init__(self, arguments: str):
        self.function = type("F", (), {"arguments": arguments})()


class _FakeMessage:
    def __init__(self, arguments: str):
        self.tool_calls = [_FakeToolCall(arguments)]


class _FakeChoice:
    def __init__(self, arguments: str):
        self.message = _FakeMessage(arguments)


class _FakeResponse:
    def __init__(self, arguments: str):
        self.choices = [_FakeChoice(arguments)]


class _RecordingClient:
    """Fake openai.OpenAI-shaped client recording the request, returning canned tool args."""

    def __init__(self, arguments: dict):
        self._arguments = arguments
        self.calls: list[dict] = []
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(json.dumps(self._arguments))


def test_structured_builds_strict_tool_and_parses_response():
    fake_client = _RecordingClient({
        "dimension": "legal", "findings": [], "reasoning": "clean", "score": 8,
    })
    llm = NebiusLLM(model="test-model", client=fake_client)
    section = llm.structured("some prompt", Section)

    assert isinstance(section, Section)
    assert section.dimension is Dimension.LEGAL
    assert section.score == 8

    call = fake_client.calls[0]
    assert call["model"] == "test-model"
    assert call["reasoning_effort"] == "none"
    assert call["tool_choice"] == {"type": "function", "function": {"name": "Section"}}
    tool = call["tools"][0]
    assert tool["function"]["strict"] is True
    assert tool["function"]["parameters"]["additionalProperties"] is False


def test_structured_coerces_literal_null_strings_in_response():
    fake_client = _RecordingClient({
        "name": "Acme", "domain": "acme.com", "country": "null", "industry": "steel",
        "parent": "null", "is_public": False, "ticker": "null", "exchange": "null",
    })
    llm = NebiusLLM(model="test-model", client=fake_client)
    entity = llm.structured("some prompt", EntityCard)

    assert entity.name == "Acme"
    assert entity.country is None
    assert entity.parent is None
    assert entity.ticker is None
    assert entity.exchange is None


def test_structured_with_nested_findings_round_trips():
    fake_client = _RecordingClient({
        "dimension": "legal",
        "findings": [{"claim": "clean record", "citation": {
            "url": "https://x.com", "title": "X", "source_type": "independent",
            "score": 0.9, "as_of": None,
        }}],
        "reasoning": "ok", "score": 7,
    })
    llm = NebiusLLM(model="test-model", client=fake_client)
    section = llm.structured("some prompt", Section)

    assert section.findings == [Finding(
        claim="clean record",
        citation=Citation(url="https://x.com", title="X",
                          source_type=SourceType.INDEPENDENT, score=0.9, as_of=None),
    )]
