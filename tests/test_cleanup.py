from __future__ import annotations

import os
import time


def _make_file(path: str, age_days: float = 0) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * 10)
    if age_days > 0:
        past = time.time() - age_days * 86400
        os.utime(path, (past, past))


def test_stats_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    import cleanup
    monkeypatch.setattr(cleanup, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(cleanup, "RESULTS_DIR", str(tmp_path / "results"))
    s = cleanup.stats()
    assert s["logs"]["files"] == 0
    assert s["results"]["files"] == 0


def test_prune_logs_dry_run(tmp_path, monkeypatch):
    import cleanup
    logs = tmp_path / "logs"
    logs.mkdir()
    _make_file(str(logs / "old.json"), age_days=60)
    _make_file(str(logs / "new.json"), age_days=1)
    monkeypatch.setattr(cleanup, "LOG_DIR", str(logs))

    r = cleanup.prune_logs(days_old=30, dry_run=True)
    assert r.count == 1
    assert "old.json" in r.deleted_files
    assert os.path.exists(str(logs / "old.json"))  # dry run keeps file


def test_prune_logs_apply(tmp_path, monkeypatch):
    import cleanup
    logs = tmp_path / "logs"
    logs.mkdir()
    _make_file(str(logs / "old.json"), age_days=60)
    _make_file(str(logs / "new.json"), age_days=1)
    monkeypatch.setattr(cleanup, "LOG_DIR", str(logs))

    r = cleanup.prune_logs(days_old=30, dry_run=False)
    assert r.count == 1
    assert not os.path.exists(str(logs / "old.json"))
    assert os.path.exists(str(logs / "new.json"))


def test_prune_results_respects_threshold(tmp_path, monkeypatch):
    import cleanup
    res = tmp_path / "results"
    res.mkdir()
    _make_file(str(res / "a.json"), age_days=5)
    _make_file(str(res / "b.json"), age_days=45)
    monkeypatch.setattr(cleanup, "RESULTS_DIR", str(res))

    r = cleanup.prune_results(days_old=30, dry_run=False)
    assert r.count == 1
    assert "b.json" in r.deleted_files
    assert os.path.exists(str(res / "a.json"))


def test_prune_rbs_status_filter(tmp_db, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta

    import cleanup
    import research_box as rb_store

    rb1 = rb_store.create("task old error")
    rb1.status = "error"
    rb1.save()
    rb2 = rb_store.create("task old completed")
    rb2.status = "completed"
    rb2.save()

    past_ts = (datetime.now() - timedelta(days=100)).isoformat(timespec="seconds")
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE research_box SET updated_at = ? WHERE id IN (?, ?)", (past_ts, rb1.id, rb2.id))
    conn.commit()
    conn.close()

    r = cleanup.prune_rbs(days_old=30, status_filter=["error"], dry_run=False)
    assert r.count == 1
    assert rb1.id in r.deleted_files
    assert rb_store.load(rb2.id) is not None
    assert rb_store.load(rb1.id) is None
