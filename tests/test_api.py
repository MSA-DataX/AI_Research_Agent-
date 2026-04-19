from __future__ import annotations


def test_list_empty(tmp_db):
    from fastapi.testclient import TestClient
    from api import app
    c = TestClient(app)
    r = c.get("/research_box")
    assert r.status_code == 200
    assert r.json() == []


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
    assert "name_substring" in body
    assert "llm_semantic" in body
    assert "cross_source" in body


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
