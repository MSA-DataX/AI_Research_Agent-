from __future__ import annotations

import json
from types import SimpleNamespace


def fake_chat_response(content: str | None = None, tool_calls: list | None = None):
    tcs = None
    if tool_calls:
        tcs = []
        for i, call in enumerate(tool_calls):
            name, args = call
            tcs.append(
                SimpleNamespace(
                    id=f"call_{i}",
                    function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                )
            )
    msg = SimpleNamespace(content=content or "", tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def queued_chat_with_tools(responses: list):
    """Returns a fake chat_with_tools fn that yields the queued responses in order."""
    counter = {"i": 0}

    def _fake(messages, tools, temperature=0.2):
        if counter["i"] >= len(responses):
            return fake_chat_response(tool_calls=[("finish", {"result": "<exhausted>"})])
        r = responses[counter["i"]]
        counter["i"] += 1
        return r

    return _fake


def install_fake_tools(monkeypatch, overrides: dict):
    """Replace entries in agent.TOOLS with the given callables for the test duration."""
    import agent
    import tools as tools_mod
    original = dict(tools_mod.TOOLS)
    patched = {**original, **overrides}
    monkeypatch.setattr(agent, "TOOLS", patched)
    monkeypatch.setattr(tools_mod, "TOOLS", patched)
