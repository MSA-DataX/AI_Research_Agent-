from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def create_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        if len(_JOBS) >= _MAX_JOBS:
            oldest = sorted(_JOBS.items(), key=lambda kv: kv[1].get("created_at", ""))[0][0]
            _JOBS.pop(oldest, None)
        _JOBS[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "status": "pending",
            "rb_id": None,
            "result": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    with _LOCK:
        if job_id not in _JOBS:
            return
        _JOBS[job_id].update(fields)
        _JOBS[job_id]["updated_at"] = _now()


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(offset: int = 0, limit: int = 50) -> tuple[int, list[dict]]:
    with _LOCK:
        items = [dict(j) for j in _JOBS.values()]
    items.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return len(items), items[offset : offset + limit]


def run_async(job_id: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
    def _runner() -> None:
        update_job(job_id, status="running")
        try:
            result = fn(*args, **kwargs)
            rb_id = result.get("rb_id") if isinstance(result, dict) else None
            err = result.get("error") if isinstance(result, dict) else None
            if err:
                update_job(job_id, status="error", rb_id=rb_id, result=result, error=str(err)[:500])
            else:
                update_job(job_id, status="completed", rb_id=rb_id, result=result)
        except Exception as e:
            update_job(job_id, status="error", error=f"{type(e).__name__}: {e}"[:500])

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


def clear_all() -> None:
    with _LOCK:
        _JOBS.clear()
