from __future__ import annotations

import json
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from vendor_dd.logs import get_logger

T = TypeVar("T", bound=BaseModel)

_LOG = get_logger("llm")


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


class LLMError(RuntimeError):
    """The model returned an unusable response (empty/none content or invalid JSON)."""


_NEBIUS_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"


def _to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively enforce OpenAI 'strict' json_schema requirements (every object
    forbids extra properties and lists every declared property as required, including
    nested $defs). Verified live against Nebius: without `strict=True` + this shape,
    nvidia/Nemotron-3-Ultra-550b-a55b's structured output frequently drops required
    fields or stuffs free text into list fields; with it, output reliably matches the
    schema."""
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())
        for prop_schema in schema["properties"].values():
            _to_strict_schema(prop_schema)
    if schema.get("type") == "array" and "items" in schema:
        _to_strict_schema(schema["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            _to_strict_schema(sub)
    for sub in schema.get("$defs", {}).values():
        _to_strict_schema(sub)
    return schema


def _resolve_schema(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a $ref into its $defs target; pass through any other schema unchanged."""
    ref = schema.get("$ref", "").removeprefix("#/$defs/")
    return defs.get(ref, schema) if ref else schema


def _coerce_null_strings(data: Any, schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """Nemotron-3-Ultra sometimes writes the literal string "null" for a nullable
    field instead of JSON null (verified live: e.g. EntityCard.ticker='null'). Coerce
    that exact string back to None wherever the schema actually permits null, at ANY
    nesting depth -- top-level fields, fields on nested objects, and fields on objects
    inside arrays (e.g. Section.findings[i].citation.as_of) -- so downstream code sees
    a real optional-empty value rather than a garbage string."""
    defs = defs if defs is not None else schema.get("$defs", {})
    resolved = _resolve_schema(schema, defs)

    if isinstance(data, dict) and "properties" in resolved:
        for key, prop in resolved["properties"].items():
            if key not in data:
                continue
            options = prop.get("anyOf", [prop])
            is_nullable = any(_resolve_schema(opt, defs).get("type") == "null" for opt in options)
            value = data[key]
            if is_nullable and isinstance(value, str) and value.strip().lower() == "null":
                data[key] = None
                continue
            # descend into whichever anyOf branch is a real (non-null) object/array schema
            for opt in options:
                opt_resolved = _resolve_schema(opt, defs)
                if opt_resolved.get("type") in ("object", "array"):
                    data[key] = _coerce_null_strings(data[key], opt_resolved, defs)
                    break
    elif isinstance(data, list) and resolved.get("type") == "array" and "items" in resolved:
        item_schema = resolved["items"]
        for i, item in enumerate(data):
            data[i] = _coerce_null_strings(item, item_schema, defs)

    return data


def _usage_dict(usage) -> dict[str, Any] | None:
    try:
        return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens}
    except Exception:
        return None


def _extract_content(resp) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMError("model returned no choices")
    content = getattr(choices[0].message, "content", None)
    if not content:
        raise LLMError("model returned empty content")
    return content


class NebiusLLM:
    """Nebius Token Factory structured-output client: the raw OpenAI SDK's
    chat.completions endpoint with native `response_format=json_schema` — no tool
    calling.

    Not /v1/responses: as of 2026-07, Nebius's /v1/responses implementation has a
    confirmed bug (structured `text.format=json_schema` 400s on a server-side
    field-name mismatch, `schema_` vs `schema`), even though plain-text responses
    work fine there. /v1/chat/completions' native structured-output mode is the
    verified-working path.

    `reasoning_effort="none"` is required, not optional: this reasoning model leaks
    chain-of-thought into the structured output without it — verified live 3x, e.g.
    EntityCard.domain coming back None/EntityCard.ticker='null' (the literal string)
    and Section failing schema validation outright with reasoning dumped into
    `findings`. With it, output is clean and schema-valid.
    """

    def __init__(self, model: str = "nvidia/Nemotron-3-Ultra-550b-a55b",
                 api_key: str | None = None, client: Any = None):
        self._model = model
        if client is not None:
            self._client = client
            return
        import os

        from openai import OpenAI
        key = api_key or os.environ["NEBIUS_API_KEY"]
        self._client = OpenAI(base_url=_NEBIUS_BASE_URL, api_key=key)

    def structured(self, prompt: str, schema: type[T]) -> T:
        json_schema = _to_strict_schema(schema.model_json_schema())
        _LOG.info("llm.request", extra={"payload": {
            "model": self._model, "schema": schema.__name__,
            "reasoning_effort": "none", "prompt": prompt,
        }})
        started = time.monotonic()
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": True},
            },
            reasoning_effort="none",
        )
        content = _extract_content(resp)
        latency_ms = round((time.monotonic() - started) * 1000)
        _LOG.info("llm.response", extra={"payload": {
            "model": self._model, "schema": schema.__name__,
            "latency_ms": latency_ms, "content": content,
            "usage": getattr(resp, "usage", None) and _usage_dict(resp.usage),
        }})
        try:
            data = _coerce_null_strings(json.loads(content), json_schema)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            _LOG.error("llm.error", extra={"payload": {"schema": schema.__name__, "content": content,
                                                        "error": str(exc)}})
            raise LLMError(f"{schema.__name__}: model returned unparseable/invalid JSON") from exc
