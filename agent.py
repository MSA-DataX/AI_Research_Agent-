from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

import research_box as rb_store
import validation
from config import ENABLE_PLANNING, LOG_DIR, MAX_ITERATIONS
from llm_client import chat, chat_with_tools
from research_box import ResearchBox
from tools import TOOL_SCHEMAS, TOOLS, fetch_url, web_search
import validators

SYSTEM_PROMPT = """You are a rigorous autonomous research agent.

ABSOLUTE RULES (never violate):
1. NEVER invent facts, company names, URLs, numbers or addresses. Every single
   item in your final output MUST be traceable to a tool observation from THIS
   session (web_search snippet or fetch_url content).
2. Prior-session hints are NOT verified data. Re-confirm with web_search /
   fetch_url before reusing anything from them.
3. Each fact in the final output needs a "source_url" pointing to a URL that
   actually appeared in a tool observation this session.
4. If web_search returns unrelated results, refine the query.
5. Cross-check important numbers against 2+ independent sources. Mark
   unverified or conflicting values with "confidence": "low".
6. Always call save_json to persist the final structured result, THEN call
   finish with the same data.
7. Do not ask the user any questions. Decide autonomously.
8. Prefer clean structured JSON (arrays/objects) over prose in outputs.
9. Stop searching once you have enough verified data.

WORKFLOW:
- Plan briefly, then act via tool calls.
- Typical loop: web_search -> fetch_url on 1-2 promising hits -> save_json -> finish.
- Use the user's language in outputs.
"""

PLANNER_PROMPT = """You are a research planner. Output a SHORT plan (2-4 numbered steps)
for accomplishing the task using only these tools: web_search, web_search_parallel,
fetch_url, extract_contacts, save_json, finish. Output plain text - no JSON, no code
fences. Be concrete. Keep the whole plan under 300 characters."""


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


def _format_user_content(task: str, rb: ResearchBox, extend: bool) -> str:
    parts = [f"Task: {task}"]
    if extend and rb.visited_sources:
        avoid = "\n".join(f"- {u}" for u in rb.visited_sources[:30])
        parts.append(
            "You are EXTENDING an existing Research Box. Find NEW sources / NEW items.\n"
            f"Avoid re-using these already-visited URLs:\n{avoid}"
        )
        if rb.extracted_data:
            parts.append(
                "Already-collected items (DO NOT duplicate, add only NEW ones):\n"
                + json.dumps(rb.extracted_data, ensure_ascii=False)[:1500]
            )
    hints = rb_store.recall_hints(task)
    if hints:
        parts.append(
            "HINTS from past sessions (titles + source URLs only - NOT verified data).\n"
            "Re-confirm with web_search / fetch_url before using:\n" + hints
        )
    return "\n\n".join(parts)


