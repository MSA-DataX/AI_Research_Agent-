from __future__ import annotations


def test_list_empty(tmp_db):
    from fastapi.testclient import TestClient
    from api import app
    c = TestClient(app)
    r = c.get("/research_box")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["offset"] == 0


def test_get_not_found(tmp_db):
    from fastapi.testclient import TestClient
    from api import app
    c = TestClient(app)
    r = c.get("/research_box/doesnotexist")
    assert r.status_code == 404


def test_validation_methods_endpoint(tmp_db):
    from fastapi.testclient import TestClient
    from api import app
    c = TestClient(app)
    r = c.get("/validation_methods")
    assert r.status_code == 200
    body = r.json()
    assert "name_substring" in body["methods"]
    assert "llm_semantic" in body["methods"]
    assert "cross_source" in body["methods"]


def test_pagination_offset_and_limit(tmp_db):
    import research_box as rb_store
    from fastapi.testclient import TestClient
    from api import app
    for i in range(5):
        rb_store.create(f"task {i}")
    c = TestClient(app)

    r = c.get("/research_box?offset=0&limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    r = c.get("/research_box?offset=4&limit=10")
    assert len(r.json()["items"]) == 1


def test_openapi_schema_loads(tmp_db):
    from fastapi.testclient import TestClient
    from api import app
    c = TestClient(app)
    r = c.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert "/research_box" in paths
    assert "/jobs" in paths
    assert "/jobs/{job_id}" in paths


def test_get_and_delete(tmp_db):
    import research_box as rb_store
    from fastapi.testclient import TestClient
    from api import app
    rb = rb_store.create("api test")
    rb.extracted_data = [{"name": "X", "source_url": "https://x.com"}]
    rb.save()
    c = TestClient(app)

    r = c.get(f"/research_box/{rb.id}")
    assert r.status_code == 200
    assert r.json()["task"] == "api test"

    r = c.delete(f"/research_box/{rb.id}")
    assert r.status_code == 200

    r = c.get(f"/research_box/{rb.id}")
    assert r.status_code == 404


def test_validation_endpoint(tmp_db):
    import research_box as rb_store
    from fastapi.testclient import TestClient
    from api import app
    rb = rb_store.create("t")
    rb.validation = {"confidence": 77, "label": "medium"}
    rb.save()
    c = TestClient(app)
    r = c.get(f"/research_box/{rb.id}/validation")
    assert r.status_code == 200
    assert r.json()["validation"]["confidence"] == 77


def test_export_csv(tmp_db):
    import research_box as rb_store
    from fastapi.testclient import TestClient
    from api import app
    rb = rb_store.create("exp")
    rb.extracted_data = [
        {"name": "A", "source_url": "https://a.com"},
        {"name": "B", "source_url": "https://b.com"},
    ]
    rb.save()
    c = TestClient(app)
    r = c.get(f"/research_box/{rb.id}/export?fmt=csv")
    assert r.status_code == 200
    body = r.text
    assert "name,source_url" in body or "source_url,name" in body
    assert "A" in body and "B" in body
