from __future__ import annotations

from typing import Any


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
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
            to_strict_schema(prop_schema)
    if schema.get("type") == "array" and "items" in schema:
        to_strict_schema(schema["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            to_strict_schema(sub)
    for sub in schema.get("$defs", {}).values():
        to_strict_schema(sub)
    return schema


def resolve_schema(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a $ref into its $defs target; pass through any other schema unchanged."""
    ref = schema.get("$ref", "").removeprefix("#/$defs/")
    return defs.get(ref, schema) if ref else schema


def coerce_null_strings(data: Any, schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """Nemotron-3-Ultra sometimes writes the literal string "null" for a nullable
    field instead of JSON null (verified live: e.g. EntityCard.ticker='null'). Coerce
    that exact string back to None wherever the schema actually permits null, at ANY
    nesting depth -- top-level fields, fields on nested objects, and fields on objects
    inside arrays (e.g. Section.findings[i].citation.as_of) -- so downstream code sees
    a real optional-empty value rather than a garbage string."""
    defs = defs if defs is not None else schema.get("$defs", {})
    resolved = resolve_schema(schema, defs)

    if isinstance(data, dict) and "properties" in resolved:
        for key, prop in resolved["properties"].items():
            if key not in data:
                continue
            options = prop.get("anyOf", [prop])
            is_nullable = any(resolve_schema(opt, defs).get("type") == "null" for opt in options)
            value = data[key]
            if is_nullable and isinstance(value, str) and value.strip().lower() == "null":
                data[key] = None
                continue
            # descend into whichever anyOf branch is a real (non-null) object/array schema
            for opt in options:
                opt_resolved = resolve_schema(opt, defs)
                if opt_resolved.get("type") in ("object", "array"):
                    data[key] = coerce_null_strings(data[key], opt_resolved, defs)
                    break
    elif isinstance(data, list) and resolved.get("type") == "array" and "items" in resolved:
        item_schema = resolved["items"]
        for i, item in enumerate(data):
            data[i] = coerce_null_strings(item, item_schema, defs)

    return data