def run(
    task: str,
    verbose: bool = True,
    on_event=None,
    cancel_event=None,
    rb_id: Optional[str] = None,
    extend: bool = False,
) -> dict:
    def emit(event: dict) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                pass

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if rb_id:
        rb = rb_store.load(rb_id) or rb_store.create(task)
    else:
        similar = rb_store.find_similar(task) if not extend else None
        rb = similar if similar is not None else rb_store.create(task)
    if rb.task != task and not rb.task:
        rb.task = task
    rb.status = "running"
    rb.iterations += 1
    rb.save()
    emit({"type": "rb", "id": rb.id, "reused": rb.iterations > 1})

    plan_text = _plan(task) if ENABLE_PLANNING and not cancelled() else ""
    if plan_text:
        emit({"type": "plan", "text": plan_text})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _format_user_content(task, rb, extend)},
    ]
    trace: list[dict] = []
    entities: dict = dict(rb.entities or {})

    for step_idx in range(1, MAX_ITERATIONS + 1):
        if cancelled():
            rb.status = "cancelled"
            rb.save()
            log_path = _log_trace(task, trace)
            emit({"type": "cancelled"})
            return {"error": "cancelled by user", "trace_log": log_path, "rb_id": rb.id}

        try:
            resp = chat_with_tools(messages, TOOL_SCHEMAS)
        except Exception as e:
            rb.status = "error"
            rb.save()
            log_path = _log_trace(task, trace)
            msg_txt = str(e)
            if "Connection" in msg_txt or "refused" in msg_txt.lower() or "Max retries" in msg_txt:
                msg_txt = (
                    "LM Studio server not reachable. Start LM Studio, load a model and "
                    "click 'Start Server' (port 1234). Original: " + msg_txt[:200]
                )
            emit({"type": "error", "message": msg_txt})
            return {"error": msg_txt, "trace_log": log_path, "rb_id": rb.id}

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
                obs = None
            except Exception as e:
                args = {}
                obs = f"[arg parse error] {e}: {tc.function.arguments}"

            if verbose:
                try:
                    print(f"\n[step {step_idx}] tool={name} args={args}")
                except UnicodeEncodeError:
                    print(f"\n[step {step_idx}] tool={name} args=<non-printable>")
            emit({"step": step_idx, "type": "tool_call", "name": name, "args": args})

            if name == "finish":
                result = args.get("result", args)
                trace.append({"step": step_idx, "action": "finish", "args": args})

                rb.extracted_data = result
                rb.entities = entities
                rb.status = "completed"
                rb.validation = validation.compute(result, rb.visited_sources)
                rb.save()

                log_path = _log_trace(task, trace)
                if verbose:
                    print(f"\n[done] log: {log_path}")
                emit({"type": "finish", "result": result, "validation": rb.validation})
                return {
                    "result": result,
                    "trace_log": log_path,
                    "rb_id": rb.id,
                    "validation": rb.validation,
                    "visited_sources": rb.visited_sources,
                    "sources_seen": rb.sources,
                }

            if obs is None:
                obs = _run_tool(name, args)

            visited_set = set(rb.visited_sources)
            if name == "web_search" and isinstance(obs, list):
                if extend:
                    obs = [r for r in obs if isinstance(r, dict) and r.get("url") not in visited_set]
                for r in obs:
                    u = r.get("url") if isinstance(r, dict) else None
                    if u:
                        rb.add_sources([u])
            elif name == "web_search_parallel" and isinstance(obs, dict):
                if extend:
                    obs = {
                        q: [r for r in (rs or []) if isinstance(r, dict) and r.get("url") not in visited_set]
                        for q, rs in obs.items()
                    }
                for rs in obs.values():
                    for r in rs or []:
                        u = r.get("url") if isinstance(r, dict) else None
                        if u:
                            rb.add_sources([u])
            elif name == "fetch_url":
                url_arg = args.get("url")
                if url_arg:
                    rb.mark_visited(url_arg)
                    rb.add_sources([url_arg])
            elif name == "extract_contacts" and isinstance(obs, dict):
                for k, vals in obs.items():
                    bucket = entities.setdefault(k, [])
                    for v in vals or []:
                        if v not in bucket:
                            bucket.append(v)

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

    rb.status = "max_iterations"
    rb.save()
    log_path = _log_trace(task, trace)
    return {
        "error": "max iterations reached",
        "trace_log": log_path,
        "rb_id": rb.id,
        "visited_sources": rb.visited_sources,
    }


def validate_rb(rb_id: str) -> dict:
    rb = rb_store.load(rb_id)
    if rb is None:
        return {"error": "rb not found"}
    rb.validation = validation.compute(rb.extracted_data, rb.visited_sources)
    rb.save()
    return rb.validation


def extend_rb(rb_id: str, **kwargs) -> dict:
    rb = rb_store.load(rb_id)
    if rb is None:
        return {"error": "rb not found"}
    return run(rb.task, rb_id=rb_id, extend=True, **kwargs)


