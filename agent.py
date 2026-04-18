from __future__ import annotations

import json
import os
from datetime import datetime

from config import ENABLE_PLANNING, LOG_DIR, MAX_ITERATIONS
from llm_client import chat, chat_with_tools
from memory import recall, remember
from tools import TOOL_SCHEMAS, TOOLS

PLANNER_PROMPT = """You are a research planner. Output a SHORT plan (2-4 numbered steps)
for accomplishing the task using only these tools: web_search, web_search_parallel,
fetch_url, save_json, finish. Output plain text - no JSON, no code fences.
Be concrete (e.g. 'web_search: deutsche KI-Startups 2026').
Keep the whole plan under 300 characters."""


def _plan(task: str) -> str:
    try:
        return chat(
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": f"Task: {task}"},
            ],
            temperature=0.2,
        ).strip()
    except Exception as e:
        return f"[planning skipped: {e}]"

SYSTEM_PROMPT = """You are a rigorous autonomous research agent.

ABSOLUTE RULES (never violate):
1. NEVER invent facts, company names, URLs, numbers or addresses. Every single
   item in your final output MUST be traceable to a tool observation from THIS
   session (web_search snippet or fetch_url content). If it is not in your
   observations, DO NOT output it.
2. Prior-session hints are NOT verified data. Re-confirm with web_search /
   fetch_url before reusing anything from them.
3. Each fact in the final output needs a "source_url" pointing to a URL that
   actually appeared in a tool observation this session.
4. If web_search returns unrelated results (e.g. the wrong topic), refine the
   query - do not fabricate an answer.
5. Cross-check important numbers against 2+ independent sources. Mark
   unverified or conflicting values with "confidence": "low".
6. Always call save_json to persist the final structured result, THEN call
   finish with the same data.
7. Do not ask the user any questions. Make reasonable assumptions and proceed.
8. Prefer clean structured JSON (arrays/objects) over prose in outputs.
9. Stop searching once you have enough verified data - do not loop endlessly.

WORKFLOW:
- Plan briefly, then act via tool calls.
- Typical loop: web_search -> fetch_url on 1-2 promising hits -> save_json -> finish.
- Use the user's language in outputs.
"""


def _truncate(s: str, limit: int = 6000) -> str:
    return s if len(s) <= limit else s[:limit] + "\n...[truncated]"


def _log_trace(task: str, trace: list[dict]) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"run_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"task": task, "trace": trace}, f, ensure_ascii=False, indent=2)
    return path


def _run_tool(name: str, args: dict):
    fn = TOOLS.get(name)
    if fn is None:
        return f"[error] unknown tool '{name}'"
    try:
        return fn(**args)
    except TypeError as e:
        return f"[arg error] {e}"
    except Exception as e:
        return f"[runtime error] {e}"


def run(task: str, verbose: bool = True, on_event=None, cancel_event=None) -> dict:
    def emit(event: dict) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                pass

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    memory_snippet = recall(task)
    plan_text = _plan(task) if ENABLE_PLANNING and not cancelled() else ""
    if plan_text:
        emit({"type": "plan", "text": plan_text})

    user_content = f"Task: {task}"
    if plan_text:
        user_content += f"\n\nYour plan (follow it, adjust only if tool results require it):\n{plan_text}"
        if verbose:
            print(f"\n[plan]\n{plan_text}\n")
    if memory_snippet:
        user_content += (
            "\n\nHINTS from past sessions (titles + source URLs only - NOT verified data).\n"
            "You MUST still run web_search / fetch_url to confirm facts before outputting them:\n"
            f"{memory_snippet}"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    trace: list[dict] = []
    collected_sources: list[str] = []

    for step_idx in range(1, MAX_ITERATIONS + 1):
        if cancelled():
            log_path = _log_trace(task, trace)
            emit({"type": "cancelled"})
            return {"error": "cancelled by user", "trace_log": log_path, "sources_seen": collected_sources}

        try:
            resp = chat_with_tools(messages, TOOL_SCHEMAS)
        except Exception as e:
            log_path = _log_trace(task, trace)
            msg_txt = str(e)
            if "Connection" in msg_txt or "refused" in msg_txt.lower() or "Max retries" in msg_txt:
                msg_txt = (
                    "LM Studio server not reachable. Start LM Studio, load a model and "
                    "click 'Start Server' (port 1234). Original: " + msg_txt[:200]
                )
            emit({"type": "error", "message": msg_txt})
            return {"error": msg_txt, "trace_log": log_path, "sources_seen": collected_sources}
        msg = resp.choices[0].message

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            if verbose:
                print(f"\n[step {step_idx}] (no tool call) model said: {(msg.content or '')[:200]}")
            messages.append(
                {
                    "role": "user",
                    "content": "You must call a tool or the finish function. Do not answer in plain text.",
                }
            )
            continue

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception as e:
                args = {}
                obs: object = f"[arg parse error] {e}: {tc.function.arguments}"
            else:
                obs = None

            if verbose:
                try:
                    print(f"\n[step {step_idx}] tool={name} args={args}")
                except UnicodeEncodeError:
                    print(f"\n[step {step_idx}] tool={name} args=<non-printable args>")
            emit({"step": step_idx, "type": "tool_call", "name": name, "args": args})

            if name == "finish":
                result = args.get("result", args)
                trace.append({"step": step_idx, "action": "finish", "args": args})
                remember(task, result, collected_sources)
                log_path = _log_trace(task, trace)
                if verbose:
                    print(f"\n[done] log: {log_path}")
                emit({"type": "finish", "result": result})
                return {"result": result, "trace_log": log_path, "sources_seen": collected_sources}

            if obs is None:
                obs = _run_tool(name, args)

            if name == "web_search" and isinstance(obs, list):
                for r in obs:
                    u = r.get("url") if isinstance(r, dict) else None
                    if u and u not in collected_sources:
                        collected_sources.append(u)
            elif name == "web_search_parallel" and isinstance(obs, dict):
                for rs in obs.values():
                    for r in rs or []:
                        u = r.get("url") if isinstance(r, dict) else None
                        if u and u not in collected_sources:
                            collected_sources.append(u)

            obs_str = obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False)
            obs_str = _truncate(obs_str)
            emit({"step": step_idx, "type": "observation", "name": name, "preview": obs_str[:300]})

            if name == "save_json" and isinstance(obs, str) and not obs.startswith("["):
                obs_str += (
                    "\n\n[SYSTEM NUDGE] File saved. Your next action MUST be `finish` "
                    "with the SAME data you just saved. Do not search further."
                )

            trace.append(
                {
                    "step": step_idx,
                    "action": name,
                    "args": args,
                    "observation_preview": obs_str[:400],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": obs_str,
                }
            )

    log_path = _log_trace(task, trace)
    return {"error": "max iterations reached", "trace_log": log_path, "sources_seen": collected_sources}
