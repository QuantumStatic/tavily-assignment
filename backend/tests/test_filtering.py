from vendor_dd.engine.filtering import passes_filter, verify_entity


def _result(score=0.9, title="Cives Steel wins award", content="Cives Steel Company ..."):
    return {"score": score, "title": title, "content": content, "url": "https://x.com"}


def test_result_that_names_the_entity_passes_regardless_of_score():
    assert passes_filter(_result(score=0.05), entity_name="Cives Steel") is True
    assert passes_filter(_result(score=0.9), entity_name="Cives Steel") is True


def test_entity_not_mentioned_is_dropped_even_with_high_score():
    r = _result(score=0.9, title="U.S. Steel EEOC lawsuit", content="U.S. Steel violated ...")
    assert passes_filter(r, entity_name="Cives Steel") is False


def test_verify_entity_is_case_insensitive_and_checks_title_or_content():
    assert verify_entity({"title": "CIVES STEEL", "content": ""}, "Cives Steel") is True
    assert verify_entity({"title": "", "content": "about cives steel co"}, "Cives Steel") is True
    assert verify_entity({"title": "Nucor", "content": "steel"}, "Cives Steel") is False


def test_verify_entity_ignores_legal_suffixes_on_the_name():
    """Regression: an article saying 'Voith' must not be rejected just because the
    resolved name carried a 'GmbH' suffix ('Voith GmbH')."""
    article = {"title": "Voith appoints new CEO of Voith Turbo", "content": "Voith announced"}
    assert verify_entity(article, "Voith GmbH") is True
    assert verify_entity(article, "Voith Hydro Holding GmbH & Co. KG") is False  # 'hydro' absent
    # a genuinely different company is still rejected
    assert verify_entity({"title": "Andritz AG results", "content": "Andritz"}, "Voith GmbH") is False


def test_verify_entity_core_tokens_are_order_independent():
    assert verify_entity({"title": "hydro plant by Voith", "content": ""},
                         "Voith Hydro GmbH") is True
