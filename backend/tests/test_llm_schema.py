from vendor_dd.engine.llm_schema import coerce_null_strings, to_strict_schema
from vendor_dd.engine.schemas import EntityCard, Section


def test_to_strict_schema_marks_every_property_required_and_forbids_extra():
    schema = to_strict_schema(Section.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_to_strict_schema_recurses_into_defs_and_nested_lists():
    schema = to_strict_schema(Section.model_json_schema())
    finding_def = schema["$defs"]["Finding"]
    assert finding_def["additionalProperties"] is False
    citation_def = schema["$defs"]["Citation"]
    assert citation_def["additionalProperties"] is False
    assert set(citation_def["required"]) == set(citation_def["properties"])


def test_coerce_null_strings_converts_literal_null_string_to_none():
    schema = EntityCard.model_json_schema()
    data = {"name": "Acme", "domain": "acme.com", "country": None, "industry": "steel",
            "parent": "null", "is_public": False, "ticker": "NULL", "exchange": " null "}
    out = coerce_null_strings(dict(data), schema)
    assert out["parent"] is None
    assert out["ticker"] is None      # case-insensitive
    assert out["exchange"] is None    # whitespace-tolerant
    assert out["domain"] == "acme.com"  # untouched, not "null"


def test_coerce_null_strings_leaves_non_nullable_fields_alone():
    schema = EntityCard.model_json_schema()
    data = {"name": "null", "domain": None, "country": None, "industry": None,
            "parent": None, "is_public": False, "ticker": None, "exchange": None}
    out = coerce_null_strings(dict(data), schema)
    assert out["name"] == "null"  # `name` isn't nullable in the schema; left as a literal string


def test_coerce_null_strings_recurses_into_nested_objects():
    schema = Section.model_json_schema()
    data = {"dimension": "legal", "reasoning": "x", "score": 5, "findings": [
        {"claim": "c", "citation": {"url": "u", "title": "t", "source_type": "independent",
                                    "score": 0.5, "as_of": "null"}}]}
    out = coerce_null_strings(data, schema)
    assert out["findings"][0]["citation"]["as_of"] is None


def test_coerce_null_strings_still_handles_top_level_nullable_fields():
    schema = EntityCard.model_json_schema()
    data = {"name": "Acme", "domain": "acme.com", "country": "null", "industry": None,
           "parent": None, "is_public": False, "ticker": "null", "exchange": None}
    out = coerce_null_strings(data, schema)
    assert out["country"] is None
    assert out["ticker"] is None
