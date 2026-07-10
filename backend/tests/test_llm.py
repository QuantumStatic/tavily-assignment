import json as _json

import httpx
import pytest
from openai import APIError, APITimeoutError

from vendor_dd.engine.llm import LLMError, OpenAILLM
from vendor_dd.engine.schemas import Citation, Dimension, Finding, Section, SourceType


class _FakeResp:
    def __init__(self, parsed, text="raw output", usage=None):
        self.output_parsed = parsed
        self.output_text = text
        self.usage = usage


class _RecordingResponses:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._parsed)


class _RecordingClient:
    """Fake openai client exposing the Responses API surface we use."""
    def __init__(self, parsed):
        self.responses = _RecordingResponses(parsed)

    @property
    def calls(self):
        return self.responses.calls


def test_structured_returns_parsed_object_and_forwards_schema():
    section = Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)
    client = _RecordingClient(section)
    llm = OpenAILLM(model="test-model", client=client)

    out = llm.structured("some prompt", Section)

    assert out is section                       # responses.parse returns a parsed instance
    call = client.calls[0]
    assert call["model"] == "test-model"
    assert call["input"] == "some prompt"
    assert call["text_format"] is Section       # native structured output, no manual schema


def test_structured_round_trips_nested_findings():
    section = Section(
        dimension=Dimension.LEGAL, reasoning="ok", score=7,
        findings=[Finding(claim="clean record", citation=Citation(
            url="https://x.com", title="X", source_type=SourceType.INDEPENDENT,
            score=0.9, as_of=None))],
    )
    llm = OpenAILLM(model="m", client=_RecordingClient(section))
    out = llm.structured("p", Section)
    assert out.findings[0].claim == "clean record"
    assert out.findings[0].citation.source_type is SourceType.INDEPENDENT


def test_structured_logs_request_and_response_without_secrets(tmp_path, monkeypatch):
    from vendor_dd.logs import configure_logging
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key-xyz")
    configure_logging(tmp_path, level="INFO")

    section = Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=8)
    llm = OpenAILLM(model="test-model", client=_RecordingClient(section), api_key="secret-key-xyz")
    llm.structured("classify this", Section)

    lines = [_json.loads(l) for l in (tmp_path / "llm.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "llm.request" in events and "llm.response" in events
    assert "secret-key-xyz" not in (tmp_path / "llm.log").read_text()   # api key never logged
    req = next(o for o in lines if o["event"] == "llm.request")
    assert req["payload"]["model"] == "test-model"


def test_no_parsed_output_raises_clear_llm_error():
    llm = OpenAILLM(model="m", client=_RecordingClient(None))
    with pytest.raises(LLMError, match="no parsed output"):
        llm.structured("p", Section)


class _RaisingClient:
    def __init__(self, exc):
        self.responses = self
        self._exc = exc

    def parse(self, **kwargs):
        raise self._exc


def test_llm_timeout_becomes_llm_error():
    exc = APITimeoutError(request=httpx.Request("POST", "http://x"))
    llm = OpenAILLM(model="m", client=_RaisingClient(exc))
    with pytest.raises(LLMError, match="timed out"):
        llm.structured("p", Section)


def test_llm_transport_error_becomes_llm_error():
    exc = APIError("boom", request=httpx.Request("POST", "http://x"), body=None)
    llm = OpenAILLM(model="m", client=_RaisingClient(exc))
    with pytest.raises(LLMError):
        llm.structured("p", Section)
