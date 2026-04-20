from __future__ import annotations

from dedup import merge_items


def test_merge_adds_new_item():
    existing = [{"name": "Aleph Alpha"}]
    new = [{"name": "DeepL"}]
    r = merge_items(existing, new)
    assert r["added"] == 1
    assert r["updated"] == 0
    assert len(r["merged"]) == 2


def test_merge_dedupes_by_normalized_name():
    existing = [{"name": "Aleph Alpha", "website": "https://aleph-alpha.com"}]
    new = [{"name": "aleph  alpha!"}]  # extra spaces + punct + case
    r = merge_items(existing, new)
    assert r["added"] == 0
    assert len(r["merged"]) == 1


def test_merge_fills_missing_fields():
    existing = [{"name": "X"}]
    new = [{"name": "X", "website": "https://x.com", "description": "test"}]
    r = merge_items(existing, new)
    assert r["updated"] == 1
    assert r["merged"][0]["website"] == "https://x.com"
    assert r["merged"][0]["description"] == "test"


def test_merge_does_not_overwrite_existing():
    existing = [{"name": "X", "website": "https://old.com"}]
    new = [{"name": "X", "website": "https://new.com"}]
    r = merge_items(existing, new)
    assert r["merged"][0]["website"] == "https://old.com"


def test_merge_detects_duplicate_via_canonical_source():
    existing = [{"name": "Foo", "source_url": "https://example.com/article/?utm_source=x"}]
    new = [{"name": "Bar", "source_url": "https://www.example.com/article"}]
    r = merge_items(existing, new)
    assert r["added"] == 0
    assert len(r["merged"]) == 1


def test_merge_counts_stats():
    existing = [{"name": "A"}, {"name": "B"}]
    new = [
        {"name": "A", "extra": "fill"},
        {"name": "C"},
        {"name": "B"},
    ]
    r = merge_items(existing, new)
    assert r["added"] == 1
    assert r["updated"] == 1
    assert r["skipped"] == 1
    assert len(r["merged"]) == 3


def test_merge_ignores_non_dict_items():
    existing = [{"name": "A"}]
    new = ["not a dict", 42, {"name": "B"}]
    r = merge_items(existing, new)
    assert r["added"] == 1
    assert len(r["merged"]) == 2


def test_merge_handles_empty_inputs():
    assert merge_items([], [])["merged"] == []
    r = merge_items(None, [{"name": "X"}])
    assert len(r["merged"]) == 1
