from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import time as _time

import pandas as pd
import streamlit as st

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import research_box as rb_store
from agent import run as agent_run
from agent import validate_rb, verify_rb
from config import API_PORT, RESULTS_DIR

st.set_page_config(page_title="AI Research Agent", page_icon="🔎", layout="wide")

_MODELS = ["qwen3-14b", "qwen3-32b"]

st.session_state.setdefault("last_result", None)
st.session_state.setdefault("last_events", [])
st.session_state.setdefault("running", False)
st.session_state.setdefault("cancel_event", None)
st.session_state.setdefault("agent_queue", None)
st.session_state.setdefault("agent_thread", None)
st.session_state.setdefault("task_input", "")
st.session_state.setdefault("current_rb_id", None)
st.session_state.setdefault("pending_run", None)


def _to_dataframe(data):
    if isinstance(data, list) and data and isinstance(data[0], dict):
        try:
            return pd.DataFrame(data)
        except Exception:
            return None
    return None


def _downloads(data, key_prefix: str) -> None:
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "⬇️ JSON",
        data=json.dumps(data, ensure_ascii=False, indent=2),
        file_name="agent_result.json",
        mime="application/json",
        key=f"{key_prefix}_json",
        use_container_width=True,
    )
    df = _to_dataframe(data)
    if df is not None:
        c2.download_button(
            "⬇️ CSV",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name="agent_result.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
            use_container_width=True,
        )
        buf = io.BytesIO()
        try:
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="result")
            c3.download_button(
                "⬇️ Excel",
                data=buf.getvalue(),
                file_name="agent_result.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{key_prefix}_xlsx",
                use_container_width=True,
            )
        except ModuleNotFoundError:
            c3.caption("Excel: `pip install openpyxl`")


def _confidence_badge(validation: dict) -> None:
    if not validation:
        return
    conf = validation.get("confidence", 0)
    label = validation.get("label", "?")
    total = validation.get("total", 0)
    supported = validation.get("supported", 0)
    n_src = validation.get("n_sources", 0)
    color = {"high": "🟢", "medium": "🟡", "low": "🟠", "unverified": "🔴"}.get(label, "⚪")
    st.markdown(
        f"**{color} Confidence: {conf}% ({label})** · "
        f"{supported}/{total} Items mit Quell-URL · {n_src} besuchte Quellen"
    )


def _trigger_run(task: str, rb_id: str | None = None, extend: bool = False) -> None:
    st.session_state["pending_run"] = {"task": task, "rb_id": rb_id, "extend": extend}
    st.session_state["task_input_staging"] = task


with st.sidebar:
    st.title("🔎 AI Research Agent")
    st.caption("Research Box System · LM Studio")
    model = st.selectbox("Modell", _MODELS, index=0)
    os.environ["MODEL_NAME"] = model

    st.divider()
    st.subheader("Beispiel-Tasks")
    examples = [
        "Finde 5 bekannte deutsche KI-Startups (name, website, description, source_url).",
        "Recherchiere die 10 größten Wohnungsbaugesellschaften in Berlin als JSON.",
        "Finde 5 Berliner Immobilien-Dienstleister mit E-Mail und Telefon. Nutze extract_contacts.",
        "Top 10 Proptech-Startups DACH (name, funding, website, source_url).",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True, key=f"ex_{hash(ex)}"):
            _trigger_run(ex)
            st.rerun()

    st.divider()
    st.caption(f"📡 API: http://localhost:{API_PORT}")
    st.caption("Start: `python api.py`")


tab_new, tab_history = st.tabs(["🆕 Neuer Task", "📚 Research Boxes"])


