from __future__ import annotations

import os
import time
from typing import Any, Protocol, TypeVar

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel

from vendor_dd.logs import get_logger

T = TypeVar("T", bound=BaseModel)

_LOG = get_logger("llm")


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


class LLMError(RuntimeError):
    """The model returned an unusable response (no parsed output, a timeout, or any
    transport error)."""


_DEFAULT_MODEL = "gpt-5.6-luna"

# Per-call ceiling so a stalled synthesis can't hang the whole report generator forever
# (the SDK default is 600s ≈ never for a live UI). On expiry the call raises LLMError,
# which the pipeline turns into a per-dimension SectionError. Override via env.
_LLM_TIMEOUT_S = float(os.environ.get("VENDOR_DD_LLM_TIMEOUT", "90"))


def _usage_dict(usage) -> dict[str, Any] | None:
    try:
        return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens}
    except Exception:
        return None


class OpenAILLM:
    """OpenAI structured-output client over the Responses API.

    Uses `responses.parse(..., text_format=schema)`, which validates the model's output
    against the Pydantic schema server-side and returns a parsed instance — no manual
    JSON handling, no schema massaging. The default model reasons about its answers, so
    numeric fields (e.g. a section's 0-10 score) are calibrated rather than defaulted.
    """

    def __init__(self, model: str = _DEFAULT_MODEL, api_key: str | None = None,
                 client: Any = None):
        self._model = model
        if client is not None:
            self._client = client
            return
        key = api_key or os.environ["OPENAI_API_KEY"]
        self._client = OpenAI(api_key=key, timeout=_LLM_TIMEOUT_S)

    def structured(self, prompt: str, schema: type[T]) -> T:
        _LOG.info("llm.request", extra={"payload": {
            "model": self._model, "schema": schema.__name__, "prompt": prompt,
        }})
        started = time.monotonic()
        try:
            resp = self._client.responses.parse(
                model=self._model, input=prompt, text_format=schema)
        except APITimeoutError as exc:
            _LOG.error("llm.error", extra={"payload": {"schema": schema.__name__,
                                                       "error": f"timeout after {_LLM_TIMEOUT_S}s"}})
            raise LLMError(f"{schema.__name__}: LLM call timed out after {_LLM_TIMEOUT_S}s") from exc
        except APIError as exc:
            _LOG.error("llm.error", extra={"payload": {"schema": schema.__name__, "error": str(exc)}})
            raise LLMError(f"{schema.__name__}: LLM transport error") from exc

        latency_ms = round((time.monotonic() - started) * 1000)
        parsed = getattr(resp, "output_parsed", None)
        _LOG.info("llm.response", extra={"payload": {
            "model": self._model, "schema": schema.__name__, "latency_ms": latency_ms,
            "content": getattr(resp, "output_text", None),
            "usage": _usage_dict(getattr(resp, "usage", None)),
        }})
        if parsed is None:
            _LOG.error("llm.error", extra={"payload": {"schema": schema.__name__,
                                                       "error": "model returned no parsed output"}})
            raise LLMError(f"{schema.__name__}: model returned no parsed output")
        return parsed
