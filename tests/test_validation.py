from __future__ import annotations

from validation import compute


def test_empty_input():
    r = compute([], [])
    assert r["confidence"] == 0
    assert r["total"] == 0
    assert r["label"] == "empty"


def test_all_supported_3_sources():
    items = [
        {"name": "A", "source_url": "https://a.com"},
        {"name": "B", "source_url": "https://b.com"},
    ]
    visited = ["https://a.com", "https://b.com", "https://c.com"]
    r = compute(items, visited)
    assert r["supported"] == 2
    assert r["total"] == 2
    assert r["confidence"] == 100
    assert r["label"] == "high"


def test_none_supported():
    items = [{"name": "A", "source_url": "https://unknown.com"}]
    r = compute(items, ["https://visited.com"])
    assert r["supported"] == 0
    assert r["confidence"] == 0
    assert r["label"] == "unverified"


def test_source_factor_penalizes_single_source():
    items = [{"name": "A", "source_url": "https://a.com"}]
    r = compute(items, ["https://a.com"])
    # 1/1 = 100%, but source_factor = 1/3 => confidence = 33
    assert r["confidence"] == 33
    assert r["label"] == "low"


def test_accepts_dict_single_item():
    r = compute({"name": "A", "source_url": "https://a.com"}, ["https://a.com"])
    assert r["total"] == 1