with tab_new:
    st.title("AI Research Agent")
    st.caption("Research Box System — Agent recherchiert autonom, validiert Quellen, speichert in zentraler DB.")

    if "task_input_staging" in st.session_state:
        st.session_state["task_input"] = st.session_state.pop("task_input_staging")

    task = st.text_area(
        "Dein Task",
        key="task_input",
        placeholder="z.B. Finde 5 deutsche KI-Startups mit name, website, description, source_url.",
        height=100,
        disabled=st.session_state["running"],
    )

    col_start, col_stop, col_clear = st.columns([2, 2, 1])
    start = col_start.button(
        "🚀 Starten",
        type="primary",
        use_container_width=True,
        disabled=st.session_state["running"] or not task.strip(),
    )
    stop = col_stop.button(
        "⏹️ Stop", use_container_width=True, disabled=not st.session_state["running"]
    )
    clear = col_clear.button("🧹", use_container_width=True, help="Clear result")

    if clear and not st.session_state["running"]:
        st.session_state["last_result"] = None
        st.session_state["last_events"] = []
        st.session_state["current_rb_id"] = None
        st.rerun()

    if stop and st.session_state["cancel_event"] is not None:
        st.session_state["cancel_event"].set()
        st.toast("⏹️ Stop-Signal gesendet", icon="⏹️")

    def _run_thread(t: str, q: queue.Queue, cancel: threading.Event, rb_id: str | None, extend: bool) -> None:
        def on_event(ev: dict) -> None:
            q.put(("event", ev))
        try:
            result = agent_run(
                t, verbose=False, on_event=on_event, cancel_event=cancel,
                rb_id=rb_id, extend=extend,
            )
            q.put(("done", result))
        except Exception as e:
            q.put(("error", str(e)))

    pending = st.session_state.get("pending_run")
    if start and task.strip() and not st.session_state["running"]:
        pending = {"task": task, "rb_id": None, "extend": False}
    if pending and not st.session_state["running"]:
        st.session_state["pending_run"] = None
        st.session_state["last_events"] = []
        st.session_state["last_result"] = None
        cancel_event = threading.Event()
        q: queue.Queue = queue.Queue()
        t = threading.Thread(
            target=_run_thread,
            args=(pending["task"], q, cancel_event, pending.get("rb_id"), pending.get("extend", False)),
            daemon=True,
        )
        t.start()
        st.session_state["cancel_event"] = cancel_event
        st.session_state["agent_queue"] = q
        st.session_state["agent_thread"] = t
        st.session_state["running"] = True
        st.rerun()

    if st.session_state["running"]:
        status = st.status(f"Agent arbeitet mit **{model}** ...", expanded=True)
        container = status.container()

        for ev in st.session_state["last_events"]:
            etype = ev.get("type")
            if etype == "rb":
                container.info(f"📦 Research Box: `{ev['id']}` {'(erweitert)' if ev.get('reused') else '(neu)'}")
            elif etype == "plan":
                container.markdown(f"**📋 Plan**\n\n{ev['text']}")
            elif etype == "tool_call":
                args_short = json.dumps(ev.get("args") or {}, ensure_ascii=False)
                if len(args_short) > 200:
                    args_short = args_short[:200] + "…"
                container.markdown(f"**Step {ev['step']}** · `{ev['name']}`  \n`{args_short}`")
            elif etype == "observation":
                p = ev.get("preview", "")
                if p:
                    container.caption(f"↳ {p[:160]}")
            elif etype == "finish":
                container.success("✅ finish()")
            elif etype == "cancelled":
                container.warning("⏹️ Abgebrochen")
            elif etype == "error":
                container.error(ev.get("message", "error"))

        q = st.session_state["agent_queue"]
        finished = False
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "event":
                    ev = payload
                    st.session_state["last_events"].append(ev)
                    etype = ev.get("type")
                    if etype == "rb":
                        container.info(f"📦 Research Box: `{ev['id']}` {'(erweitert)' if ev.get('reused') else '(neu)'}")
                        st.session_state["current_rb_id"] = ev["id"]
                    elif etype == "plan":
                        container.markdown(f"**📋 Plan**\n\n{ev['text']}")
                    elif etype == "tool_call":
                        args_short = json.dumps(ev.get("args") or {}, ensure_ascii=False)
                        if len(args_short) > 200:
                            args_short = args_short[:200] + "…"
                        container.markdown(f"**Step {ev['step']}** · `{ev['name']}`  \n`{args_short}`")
                    elif etype == "observation":
                        p = ev.get("preview", "")
                        if p:
                            container.caption(f"↳ {p[:160]}")
                    elif etype == "finish":
                        container.success("✅ finish()")
                    elif etype == "cancelled":
                        container.warning("⏹️ Abgebrochen")
                    elif etype == "error":
                        container.error(ev.get("message", "error"))
                elif kind == "done":
                    st.session_state["last_result"] = payload
                    finished = True
                    break
                elif kind == "error":
                    st.session_state["last_result"] = {"error": payload}
                    finished = True
                    break
        except queue.Empty:
            pass

        if not finished and st.session_state["agent_thread"] is not None:
            if not st.session_state["agent_thread"].is_alive():
                finished = True

        if finished:
            st.session_state["running"] = False
            res = st.session_state.get("last_result") or {}
            if "error" in res:
                status.update(label=f"❌ {res['error'][:80]}", state="error", expanded=True)
            else:
                status.update(label="✅ Fertig", state="complete", expanded=False)
            st.rerun()
        else:
            _time.sleep(0.5)
            st.rerun()

    if not st.session_state["running"] and st.session_state["last_result"]:
        result = st.session_state["last_result"]
        st.divider()

        if "error" in result:
            st.error(f"Fehler: {result['error']}")
            if result.get("rb_id"):
                st.caption(f"Research Box: `{result['rb_id']}`")
        else:
            rb_id = result.get("rb_id")
            if rb_id:
                st.markdown(f"**📦 Research Box:** `{rb_id}`")
            _confidence_badge(result.get("validation"))

            st.subheader("📦 Ergebnis")
            data = result.get("result")
            df = _to_dataframe(data)
            if df is not None:
                st.dataframe(df, use_container_width=True)
                with st.expander("JSON"):
                    st.json(data)
            else:
                st.json(data)
            _downloads(data, "new")

            st.divider()
            st.subheader("🎛️ Commands")
            bc1, bc2, bc3, bc4 = st.columns(4)
            if bc1.button("➕ Nur neue Quellen", use_container_width=True, key="cmd_extend",
                          help="Sucht weiter, blendet bereits besuchte URLs strikt aus"):
                rb = rb_store.load(rb_id)
                if rb:
                    _trigger_run(rb.task, rb_id=rb.id, extend=True)
                    st.rerun()
            if bc2.button("🔍 Verifizieren", use_container_width=True, key="cmd_verify",
                          help="Lädt jede Quell-URL neu & prüft ob die Fakten in der Seite stehen"):
                with st.spinner("Verifiziere Quellen..."):
                    v = verify_rb(rb_id)
                st.session_state["last_result"]["validation"] = v
                st.toast(f"Verify: {v.get('confidence')}% ({v.get('label')})", icon="🔍")
                st.rerun()
            if bc3.button("✅ Re-Validate", use_container_width=True, key="cmd_validate",
                          help="Schnelle Neuberechnung ohne Neu-Fetch"):
                v = validate_rb(rb_id)
                st.session_state["last_result"]["validation"] = v
                st.toast("Validation neu berechnet", icon="✅")
                st.rerun()
            bc4.caption("📤 Export-Buttons oben")

        with st.expander("Validation-Report"):
            st.json(result.get("validation") or {})
        with st.expander("Besuchte Quellen (visited_sources)"):
            for u in result.get("visited_sources", []) or []:
                st.markdown(f"- {u}")
        with st.expander("Alle gefundenen Quellen (sources)"):
            for u in result.get("sources_seen", []) or []:
                st.markdown(f"- {u}")
        with st.expander("Trace-Log"):
            st.caption(result.get("trace_log", ""))


