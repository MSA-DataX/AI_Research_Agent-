from __future__ import annotations

import validators


def test_norm_removes_diacritics():
    assert validators._norm("Köln") == "koln"
    assert validators._norm("Ä Ö Ü ß") == "a o u ss" or "a o u" in validators._norm("Ä Ö Ü ß")


def test_row_name_prefers_name():
    assert validators._row_name({"name": "Foo", "title": "Bar"}) == "Foo"


def test_row_name_falls_back_to_title():
    assert validators._row_name({"title": "Bar"}) == "Bar"


def test_name_substring_found():
    r = validators.method_name_substring({"name": "Aleph Alpha"}, "Die Firma Aleph Alpha aus Heidelberg")
    assert r["supported"] is True


def test_name_substring_not_found():
    r = validators.method_name_substring({"name": "XYZ Corp"}, "andere Inhalte")
    assert r["supported"] is False


def test_name_substring_no_page():
    r = validators.method_name_substring({"name": "A"}, "[fetch error] 404")
    assert r["supported"] is False


def test_all_fields_all_present():
    row = {"name": "Neujahr", "date": "01.01.2026"}
    page = "Am 01.01.2026 wird Neujahr gefeiert."
    r = validators.method_all_fields(row, page)
    assert r["supported"] is True
    assert r["fields_missing"] == 0


def test_all_fields_missing_one():
    row = {"name": "Neujahr", "date": "01.01.2026"}
    page = "Am 01.01.2099 wird Neujahr gefeiert."
    r = validators.method_all_fields(row, page)
    assert r["supported"] is False
    assert r["fields_missing"] >= 1


def test_all_fields_skips_url_keys():
    row = {"name": "Acme", "source_url": "https://example.com", "url": "https://x.com"}
    page = "Acme Corp steht hier - kein example.com oder x.com im Fließtext"
    r = validators.method_all_fields(row, page)
    assert r["supported"] is True
    assert r["fields_checked"] == 1


def test_cross_source_counts_distinct_domains():
    def fake_search(q, max_results=5):
        return [
            {"title": "Aleph Alpha Wiki", "url": "https://wikipedia.org/a", "snippet": ""},
            {"title": "Aleph Alpha News", "url": "https://handelsblatt.de/x", "snippet": ""},
            {"title": "unrelated", "url": "https://example.com", "snippet": "no match here"},
        ]
    r = validators.method_cross_source({"name": "Aleph Alpha"}, fake_search)
    assert r["n_domains"] == 2
    assert r["label"] == "medium"
    assert r["supported"] is True


def test_cross_source_single_domain_low():
    def fake_search(q, max_results=5):
        return [{"title": "X", "url": "https://only.com/a", "snippet": ""}]
    r = validators.method_cross_source({"name": "X"}, fake_search)
    assert r["n_domains"] == 1
    assert r["label"] == "low"


def test_llm_semantic_supported():
    def fake_chat(messages, temperature=0.0):
        return '{"supported": true, "confidence": 90, "reason": "date matches"}'
    r = validators.method_llm_semantic({"name": "A"}, "some page content", fake_chat)
    assert r["supported"] is True
    assert r["confidence"] == 90


def test_llm_semantic_not_supported():
    def fake_chat(messages, temperature=0.0):
        return '{"supported": false, "confidence": 20, "reason": "year mismatch"}'
    r = validators.method_llm_semantic({"name": "A", "year": "2026"}, "content from 2025", fake_chat)
    assert r["supported"] is False


def test_llm_semantic_handles_code_fences():
    def fake_chat(messages, temperature=0.0):
        return '```json\n{"supported": true, "confidence": 80}\n```'
    r = validators.method_llm_semantic({"name": "A"}, "content", fake_chat)
    assert r["supported"] is True


def test_llm_semantic_handles_chat_error():
    def fake_chat(messages, temperature=0.0):
        raise ConnectionError("lm studio down")
    r = validators.method_llm_semantic({"name": "A"}, "content", fake_chat)
    assert r["supported"] is False
    assert "llm error" in r["reason"]


def test_domain_trust_wikipedia_high():
    r = validators.method_domain_trust({"source_url": "https://de.wikipedia.org/wiki/Berlin"})
    assert r["supported"] is True
    assert r["score"] >= 0.9


def test_domain_trust_unknown_domain_low():
    r = validators.method_domain_trust({"source_url": "https://random-blog-42.example/stuff"})
    assert r["supported"] is False
    assert r["score"] < 0.55


def test_domain_trust_quiz_site_distrust():
    r = validators.method_domain_trust({"source_url": "https://www.quiztante.de/stadt-mit-z/"})
    assert r["supported"] is False
    assert r["score"] <= 0.25


def test_domain_trust_no_source():
    r = validators.method_domain_trust({"name": "X"})
    assert r["supported"] is False
    assert r["score"] == 0.0


def test_domain_trust_handles_www():
    r = validators.method_domain_trust({"source_url": "https://www.wikipedia.org/wiki/X"})
    assert r["score"] >= 0.9


def test_field_completeness_all_filled():
    r = validators.method_field_completeness({"name": "A", "bundesland": "Bayern", "source_url": "https://x.com"})
    assert r["supported"] is True


def test_field_completeness_partial():
    r = validators.method_field_completeness({"name": "A", "bundesland": None, "date": ""})
    assert r["supported"] is False
    assert r["filled"] == 1
    assert r["total"] == 3


