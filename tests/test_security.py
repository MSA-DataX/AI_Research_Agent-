from __future__ import annotations


def _client(monkeypatch, api_key: str = "", rate_limit: int = 0):
    import config
    import security
    monkeypatch.setattr(config, "API_KEY", api_key)
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", rate_limit)
    monkeypatch.setattr(security, "API_KEY", api_key)
    monkeypatch.setattr(security, "RATE_LIMIT_PER_MINUTE", rate_limit)
    security.reset_rate_limit()

    from fastapi.testclient import TestClient
    from api import app
    return TestClient(app)


def test_no_api_key_needed_when_disabled(tmp_db, monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/research_box")
    assert r.status_code == 200


def test_api_key_enabled_blocks_without_header(tmp_db, monkeypatch):
    c = _client(monkeypatch, api_key="secret123")
    r = c.get("/research_box")
    assert r.status_code == 401
    body = r.json()
    assert body["detail"]["code"] == "UNAUTHORIZED"


def test_api_key_enabled_allows_with_header(tmp_db, monkeypatch):
    c = _client(monkeypatch, api_key="secret123")
    r = c.get("/research_box", headers={"X-API-Key": "secret123"})
    assert r.status_code == 200


def test_api_key_wrong_value_rejected(tmp_db, monkeypatch):
    c = _client(monkeypatch, api_key="secret123")
    r = c.get("/research_box", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_public_endpoints_ignore_api_key(tmp_db, monkeypatch):
    c = _client(monkeypatch, api_key="secret123")
    for path in ("/", "/docs", "/openapi.json"):
        r = c.get(path)
        assert r.status_code in (200, 307), f"{path} returned {r.status_code}"


def test_rate_limit_blocks_after_threshold(tmp_db, monkeypatch):
    c = _client(monkeypatch, rate_limit=3)
    ok = 0
    for _ in range(3):
        if c.get("/research_box").status_code == 200:
            ok += 1
    assert ok == 3
    r = c.get("/research_box")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "RATE_LIMITED"


def test_rate_limit_zero_means_unlimited(tmp_db, monkeypatch):
    c = _client(monkeypatch, rate_limit=0)
    for _ in range(20):
        r = c.get("/research_box")
        assert r.status_code == 200


def test_cors_headers_present(tmp_db, monkeypatch):
    c = _client(monkeypatch)
    r = c.options(
        "/research_box",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8501"


def test_index_reports_security_state(tmp_db, monkeypatch):
    c = _client(monkeypatch, api_key="xyz", rate_limit=42)
    r = c.get("/")
    body = r.json()
    assert body["security"]["api_key_required"] is True
    assert body["security"]["rate_limit_per_minute"] == 42


def test_openapi_has_security_scheme_when_api_key_set(tmp_db, monkeypatch):
    from api import app
    app.openapi_schema = None
    c = _client(monkeypatch, api_key="secret")
    app.openapi_schema = None
    r = c.get("/openapi.json", headers={"X-API-Key": "secret"})
    assert r.status_code == 200
    schema = r.json()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "ApiKeyHeader" in schemes
    assert schemes["ApiKeyHeader"]["name"] == "X-API-Key"
    rb_path = schema["paths"]["/research_box"]
    get_op = rb_path.get("get", {})
    assert any("ApiKeyHeader" in s for s in get_op.get("security", []))


def test_openapi_no_security_scheme_when_api_key_disabled(tmp_db, monkeypatch):
    from api import app
    app.openapi_schema = None
    c = _client(monkeypatch, api_key="")
    app.openapi_schema = None
    r = c.get("/openapi.json")
    assert r.status_code == 200
    schemes = r.json().get("components", {}).get("securitySchemes", {})
    assert "ApiKeyHeader" not in schemes