with tab_history:
    st.title("📚 Research Boxes")
    st.caption("Zentrale Datenbank aller Research Boxes (SQLite)")

    boxes = rb_store.list_all(limit=100)
    if not boxes:
        st.info("Noch keine Research Boxes.")
    else:
        for rb in boxes:
            conf = (rb.validation or {}).get("confidence", "—")
            label = (rb.validation or {}).get("label", "")
            status_icon = {"completed": "✅", "running": "⏳", "cancelled": "⏹️", "error": "❌", "max_iterations": "⚠️"}.get(rb.status, "•")
            header = f"{status_icon} `{rb.id}`  ·  conf {conf}% {label}  ·  iter {rb.iterations}  ·  {rb.updated_at}  ·  {rb.task[:80]}"
            with st.expander(header):
                st.write("**Task:**", rb.task)
                if rb.validation:
                    _confidence_badge(rb.validation)
                data = rb.extracted_data
                df = _to_dataframe(data)
                if df is not None:
                    st.dataframe(df, use_container_width=True)
                else:
                    st.json(data)
                _downloads(data, f"hist_{rb.id}")

                c1, c2, c3, c4 = st.columns(4)
                if c1.button("➕ Neue Quellen", key=f"ext_{rb.id}", use_container_width=True):
                    _trigger_run(rb.task, rb_id=rb.id, extend=True)
                    st.rerun()
                if c2.button("🔍 Verify", key=f"ver_{rb.id}", use_container_width=True):
                    with st.spinner("Verifiziere..."):
                        verify_rb(rb.id)
                    st.toast("Quellen verifiziert", icon="🔍")
                    st.rerun()
                if c3.button("✅ Re-Validate", key=f"val_{rb.id}", use_container_width=True):
                    validate_rb(rb.id)
                    st.toast("Validation aktualisiert", icon="✅")
                    st.rerun()
                if c4.button("🗑️ Löschen", key=f"del_{rb.id}", use_container_width=True):
                    rb_store.delete(rb.id)
                    st.rerun()

                if rb.entities:
                    with st.expander("Extrahierte Entitäten"):
                        st.json(rb.entities)
                with st.expander(f"Visited Sources ({len(rb.visited_sources)})"):
                    for u in rb.visited_sources:
                        st.markdown(f"- {u}")

    st.divider()
    st.caption(f"📁 Ergebnis-Dateien in {RESULTS_DIR}")
    if os.path.isdir(RESULTS_DIR):
        for f in sorted(os.listdir(RESULTS_DIR), reverse=True)[:20]:
            st.caption(f"📄 {f}")
