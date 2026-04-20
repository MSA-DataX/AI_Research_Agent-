from __future__ import annotations

import time


def test_create_and_get_job():
    import jobs
    jobs.clear_all()
    job_id = jobs.create_job("test")
    assert len(job_id) == 12
    job = jobs.get_job(job_id)
    assert job["status"] == "pending"
    assert job["kind"] == "test"


def test_get_nonexistent_job():
    import jobs
    jobs.clear_all()
    assert jobs.get_job("does_not_exist") is None


def test_update_job():
    import jobs
    jobs.clear_all()
    job_id = jobs.create_job("t")
    jobs.update_job(job_id, status="running")
    assert jobs.get_job(job_id)["status"] == "running"


def test_run_async_success():
    import jobs
    jobs.clear_all()
    job_id = jobs.create_job("t")

    def work(x):
        return {"rb_id": "abc", "value": x}

    jobs.run_async(job_id, work, 42)
    for _ in range(20):
        time.sleep(0.05)
        if jobs.get_job(job_id)["status"] in ("completed", "error"):
            break
    job = jobs.get_job(job_id)
    assert job["status"] == "completed"
    assert job["rb_id"] == "abc"
    assert job["result"]["value"] == 42


def test_run_async_error_in_result():
    import jobs
    jobs.clear_all()
    job_id = jobs.create_job("t")

    def work():
        return {"error": "something broke", "rb_id": "xyz"}

    jobs.run_async(job_id, work)
    for _ in range(20):
        time.sleep(0.05)
        if jobs.get_job(job_id)["status"] in ("completed", "error"):
            break
    job = jobs.get_job(job_id)
    assert job["status"] == "error"
    assert "something broke" in job["error"]


def test_run_async_exception():
    import jobs
    jobs.clear_all()
    job_id = jobs.create_job("t")

    def work():
        raise ValueError("boom")

    jobs.run_async(job_id, work)
    for _ in range(20):
        time.sleep(0.05)
        if jobs.get_job(job_id)["status"] in ("completed", "error"):
            break
    job = jobs.get_job(job_id)
    assert job["status"] == "error"
    assert "ValueError" in job["error"]


def test_list_jobs_pagination():
    import jobs
    jobs.clear_all()
    for i in range(5):
        jobs.create_job(f"kind_{i}")
        time.sleep(0.001)
    total, items = jobs.list_jobs(offset=0, limit=2)
    assert total == 5
    assert len(items) == 2
    total, items = jobs.list_jobs(offset=2, limit=2)
    assert total == 5
    assert len(items) == 2


def test_list_jobs_ordered_newest_first():
    import jobs
    jobs.clear_all()
    a = jobs.create_job("a")
    time.sleep(0.01)
    b = jobs.create_job("b")
    _, items = jobs.list_jobs()
    assert items[0]["job_id"] == b
    assert items[1]["job_id"] == a
