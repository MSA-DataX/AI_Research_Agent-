from __future__ import annotations

from tests.agent_fakes import (
    fake_chat_response,
    install_fake_tools,
    queued_chat_with_tools,
)


def _no_plan(monkeypatch):
    import agent
    monkeypatch.setattr(agent, "_plan", lambda task: "")


def test_truncate_short_passthrough():
    from agent import _truncate
    assert _truncate("short", limit=10) == "short"


def test_truncate_long_cuts_and_marks():
    from agent import _truncate
    s = "x" * 100
    out = _truncate(s, limit=20)
    assert len(out) <= 20 + len("\n...[truncated]")
    assert out.endswith("[truncated]")


def test_run_tool_dispatch_success(monkeypatch):
    from agent import _run_tool
    import agent
    monkeypatch.setattr(agent, "TOOLS", {"greet": lambda name: f"hi {name}"})
    assert _run_tool("greet", {"name": "Alice"}) == "hi Alice"


def test_run_tool_unknown_name():
    from agent import _run_tool
    result = _run_tool("does_not_exist", {})
    assert "unknown tool" in result


def test_run_tool_arg_error(monkeypatch):
    from agent import _run_tool
    import agent
    monkeypatch.setattr(agent, "TOOLS", {"f": lambda a, b: a + b})
    result = _run_tool("f", {"a": 1})
    assert "arg error" in result


def test_format_user_content_with_schema(tmp_db):
    import research_box as rb_store
    from agent import _format_user_content

    rb = rb_store.create("find ki-startups")
    rb.output_fields = ["name", "website", "source_url"]
    rb.save()
    content = _format_user_content("find ki-startups", rb, extend=False)
    assert "STRICT OUTPUT SCHEMA" in content
    assert "name, website, source_url" in content


def test_format_user_content_extend_mode(tmp_db):
    import research_box as rb_store
    from agent import _format_user_content

    rb = rb_store.create("t")
    rb.visited_sources = ["https://a.com", "https://b.com"]
    rb.extracted_data = [{"name": "Alpha"}]
    rb.save()
    content = _format_user_content("t", rb, extend=True)
    assert "EXTENDING" in content
    assert "https://a.com" in content
    assert "Alpha" in content


