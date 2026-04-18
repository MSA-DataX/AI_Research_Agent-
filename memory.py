from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from config import ENABLE_EMBEDDING_MEMORY, MEMORY_PATH
from embeddings import cosine, embed

_STOP = {
    "der", "die", "das", "und", "oder", "mit", "für", "fur", "von", "zu", "zur",
    "den", "dem", "ein", "eine", "einen", "eines", "einer", "im", "in", "auf",
    "als", "ist", "sind", "sei", "werde", "werden", "the", "a", "an", "of",
    "and", "or", "to", "for", "in", "on", "by", "as", "with", "is", "are",
    "list", "liste", "top", "finde", "nenne", "recherchiere", "gib", "aus",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9]{3,}", (text or "").lower())
    return {w for w in words if w not in _STOP}


def _load() -> list[dict]:
    if not os.path.exists(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def remember(task: str, result: Any, sources: list[str] | None = None) -> None:
    items = _load()
    entry: dict = {
        "task": task,
        "result": result,
        "sources": sources or [],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if ENABLE_EMBEDDING_MEMORY:
        vec = embed(task)
        if vec:
            entry["embedding"] = vec
    items.append(entry)
    items = items[-100:]
    _save(items)


def _rank_semantic(task: str, items: list[dict]) -> list[tuple[float, dict]]:
    q_vec = embed(task)
    if not q_vec:
        return []
    scored: list[tuple[float, dict]] = []
    for it in items:
        v = it.get("embedding")
        if v:
            score = cosine(q_vec, v)
            if score >= 0.55:
                scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    return scored


def _rank_lexical(task: str, items: list[dict]) -> list[tuple[int, dict]]:
    q = _tokens(task)
    if not q:
        return []
    scored: list[tuple[int, dict]] = []
    for it in items:
        overlap = len(q & _tokens(it.get("task", "")))
        if overlap >= 2:
            scored.append((overlap, it))
    scored.sort(key=lambda x: -x[0])
    return scored


def recall(task: str, k: int = 3, max_chars: int = 800) -> str:
    items = _load()
    if not items:
        return ""
    ranked: list[dict] = []
    if ENABLE_EMBEDDING_MEMORY:
        sem = _rank_semantic(task, items)
        ranked = [it for _, it in sem[:k]]
    if not ranked:
        lex = _rank_lexical(task, items)
        ranked = [it for _, it in lex[:k]]
    if not ranked:
        return ""
    hints = [
        {"past_task": it["task"], "ts": it.get("ts"), "sources": (it.get("sources") or [])[:3]}
        for it in ranked
    ]
    return json.dumps(hints, ensure_ascii=False)[:max_chars]
