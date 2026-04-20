from __future__ import annotations

from tests.agent_fakes import (
    fake_chat_response,
    install_fake_tools,
    queued_chat_with_tools,
)


def test_full_pipeline_search_fetch_save_finish_extend(tmp_db, monkeypatch):
    """End-to-end: create RB -> agent searches -> fetches -> saves -> finishes.
    Then extend: agent finds NEW items -> dedup merges correctly.
    """
    import agent
    import research_box as rb_store

    monkeypatch.setattr(agent, "_plan", lambda t: "1. search 2. fetch 3. save 4. finish")

    round1 = [
        fake_chat_response(tool_calls=[("web_search", {"query": "ki startups deutschland"})]),
        fake_chat_response(tool_calls=[("fetch_url", {"url": "https://source1.de/ki"})]),
        fake_chat_response(tool_calls=[("save_json", {"filename": "ki1", "data": [
            {"name": "Aleph Alpha", "source_url": "https://source1.de/ki"},
            {"name": "DeepL",       "source_url": "https://source1.de/ki"},
        ]})]),
        fake_chat_response(tool_calls=[("finish", {"result": [
            {"name": "Aleph Alpha", "source_url": "https://source1.de/ki"},
            {"name": "DeepL",       "source_url": "https://source1.de/ki"},
        ]})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(round1))

    install_fake_tools(monkeypatch, {
        "web_search": lambda **kw: [
            {"title": "Top KI", "url": "https://source1.de/ki", "snippet": "Aleph Alpha, DeepL"}
        ],
        "fetch_url": lambda **kw: "Aleph Alpha und DeepL sind fuehrende deutsche KI-Unternehmen.",
        "save_json": lambda **kw: "/tmp/ki1.json",
    })

    out1 = agent.run(
        "finde 2 deutsche ki startups",
        verbose=False,
        output_fields=["name", "source_url"],
    )

    rb_id = out1["rb_id"]
    rb = rb_store.load(rb_id)

    assert rb.status == "completed"
    assert rb.iterations == 1
    assert len(rb.extracted_data) == 2
    assert rb.output_fields == ["name", "source_url"]
    assert "https://source1.de/ki" in rb.visited_sources
    assert rb.validation["confidence"] > 0

    round2 = [
        fake_chat_response(tool_calls=[("web_search", {"query": "ki startups mehr"})]),
        fake_chat_response(tool_calls=[("fetch_url", {"url": "https://source2.de/more"})]),
        fake_chat_response(tool_calls=[("save_json", {"filename": "ki2", "data": [
            {"name": "Helsing", "source_url": "https://source2.de/more"},
            {"name": "DeepL",   "source_url": "https://source2.de/more"},
        ]})]),
        fake_chat_response(tool_calls=[("finish", {"result": [
            {"name": "Helsing", "source_url": "https://source2.de/more"},
            {"name": "DeepL",   "source_url": "https://source2.de/more"},
        ]})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(round2))
    install_fake_tools(monkeypatch, {
        "web_search": lambda **kw: [
            {"title": "more ki", "url": "https://source2.de/more", "snippet": "Helsing"}
        ],
        "fetch_url": lambda **kw: "Helsing ist ein KI-Startup.",
        "save_json": lambda **kw: "/tmp/ki2.json",
    })

    out2 = agent.run("finde 2 deutsche ki startups", verbose=False, rb_id=rb_id, extend=True)
    rb = rb_store.load(rb_id)

    assert out2["rb_id"] == rb_id
    assert rb.iterations == 2
    names = {it["name"] for it in rb.extracted_data}
    assert names == {"Aleph Alpha", "DeepL", "Helsing"}

    dedup = rb.validation.get("dedup") or {}
    assert dedup.get("added") == 1
    assert dedup.get("skipped") + dedup.get("updated") >= 1


def test_full_pipeline_verify_and_analyze_after_run(tmp_db, monkeypatch):
    """After an agent run, verify and deep-analyze should both work on the same RB."""
    import agent
    import research_box as rb_store

    monkeypatch.setattr(agent, "_plan", lambda t: "")
    responses = [
        fake_chat_response(tool_calls=[("finish", {"result": [
            {"name": "Acme", "source_url": "https://ex.com/a"},
        ]})]),
    ]
    monkeypatch.setattr(agent, "chat_with_tools", queued_chat_with_tools(responses))

    out = agent.run("t", verbose=False)
    rb_id = out["rb_id"]

    rb = rb_store.load(rb_id)
    rb.visited_sources = ["https://ex.com/a"]
    rb.save()

    monkeypatch.setattr(
        agent, "fetch_url",
        lambda url, max_chars=20000: "Acme Corporation ist eine Firma.",
    )
    v = agent.verify_rb(rb_id)
    assert v["supported"] == 1
    assert v["mode"] == "verify"

    monkeypatch.setattr(agent, "web_search", lambda query, max_results=5: [
        {"title": "Acme on wikipedia", "url": "https://wikipedia.org/acme", "snippet": "Acme"}
    ])
    monkeypatch.setattr(agent, "chat", lambda messages, temperature=0.0: '{"supported": true, "confidence": 90, "reason": "ok"}')

    deep = agent.analyze_rows_rb(rb_id, methods=["name_substring", "cross_source", "llm_semantic"])
    assert deep["total"] == 1
    methods_summary = deep["methods_summary"]
    assert set(methods_summary.keys()) == {"name_substring", "cross_source", "llm_semantic"}
