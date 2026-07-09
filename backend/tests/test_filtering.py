from vendor_dd.engine.filtering import passes_filter, verify_entity


def _result(score=0.9, title="Cives Steel wins award", content="Cives Steel Company ..."):
    return {"score": score, "title": title, "content": content, "url": "https://x.com"}


def test_low_score_is_dropped():
    assert passes_filter(_result(score=0.2), entity_name="Cives Steel") is False


def test_high_score_with_entity_mention_passes():
    assert passes_filter(_result(score=0.7), entity_name="Cives Steel") is True


def test_entity_not_mentioned_is_dropped_even_if_high_score():
    r = _result(score=0.9, title="U.S. Steel EEOC lawsuit", content="U.S. Steel violated ...")
    assert passes_filter(r, entity_name="Cives Steel") is False


def test_verify_entity_is_case_insensitive_and_checks_title_or_content():
    assert verify_entity({"title": "CIVES STEEL", "content": ""}, "Cives Steel") is True
    assert verify_entity({"title": "", "content": "about cives steel co"}, "Cives Steel") is True
    assert verify_entity({"title": "Nucor", "content": "steel"}, "Cives Steel") is False
