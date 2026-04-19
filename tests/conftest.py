from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    import research_box as rb_store
    monkeypatch.setattr(rb_store, "DB_PATH", db)
    monkeypatch.setattr(rb_store, "embed", lambda _t: [])
    yield db


@pytest.fixture
def fake_embed(monkeypatch):
    import research_box as rb_store
    vec_by_task: dict[str, list[float]] = {}

    def _fake(t: str) -> list[float]:
        if t not in vec_by_task:
            h = abs(hash(t))
            vec_by_task[t] = [(h % 100) / 100.0, ((h // 100) % 100) / 100.0, 0.5]
        return vec_by_task[t]

    monkeypatch.setattr(rb_store, "embed", _fake)
    yield _fake
