import json

from pydantic import BaseModel

from vendor_dd.engine.llm import LLMError, NebiusLLM
from vendor_dd.engine.schemas import Citation, Dimension, EntityCard, Finding, Section, SourceType


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _RecordingClient:
    """Fake openai.OpenAI-shaped client recording the request, returning canned content."""

    def __init__(self, content: dict):
        self._content = content
        self.calls: list[dict] = []
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(json.dumps(self._content))


def test_structured_builds_strict_json_schema_and_parses_response():
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
    assert "tools" not in call and "tool_choice" not in call
    rf = call["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "Section"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


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


import json as _json


def test_structured_logs_request_and_response_without_secrets(tmp_path, monkeypatch):
    from vendor_dd.logs import configure_logging
    monkeypatch.setenv("NEBIUS_API_KEY", "secret-key-xyz")
    configure_logging(tmp_path, level="INFO")

    fake_client = _RecordingClient({"dimension": "legal", "findings": [], "reasoning": "ok", "score": 8})
    llm = NebiusLLM(model="test-model", client=fake_client, api_key="secret-key-xyz")
    llm.structured("classify this", Section)

    lines = [_json.loads(l) for l in (tmp_path / "llm.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "llm.request" in events and "llm.response" in events
    blob = (tmp_path / "llm.log").read_text()
    assert "secret-key-xyz" not in blob   # api key never logged
    req = next(o for o in lines if o["event"] == "llm.request")
    assert req["payload"]["model"] == "test-model"


import pytest


class _BadClient:
    def __init__(self, resp):
        self._resp = resp
        self.chat = self
    @property
    def completions(self):
        return self
    def create(self, **kwargs):
        return self._resp


def _resp(content):
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"message": msg})()
    return type("R", (), {"choices": [choice]})()


def test_none_content_raises_clear_llm_error():
    llm = NebiusLLM(model="m", client=_BadClient(_resp(None)))
    with pytest.raises(LLMError):
        llm.structured("p", Section)


def test_empty_choices_raises_clear_llm_error():
    empty = type("R", (), {"choices": []})()
    llm = NebiusLLM(model="m", client=_BadClient(empty))
    with pytest.raises(LLMError):
        llm.structured("p", Section)


def test_malformed_json_raises_clear_llm_error():
    llm = NebiusLLM(model="m", client=_BadClient(_resp("{not json")))
    with pytest.raises(LLMError):
        llm.structured("p", Section)