def test_run_happy_path_search_save_finish(tmp_db, monkeypatch):
    import agent
    _no_plan(monkeypatch)

    responses = [
        fake_chat_response(tool_calls=[("web_search", {"query": "ki startups deutschland"})]),
        fake_chat_response(tool_calls=[("fetch_url", {"url": "https://handelsblatt.de/x"})]),
        fake_chat_response(tool_calls=[("save_json", {"filename": "out", "data": [
            {"name": "Aleph Alpha", "source_url": "https://handelsblatt.de/x"}
        ]})]),
        fake_chat_response(tool_calls=[("finish", {"result": [
            {"name": "Aleph Alpha", "source_url": "https://handelsblatt.de/x"}
        ]})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(responses))

    install_fake_tools(monkeypatch, {
        "web_search": lambda **kw: [
            {"title": "Handelsblatt", "url": "https://handelsblatt.de/x", "snippet": "Aleph Alpha..."}
        ],
        "fetch_url": lambda **kw: "Aleph Alpha ist ein deutsches KI-Unternehmen aus Heidelberg.",
        "save_json": lambda **kw: "/tmp/fake.json",
    })

    out = agent.run("find ki startups", verbose=False)
    assert "rb_id" in out
    assert len(out["result"]) == 1
    assert out["result"][0]["name"] == "Aleph Alpha"
    assert out["validation"]["total"] == 1


def test_run_lm_studio_connection_error_graceful(tmp_db, monkeypatch):
    import agent
    _no_plan(monkeypatch)

    def raise_conn(messages, tools, temperature=0.2):
        raise ConnectionError("Connection refused by LM Studio")

    monkeypatch.setattr(agent, "chat_with_tools", raise_conn)
    out = agent.run("x", verbose=False)
    assert "error" in out
    assert "LM Studio" in out["error"] or "reachable" in out["error"].lower()


def test_run_cancel_event_stops_loop(tmp_db, monkeypatch):
    import threading
    import agent
    _no_plan(monkeypatch)

    cancel = threading.Event()
    cancel.set()

    def should_not_be_called(messages, tools, temperature=0.2):
        raise AssertionError("agent should have cancelled before calling chat")

    monkeypatch.setattr(agent, "chat_with_tools", should_not_be_called)
    out = agent.run("x", verbose=False, cancel_event=cancel)
    assert "cancelled" in out["error"]


def test_run_max_iterations_reached(tmp_db, monkeypatch):
    import agent
    _no_plan(monkeypatch)

    def never_finish(messages, tools, temperature=0.2):
        return fake_chat_response(tool_calls=[("web_search", {"query": "loop"})])

    monkeypatch.setattr(agent, "chat_with_tools", never_finish)
    install_fake_tools(monkeypatch, {"web_search": lambda **kw: []})

    out = agent.run("x", verbose=False)
    assert out.get("error") == "max iterations reached"


def test_run_with_output_fields_stored_on_rb(tmp_db, monkeypatch):
    import agent
    import research_box as rb_store
    _no_plan(monkeypatch)

    responses = [
        fake_chat_response(tool_calls=[("finish", {"result": []})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(responses))

    out = agent.run("t", verbose=False, output_fields=["name", "date", "source_url"])
    rb = rb_store.load(out["rb_id"])
    assert rb.output_fields == ["name", "date", "source_url"]


def test_validate_rb_unknown_id(tmp_db):
    from agent import validate_rb
    result = validate_rb("does-not-exist")
    assert "error" in result


def test_validate_rb_happy(tmp_db):
    import research_box as rb_store
    from agent import validate_rb

    rb = rb_store.create("t")
    rb.extracted_data = [{"name": "A", "source_url": "https://a.com"}]
    rb.visited_sources = ["https://a.com"]
    rb.save()

    v = validate_rb(rb.id)
    assert v["total"] == 1
    assert v["supported"] == 1


def test_verify_rb_404_source_reports_unverified(tmp_db, monkeypatch):
    import research_box as rb_store
    from agent import verify_rb
    import agent

    rb = rb_store.create("t")
    rb.extracted_data = [{"name": "Ghost", "source_url": "https://dead.example/x"}]
    rb.visited_sources = ["https://dead.example/x"]
    rb.save()

    monkeypatch.setattr(agent, "fetch_url", lambda url, max_chars=20000: "[fetch error] 404")
    v = verify_rb(rb.id)
    assert v["supported"] == 0
    assert v["label"] == "unverified"


def test_verify_rb_substring_match_confirms(tmp_db, monkeypatch):
    import research_box as rb_store
    from agent import verify_rb
    import agent

    rb = rb_store.create("t")
    rb.extracted_data = [{"name": "Aleph Alpha", "source_url": "https://x.com"}]
    rb.visited_sources = ["https://x.com"]
    rb.save()

    monkeypatch.setattr(
        agent, "fetch_url",
        lambda url, max_chars=20000: "... Aleph Alpha ist ein KI-Unternehmen ...",
    )
    v = verify_rb(rb.id)
    assert v["supported"] == 1
    assert v["confidence"] == 100


def test_analyze_rows_combines_methods(tmp_db, monkeypatch):
    import research_box as rb_store
    from agent import analyze_rows_rb
    import agent

    rb = rb_store.create("t")
    rb.extracted_data = [
        {"name": "Acme", "source_url": "https://a.com"},
        {"name": "Foo", "source_url": "https://b.com"},
    ]
    rb.visited_sources = ["https://a.com", "https://b.com"]
    rb.save()

    pages = {
        "https://a.com": "Acme ist bekannt",
        "https://b.com": "Seite ohne den gesuchten Unternehmensnamen",
    }
    monkeypatch.setattr(agent, "fetch_url", lambda url, max_chars=20000: pages.get(url, ""))
    monkeypatch.setattr(agent, "web_search", lambda query, max_results=5: [])
    monkeypatch.setattr(agent, "chat", lambda messages, temperature=0.0: '{"supported": false}')

    v = analyze_rows_rb(rb.id, methods=["name_substring"])
    rows = v["per_row"]
    assert rows[0]["verdict"] == "high"
    assert rows[1]["verdict"] == "unverified"


def test_extend_rb_reuses_existing_id(tmp_db, monkeypatch):
    import research_box as rb_store
    from agent import extend_rb
    import agent
    _no_plan(monkeypatch)

    rb = rb_store.create("t")
    rb.extracted_data = [{"name": "Alpha"}]
    rb.save()
    original_id = rb.id

    responses = [
        fake_chat_response(tool_calls=[("finish", {"result": [{"name": "Beta"}]})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(responses))

    out = extend_rb(rb.id)
    assert out["rb_id"] == original_id
    rb2 = rb_store.load(original_id)
    assert rb2.iterations == 1
    names = {it["name"] for it in rb2.extracted_data}
    assert names == {"Alpha", "Beta"}
