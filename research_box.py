from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Optional

from config import DB_PATH, SIMILARITY_REUSE_THRESHOLD
from embeddings import cosine, embed

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_box (
    id              TEXT PRIMARY KEY,
    task            TEXT NOT NULL,
    status          TEXT NOT NULL,
    sources         TEXT NOT NULL,
    visited_sources TEXT NOT NULL,
    extracted_data  TEXT,
    entities        TEXT,
    validation      TEXT,
    iterations      INTEGER DEFAULT 0,
    embedding       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rb_updated ON research_box(updated_at);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _j_load(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default


class ResearchBox:
    def __init__(self, row: Optional[dict] = None):
        row = row or {}
        self.id: str = row.get("id") or uuid.uuid4().hex[:12]
        self.task: str = row.get("task") or ""
        self.status: str = row.get("status") or "running"
        self.sources: list[str] = _j_load(row.get("sources"), [])
        self.visited_sources: list[str] = _j_load(row.get("visited_sources"), [])
        self.extracted_data: Any = _j_load(row.get("extracted_data"), None)
        self.entities: dict = _j_load(row.get("entities"), {})
        self.validation: dict = _j_load(row.get("validation"), {})
        self.iterations: int = int(row.get("iterations") or 0)
        self.embedding: list[float] = _j_load(row.get("embedding"), [])
        self.created_at: str = row.get("created_at") or _now()
        self.updated_at: str = row.get("updated_at") or _now()

    def save(self) -> "ResearchBox":
        self.updated_at = _now()
        with _conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO research_box
                (id, task, status, sources, visited_sources, extracted_data, entities,
                 validation, iterations, embedding, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.id,
                    self.task,
                    self.status,
                    json.dumps(self.sources, ensure_ascii=False),
                    json.dumps(self.visited_sources, ensure_ascii=False),
                    json.dumps(self.extracted_data, ensure_ascii=False) if self.extracted_data is not None else None,
                    json.dumps(self.entities, ensure_ascii=False),
                    json.dumps(self.validation, ensure_ascii=False),
                    self.iterations,
                    json.dumps(self.embedding) if self.embedding else None,
                    self.created_at,
                    self.updated_at,
                ),
            )
        return self

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "sources": self.sources,
            "visited_sources": self.visited_sources,
            "extracted_data": self.extracted_data,
            "entities": self.entities,
            "validation": self.validation,
            "iterations": self.iterations,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def add_sources(self, urls) -> None:
        for u in urls or []:
            if u and u not in self.sources:
                self.sources.append(u)

    def mark_visited(self, url: str) -> None:
        if url and url not in self.visited_sources:
            self.visited_sources.append(url)


def create(task: str) -> ResearchBox:
    rb = ResearchBox({"task": task})
    rb.embedding = embed(task) or []
    rb.save()
    return rb


def load(rb_id: str) -> Optional[ResearchBox]:
    with _conn() as c:
        row = c.execute("SELECT * FROM research_box WHERE id = ?", (rb_id,)).fetchone()
    return ResearchBox(dict(row)) if row else None


def list_all(limit: int = 100) -> list[ResearchBox]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM research_box ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [ResearchBox(dict(r)) for r in rows]


def delete(rb_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM research_box WHERE id = ?", (rb_id,))
    return cur.rowcount > 0


def find_similar(task: str, threshold: float = SIMILARITY_REUSE_THRESHOLD) -> Optional[ResearchBox]:
    q = embed(task)
    if not q:
        return None
    best_score = 0.0
    best_rb: Optional[ResearchBox] = None
    for rb in list_all(limit=200):
        if not rb.embedding:
            continue
        s = cosine(q, rb.embedding)
        if s > best_score:
            best_score = s
            best_rb = rb
    return best_rb if best_rb and best_score >= threshold else None


def recall_hints(task: str, k: int = 3, max_chars: int = 800) -> str:
    q = embed(task)
    if not q:
        return ""
    scored: list[tuple[float, ResearchBox]] = []
    for rb in list_all(limit=200):
        if not rb.embedding:
            continue
        s = cosine(q, rb.embedding)
        if s >= 0.55:
            scored.append((s, rb))
    scored.sort(key=lambda x: -x[0])
    hints = [
        {"past_task": rb.task, "ts": rb.updated_at, "sources": (rb.visited_sources or [])[:3]}
        for _, rb in scored[:k]
    ]
    return json.dumps(hints, ensure_ascii=False)[:max_chars] if hints else ""
