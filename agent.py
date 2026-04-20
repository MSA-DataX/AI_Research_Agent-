from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import research_box as rb_store
import validation
import validators
from config import ENABLE_PLANNING, LOG_DIR, MAX_ITERATIONS
from dedup import merge_items
from llm_client import chat, chat_with_tools
from research_box import ResearchBox
from tools import TOOL_SCHEMAS, TOOLS, fetch_url, web_search

SYSTEM_PROMPT = """You are an advanced AI Research & Validation Agent.
Your job is not only to collect data, but to VALIDATE it intelligently.

GOAL
Return structured, high-quality, validated data. Each item must include
extracted fields, source_url and be reproducible from observations in THIS
session.

DATA COLLECTION RULES
1. Always prefer high-quality sources:
   - Tier 1: wikipedia.org, official government, academic sources.
   - Tier 2: known media (BBC, Handelsblatt, FAZ, Spiegel, Reuters, etc).
   - Tier 3: unknown websites.
   - Avoid: quiz / listicle / top-X pages unless no alternative exists.
2. If possible: use multiple sources per item, prefer 1 dedicated page per
   entity (not lists).

TOOL USAGE
- web_search / web_search_parallel -> find candidate URLs only.
- fetch_url -> read real content. MANDATORY before using any URL as source_url.
- extract_contacts -> ONLY for emails / phones / addresses. Do NOT use it for
  general data like city names, company names, dates.

CRITICAL SOURCE RULE
- A URL you only saw in a web_search snippet is NOT yet a valid source.
- You MUST call fetch_url on the URL and confirm the page actually contains
  the fact before writing the URL into an item's source_url field.
- If fetch_url returns 404 or unrelated content, pick another candidate and
  fetch that instead.
- Every item you output must have a fetch_url call on its source_url in this
  session.

STRUCTURED OUTPUT (MANDATORY)
- Final result MUST be JSON only. Always a LIST of objects, never prose.
- If a field is missing -> null. Do NOT invent values.
- Normalize values (trimmed strings, ISO dates where possible).
- Avoid duplicates.

CRITICAL THINKING
Do NOT assume correctness just because:
- a name appears in text
- a single source confirms it
Always watch for:
- contradictions (e.g. Berlin with bundesland=Brandenburg - Berlin IS its own
  Bundesland; do NOT write that).
- weak sources (quiz, listicle, affiliate blogs).
- missing relationships.
Reject weak data if you have no high-quality source.

FINISH PROTOCOL
- Before save_json: verify that every item's source_url has been fetched via
  fetch_url in this session. If not, fetch it first.
- Then call save_json(filename, data) to persist, THEN call finish(result)
  with the SAME data.
- Do NOT loop once you have enough verified data.

FAILURE HANDLING
- If nothing reliable can be found, return:
  [{"error": "no data found", "source_url": null}]

EXAMPLE
Task: "Nenne 2 deutsche Staedte mit Bundesland"
Correct final result:
[
  {"name": "Muenchen", "bundesland": "Bayern",  "source_url": "https://de.wikipedia.org/wiki/M%C3%BCnchen"},
  {"name": "Hamburg",  "bundesland": "Hamburg", "source_url": "https://de.wikipedia.org/wiki/Hamburg"}
]

Quality > speed. Use the user's language in outputs.
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

    if rb.output_fields:
        fields_spec = ", ".join(rb.output_fields)
        parts.append(
            "STRICT OUTPUT SCHEMA: Every item in your final result MUST be a JSON object "
            f"with EXACTLY these fields (all required, no others): {fields_spec}.\n"
            "- Use null for a field only if it is genuinely unknown after searching.\n"
            "- Do NOT invent data for any field.\n"
            "- Do NOT rename the fields.\n"
            "- 'source_url' (if in the list) MUST be a real URL from this session's observations."
        )

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
    output_fields: Optional[list[str]] = None,
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
    if output_fields and not rb.output_fields:
        rb.output_fields = [f.strip() for f in output_fields if f.strip()]
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
            low = msg_txt.lower()
            if "connection" in low or "refused" in low or "max retries" in low:
                msg_txt = (
                    "LM Studio server not reachable. Start LM Studio, load a model and "
                    "click 'Start Server' (port 1234). Original: " + msg_txt[:200]
                )
            elif "src property" in low or "valid json" in low:
                msg_txt = (
                    "Das Modell hat ungültiges JSON als Tool-Argument erzeugt. "
                    "Probier ein präziseres / längeres Task-Statement oder nimm qwen3-32b statt 14b. "
                    "Original: " + msg_txt[:200]
                )
            elif "invalid model" in low or "not loaded" in low or "no models loaded" in low:
                msg_txt = (
                    "Kein Modell in LM Studio geladen. Führe aus: lms load qwen3-14b. "
                    "Original: " + msg_txt[:200]
                )
            emit({"type": "error", "message": msg_txt})
            return {"error": msg_txt, "trace_log": log_path, "rb_id": rb.id}

        msg = resp.choices[0].message
        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            safe_calls = []
            for tc in msg.tool_calls:
                raw_args = tc.function.arguments or "{}"
                try:
                    json.loads(raw_args)
                    safe_args = raw_args
                except Exception:
                    safe_args = "{}"
                safe_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": safe_args},
                    }
                )
            assistant_msg["tool_calls"] = safe_calls
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

                if isinstance(result, list):
                    from url_utils import canonicalize_url
                    visited_set = {canonicalize_url(u) for u in rb.visited_sources}
                    claimed = []
                    for it in result:
                        if isinstance(it, dict):
                            src = it.get("source_url") or it.get("source") or ""
                            if src and canonicalize_url(src) not in visited_set:
                                claimed.append(src)
                    auto_fetched = 0
                    for u in claimed[:5]:
                        try:
                            _ = fetch_url(u, max_chars=4000)
                            rb.mark_visited(u)
                            rb.add_sources([u])
                            auto_fetched += 1
                        except Exception:
                            pass
                    if auto_fetched:
                        if verbose:
                            print(f"[auto-fetch] {auto_fetched} source_url(s) nachgeholt")
                        emit({"type": "auto_fetch", "count": auto_fetched})

                dedup_stats = None
                if extend and isinstance(rb.extracted_data, list) and isinstance(result, list):
                    merged = merge_items(rb.extracted_data, result, key_fields=("name",))
                    result = merged["merged"]
                    dedup_stats = {
                        "added": merged["added"],
                        "updated": merged["updated"],
                        "skipped": merged["skipped"],
                    }

                rb.extracted_data = result
                rb.entities = entities
                rb.status = "completed"
                rb.validation = validation.compute(result, rb.visited_sources)
                if dedup_stats:
                    rb.validation["dedup"] = dedup_stats
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
    rb.validation["mode"] = "validate"
    rb.append_validation_snapshot()
    rb.save()
    return rb.validation


def extend_rb(rb_id: str, rounds: int = 1, on_event=None, cancel_event=None, **kwargs) -> dict:
    rb = rb_store.load(rb_id)
    if rb is None:
        return {"error": "rb not found"}
    rounds = max(1, min(int(rounds or 1), 5))
    last = None
    for i in range(1, rounds + 1):
        if cancel_event is not None and cancel_event.is_set():
            break
        if on_event:
            try:
                on_event({"type": "extend_round", "current": i, "total": rounds})
            except Exception:
                pass
        last = run(
            rb.task,
            rb_id=rb_id,
            extend=True,
            on_event=on_event,
            cancel_event=cancel_event,
            **kwargs,
        )
        if isinstance(last, dict) and last.get("error"):
            break
    return last or {"error": "no extend rounds ran"}


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
        if "domain_trust" in chosen:
            method_results["domain_trust"] = validators.method_domain_trust(it)
        if "field_completeness" in chosen:
            method_results["field_completeness"] = validators.method_field_completeness(it)

        llm_jobs: dict[str, callable] = {}
        if "llm_semantic" in chosen:
            if not page_text and src:
                if src not in page_cache:
                    page_cache[src] = fetch_url(src, max_chars=20000)
                page_text = page_cache[src]
            llm_jobs["llm_semantic"] = lambda p=page_text: validators.method_llm_semantic(it, p, chat)
        if "consistency" in chosen:
            llm_jobs["consistency"] = lambda: validators.method_consistency(it, chat)
        if "relationship_validation" in chosen:
            llm_jobs["relationship_validation"] = lambda: validators.method_relationship_validation(it, chat)
        if "cross_source" in chosen:
            llm_jobs["cross_source"] = lambda: validators.method_cross_source(it, web_search)

        if llm_jobs:
            with ThreadPoolExecutor(max_workers=min(len(llm_jobs), 4)) as ex:
                futures = {name: ex.submit(fn) for name, fn in llm_jobs.items()}
                for name, fut in futures.items():
                    try:
                        method_results[name] = fut.result(timeout=120)
                    except Exception as e:
                        method_results[name] = {"supported": False, "reason": f"parallel error: {type(e).__name__}: {str(e)[:100]}"}

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
    rb.append_validation_snapshot()
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
    rb.append_validation_snapshot()
    rb.save()
    emit({"type": "verify_done", "validation": rb.validation})
    return rb.validation
