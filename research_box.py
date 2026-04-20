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
    id                  TEXT PRIMARY KEY,
    task                TEXT NOT NULL,
    status              TEXT NOT NULL,
    sources             TEXT NOT NULL,
    visited_sources     TEXT NOT NULL,
    extracted_data      TEXT,
    entities            TEXT,
    validation          TEXT,
    iterations          INTEGER DEFAULT 0,
    embedding           TEXT,
    output_fields       TEXT,
    validation_history  TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rb_updated ON research_box(updated_at);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    existing = {row[1] for row in c.execute("PRAGMA table_info(research_box)").fetchall()}
    if "output_fields" not in existing:
        c.execute("ALTER TABLE research_box ADD COLUMN output_fields TEXT")
    if "validation_history" not in existing:
        c.execute("ALTER TABLE research_box ADD COLUMN validation_history TEXT")
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
        self.output_fields: list[str] = _j_load(row.get("output_fields"), []) or []
        self.validation_history: list[dict] = _j_load(row.get("validation_history"), []) or []
        self.created_at: str = row.get("created_at") or _now()
        self.updated_at: str = row.get("updated_at") or _now()

    def save(self) -> "ResearchBox":
        self.updated_at = _now()
        with _conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO research_box
                (id, task, status, sources, visited_sources, extracted_data, entities,
                 validation, iterations, embedding, output_fields, validation_history,
                 created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    json.dumps(self.output_fields, ensure_ascii=False) if self.output_fields else None,
                    json.dumps(self.validation_history, ensure_ascii=False) if self.validation_history else None,
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
            "output_fields": self.output_fields,
            "validation_history": self.validation_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def append_validation_snapshot(self) -> None:
        """Append current validation as a timeline entry (kept last 20)."""
        v = self.validation or {}
        if not v:
            return
        snap = {
            "ts": _now(),
            "mode": v.get("mode", "validate"),
            "confidence": v.get("confidence"),
            "label": v.get("label"),
            "total": v.get("total"),
            "supported": v.get("supported"),
            "methods_used": v.get("methods_used"),
            "methods_summary": v.get("methods_summary"),
        }
        self.validation_history = (self.validation_history or []) + [snap]
        self.validation_history = self.validation_history[-20:]

    def add_sources(self, urls) -> None:
        from url_utils import canonicalize_url
        seen = {canonicalize_url(u) for u in self.sources}
        for u in urls or []:
            if not u:
                continue
            canon = canonicalize_url(u)
            if canon not in seen:
                self.sources.append(canon)
                seen.add(canon)

    def mark_visited(self, url: str) -> None:
        from url_utils import canonicalize_url
        if not url:
            return
        canon = canonicalize_url(url)
        if canon not in {canonicalize_url(u) for u in self.visited_sources}:
            self.visited_sources.append(canon)


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
