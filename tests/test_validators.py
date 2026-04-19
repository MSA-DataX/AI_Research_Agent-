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


def test_verdict_for_row_all_supported():
    results = {
        "m1": {"supported": True},
        "m2": {"supported": True},
        "m3": {"supported": True},
    }
    label, conf = validators.verdict_for_row(results)
    assert conf == 100
    assert label == "high"


def test_verdict_for_row_mixed():
    results = {
        "m1": {"supported": True},
        "m2": {"supported": False},
        "m3": {"supported": True},
    }
    label, conf = validators.verdict_for_row(results)
    assert conf == 67
    assert label == "medium"