def analyze_rows_rb(
    rb_id: str,
    methods: Optional[list[str]] = None,
    on_event=None,
) -> dict:
    rb = rb_store.load(rb_id)
    if rb is None:
        return {"error": "rb not found"}

    def emit(ev: dict) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    chosen = methods or list(validators.METHODS.keys())
    chosen = [m for m in chosen if m in validators.METHODS]

    items: list[dict] = []
    if isinstance(rb.extracted_data, list):
        items = [x for x in rb.extracted_data if isinstance(x, dict)]
    elif isinstance(rb.extracted_data, dict):
        items = [rb.extracted_data]

    page_cache: dict[str, str] = {}
    per_row: list[dict] = []

    for idx, it in enumerate(items):
        src = it.get("source_url") or it.get("source") or ""
        name = validators._row_name(it) or f"row {idx}"
        emit({"type": "analyze_row", "index": idx, "total": len(items), "name": name})

        page_text = ""
        if src and ("name_substring" in chosen or "all_fields" in chosen):
            if src not in page_cache:
                page_cache[src] = fetch_url(src, max_chars=20000)
            page_text = page_cache[src]

        method_results: dict[str, dict] = {}
        if "name_substring" in chosen:
            method_results["name_substring"] = validators.method_name_substring(it, page_text)
        if "all_fields" in chosen:
            method_results["all_fields"] = validators.method_all_fields(it, page_text)
        if "cross_source" in chosen:
            method_results["cross_source"] = validators.method_cross_source(it, web_search)
        if "llm_semantic" in chosen:
            if not page_text and src:
                if src not in page_cache:
                    page_cache[src] = fetch_url(src, max_chars=20000)
                page_text = page_cache[src]
            method_results["llm_semantic"] = validators.method_llm_semantic(it, page_text, chat)

        label, confidence = validators.verdict_for_row(method_results)
        per_row.append(
            {
                "row_index": idx,
                "name": name,
                "source_url": src,
                "methods": method_results,
                "verdict": label,
                "confidence": confidence,
            }
        )

    total = len(per_row)
    method_totals: dict[str, dict] = {}
    for m in chosen:
        sup = sum(1 for r in per_row if r["methods"].get(m, {}).get("supported"))
        method_totals[m] = {
            "supported": sup,
            "total": total,
            "ratio": round(100 * sup / total) if total else 0,
        }

    overall_conf = int(round(sum(r["confidence"] for r in per_row) / total)) if total else 0
    if overall_conf >= 85:
        overall_label = "high"
    elif overall_conf >= 60:
        overall_label = "medium"
    elif overall_conf >= 30:
        overall_label = "low"
    else:
        overall_label = "unverified"

    report = {
        "confidence": overall_conf,
        "label": overall_label,
        "total": total,
        "methods_used": chosen,
        "methods_summary": method_totals,
        "per_row": per_row,
        "mode": "deep_analyze",
        "verified_at": datetime.now().isoformat(timespec="seconds"),
    }

    rb.validation = report
    rb.status = "verified"
    rb.save()
    emit({"type": "analyze_done", "validation": report})
    return report


def verify_rb(rb_id: str, on_event=None) -> dict:
    rb = rb_store.load(rb_id)
    if rb is None:
        return {"error": "rb not found"}

    def emit(ev: dict) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    items: list[dict] = []
    if isinstance(rb.extracted_data, list):
        items = [x for x in rb.extracted_data if isinstance(x, dict)]
    elif isinstance(rb.extracted_data, dict):
        items = [rb.extracted_data]

    page_cache: dict[str, str] = {}
    per_item: list[dict] = []

    for idx, it in enumerate(items, 1):
        src = it.get("source_url") or it.get("source") or ""
        name = it.get("name") or it.get("title") or next(iter(it.values()), "")
        name_str = str(name)[:120]

        emit({"type": "verify_item", "index": idx, "total": len(items), "name": name_str, "source": src})

        if not src or not name_str:
            per_item.append(
                {"name": name_str, "source": src, "supported": False, "reason": "missing source_url or name"}
            )
            continue

        if src not in page_cache:
            page_cache[src] = fetch_url(src, max_chars=20000)
        page = page_cache[src]

        if page.startswith("[fetch error]"):
            per_item.append({"name": name_str, "source": src, "supported": False, "reason": page[:120]})
            continue

        hay = page.lower()
        found = name_str.lower() in hay
        per_item.append(
            {
                "name": name_str,
                "source": src,
                "supported": found,
                "reason": "confirmed in page" if found else "name not found in page text",
            }
        )

    total = len(per_item)
    supported = sum(1 for x in per_item if x["supported"])
    n_src = len(rb.visited_sources)
    confidence = int(round(100 * supported / total)) if total else 0
    if confidence >= 85:
        label = "high"
    elif confidence >= 60:
        label = "medium"
    elif confidence >= 30:
        label = "low"
    else:
        label = "unverified"

    rb.validation = {
        "confidence": confidence,
        "label": label,
        "supported": supported,
        "total": total,
        "n_sources": n_src,
        "supporting_sources": sorted({x["source"] for x in per_item if x["supported"] and x["source"]}),
        "per_item": per_item,
        "mode": "verify",
        "verified_at": datetime.now().isoformat(timespec="seconds"),
    }
    rb.status = "verified"
    rb.save()
    emit({"type": "verify_done", "validation": rb.validation})
    return rb.validation
