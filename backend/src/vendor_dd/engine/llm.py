from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


_NEBIUS_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"


def _to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively enforce OpenAI 'strict' tool-schema requirements (every object
    forbids extra properties and lists every declared property as required, including
    nested $defs). Verified live against Nebius: without `strict=True` + this shape,
    nvidia/Nemotron-3-Ultra-550b-a55b's tool-calling frequently drops required fields or
    stuffs free text into list fields; with it, output reliably matches the schema."""
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


def _nullable_keys(schema: dict[str, Any], defs: dict[str, Any]) -> set[str]:
    """Property names whose schema allows null (directly or via a $ref into $defs)."""
    keys: set[str] = set()
    for key, prop in schema.get("properties", {}).items():
        options = prop.get("anyOf", [prop])
        for opt in options:
            ref = opt.get("$ref", "").removeprefix("#/$defs/")
            resolved = defs.get(ref, opt) if ref else opt
            if resolved.get("type") == "null":
                keys.add(key)
    return keys


def _coerce_null_strings(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Nemotron-3-Ultra sometimes writes the literal string "null" for a nullable
    field instead of JSON null (verified live: e.g. EntityCard.ticker='null'). Coerce
    that exact string back to None wherever the schema actually permits null, so
    downstream code sees a real optional-empty value rather than a garbage string."""
    defs = schema.get("$defs", {})
    for key in _nullable_keys(schema, defs):
        if isinstance(data.get(key), str) and data[key].strip().lower() == "null":
            data[key] = None
    return data


class NebiusLLM:
    """Nebius Token Factory structured-output client: the raw OpenAI SDK's
    chat.completions endpoint + strict function-calling.

    Not LangChain, and not /v1/responses: as of 2026-07, Nebius's /v1/responses
    implementation has a confirmed bug (raw unparsed `<tool_call>` template text
    surfaces in `function_call.arguments` instead of JSON), and its json_schema
    text-format path 400s on a field-name mismatch. /v1/chat/completions with
    `strict=True` tool calling is the verified-working path for this model.
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
        tool_name = schema.__name__
        json_schema = _to_strict_schema(schema.model_json_schema())
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Record the {tool_name} result.",
                "parameters": json_schema,
                "strict": True,
            },
        }
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            # "none" disables this reasoning model's chain-of-thought leaking into tool
            # arguments (verified live: "low"/default corrupt multiple fields with
            # inline reasoning text; unset silently defaults to on for this model).
            reasoning_effort="none",
        )
        call = resp.choices[0].message.tool_calls[0]
        data = _coerce_null_strings(json.loads(call.function.arguments), json_schema)
        return schema.model_validate(data)