def test_verdict_weighted_all_supported():
    results = {
        "name_substring": {"supported": True},
        "all_fields": {"supported": True},
        "cross_source": {"supported": True},
        "llm_semantic": {"supported": True},
    }
    label, conf = validators.verdict_for_row(results)
    assert conf == 100
    assert label == "high"


def test_verdict_weighted_llm_fail_heavy_penalty():
    """llm_semantic weighs 0.32, so failing it alone lowers confidence more than any single other method."""
    all_but_llm = {
        "name_substring": {"supported": True},
        "all_fields": {"supported": True},
        "cross_source": {"supported": True},
        "llm_semantic": {"supported": False},
    }
    _, conf_llm_fails = validators.verdict_for_row(all_but_llm)

    all_but_name = {
        "name_substring": {"supported": False},
        "all_fields": {"supported": True},
        "cross_source": {"supported": True},
        "llm_semantic": {"supported": True},
    }
    _, conf_name_fails = validators.verdict_for_row(all_but_name)

    assert conf_llm_fails < conf_name_fails, \
        f"failing llm_semantic (weighted) should hurt more than failing name_substring; got llm_fails={conf_llm_fails}, name_fails={conf_name_fails}"


def test_verdict_none_supported_is_unverified():
    results = {m: {"supported": False} for m in ("name_substring", "all_fields", "llm_semantic")}
    label, conf = validators.verdict_for_row(results)
    assert conf == 0
    assert label == "unverified"


def test_methods_dict_includes_new_ones():
    assert "domain_trust" in validators.METHODS
    assert "field_completeness" in validators.METHODS


def test_method_weights_sum_close_to_one():
    s = sum(validators.METHOD_WEIGHTS.values())
    assert 0.99 <= s <= 1.01, f"weights should sum ~1.0, got {s}"


def test_consistency_detects_bad_relation():
    def fake_chat(messages, temperature=0.0):
        return '{"consistent": false, "confidence": 10, "reason": "Berlin is a Stadtstaat, not in Brandenburg"}'
    r = validators.method_consistency(
        {"name": "Berlin", "bundesland": "Brandenburg"}, fake_chat
    )
    assert r["supported"] is False
    assert "Stadtstaat" in r["reason"] or "Brandenburg" in r["reason"]


def test_consistency_accepts_correct_relation():
    def fake_chat(messages, temperature=0.0):
        return '{"consistent": true, "confidence": 95, "reason": "Munich is the capital of Bavaria"}'
    r = validators.method_consistency(
        {"name": "München", "bundesland": "Bayern"}, fake_chat
    )
    assert r["supported"] is True


def test_consistency_handles_chat_error():
    def fake_chat(messages, temperature=0.0):
        raise ConnectionError("lm studio down")
    r = validators.method_consistency({"name": "A"}, fake_chat)
    assert r["supported"] is False
    assert "llm error" in r["reason"]


def test_consistency_no_chat_fn():
    r = validators.method_consistency({"name": "A"}, None)
    assert r["supported"] is False


def test_consistency_empty_item():
    r = validators.method_consistency({}, lambda m, **_: "")
    assert r["supported"] is False


def test_relationship_validation_city_country():
    def fake_chat(messages, temperature=0.0):
        return '{"supported": true, "relations_checked": ["city->country"], "confidence": 95, "reason": "Paris is in France"}'
    r = validators.method_relationship_validation(
        {"city": "Paris", "country": "France"}, fake_chat
    )
    assert r["supported"] is True
    assert "city->country" in r["relations_checked"]


def test_relationship_validation_rejects_wrong():
    def fake_chat(messages, temperature=0.0):
        return '{"supported": false, "relations_checked": ["city->country"], "confidence": 5, "reason": "Paris is not in Germany"}'
    r = validators.method_relationship_validation(
        {"city": "Paris", "country": "Germany"}, fake_chat
    )
    assert r["supported"] is False


def test_relationship_validation_single_field_skips():
    def fake_chat(messages, temperature=0.0):
        return '{"supported": true, "confidence": 50}'
    r = validators.method_relationship_validation({"name": "X"}, fake_chat)
    assert r["supported"] is False
    assert "2 fields" in r["reason"]


def test_method_weights_include_new_ones():
    assert "consistency" in validators.METHOD_WEIGHTS
    assert "relationship_validation" in validators.METHOD_WEIGHTS
    assert "consistency" in validators.METHODS
    assert "relationship_validation" in validators.METHODS


def test_weighted_verdict_penalizes_failing_consistency():
    """If only consistency fails, confidence should drop noticeably because of its high weight."""
    all_ok = {m: {"supported": True} for m in validators.METHOD_WEIGHTS}
    _, conf_all = validators.verdict_for_row(all_ok)

    fail_consistency = dict(all_ok)
    fail_consistency["consistency"] = {"supported": False}
    _, conf_fail_cons = validators.verdict_for_row(fail_consistency)

    fail_name = dict(all_ok)
    fail_name["name_substring"] = {"supported": False}
    _, conf_fail_name = validators.verdict_for_row(fail_name)

    assert conf_all == 100
    assert conf_fail_cons < conf_fail_name, \
        f"failing consistency should hurt more than failing name_substring, got cons={conf_fail_cons} name={conf_fail_name}"
