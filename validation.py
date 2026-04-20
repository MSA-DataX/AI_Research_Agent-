from __future__ import annotations

from typing import Any

from url_utils import canonicalize_url


def compute(extracted_data: Any, visited_sources: list[str]) -> dict:
    items: list[dict] = []
    if isinstance(extracted_data, list):
        items = [x for x in extracted_data if isinstance(x, dict)]
    elif isinstance(extracted_data, dict):
        items = [extracted_data]

    total = len(items)
    n_visited = len(visited_sources or [])
    visited_set = {canonicalize_url(u) for u in (visited_sources or [])}

    if total == 0:
        return {
            "confidence": 0,
            "supported": 0,
            "total": 0,
            "n_sources": n_visited,
            "per_item": [],
            "label": "empty",
        }

    supported = 0
    per_item: list[dict] = []
    for it in items:
        src = it.get("source_url") or it.get("source") or it.get("sourceUrl") or ""
        ok = bool(src) and canonicalize_url(src) in visited_set
        name = it.get("name") or it.get("title") or next(iter(it.values()), "?")
        per_item.append(
            {
                "name": str(name)[:80],
                "source": src,
                "supported": ok,
            }
        )
        if ok:
            supported += 1

    base_ratio = supported / total
    source_factor = min(1.0, n_visited / 3.0)
    confidence = int(round(base_ratio * source_factor * 100))

    if confidence >= 85:
        label = "high"
    elif confidence >= 60:
        label = "medium"
    elif confidence >= 30:
        label = "low"
    else:
        label = "unverified"

    supporting_sources = sorted({it["source"] for it in per_item if it["supported"] and it["source"]})

    return {
        "confidence": confidence,
        "label": label,
        "supported": supported,
        "total": total,
        "n_sources": n_visited,
        "supporting_sources": supporting_sources,
        "per_item": per_item[:100],
    }
