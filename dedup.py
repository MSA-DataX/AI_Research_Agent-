from __future__ import annotations

import re
import unicodedata
from typing import Any

from url_utils import canonicalize_url


def _norm_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _item_key(item: dict, key_fields: tuple[str, ...]) -> str:
    parts = []
    for k in key_fields:
        v = item.get(k)
        if isinstance(v, str):
            parts.append(_norm_text(v))
        elif v is not None:
            parts.append(_norm_text(str(v)))
    return "|".join(p for p in parts if p)


def _canonical_source(item: dict) -> str:
    for k in ("source_url", "source", "url"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return canonicalize_url(v)
    return ""


def merge_items(
    existing: list,
    new_items: list,
    key_fields: tuple[str, ...] = ("name",),
) -> dict:
    """Merge new_items into existing, deduping by normalized key_fields.

    When a duplicate is found, the existing record wins but missing fields
    are filled in from the new record. Returns a dict with `merged` list
    plus stats: added, updated, skipped.
    """
    existing_list: list[dict] = [it for it in (existing or []) if isinstance(it, dict)]
    new_list: list[dict] = [it for it in (new_items or []) if isinstance(it, dict)]

    by_key: dict[str, int] = {}
    by_src: dict[str, int] = {}
    for i, it in enumerate(existing_list):
        k = _item_key(it, key_fields)
        if k:
            by_key[k] = i
        src = _canonical_source(it)
        if src:
            by_src[src] = i

    added = 0
    updated = 0
    skipped = 0
    merged = list(existing_list)

    for item in new_list:
        k = _item_key(item, key_fields)
        src = _canonical_source(item)

        idx = None
        if k and k in by_key:
            idx = by_key[k]
        elif src and src in by_src:
            idx = by_src[src]

        if idx is not None:
            target = merged[idx]
            changed = False
            for field, value in item.items():
                if value in (None, "", [], {}):
                    continue
                if not target.get(field):
                    target[field] = value
                    changed = True
            if changed:
                updated += 1
            else:
                skipped += 1
        else:
            merged.append(item)
            new_idx = len(merged) - 1
            if k:
                by_key[k] = new_idx
            if src:
                by_src[src] = new_idx
            added += 1

    return {"merged": merged, "added": added, "updated": updated, "skipped": skipped}
