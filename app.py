from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading

import pandas as pd
import streamlit as st

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import run as agent_run
from config import MEMORY_PATH, RESULTS_DIR

st.set_page_config(page_title="AI Research Agent", page_icon="🔎", layout="wide")

_MODELS = ["qwen3-14b", "qwen3-32b"]

st.session_state.setdefault("last_result", None)
st.session_state.setdefault("last_events", [])
st.session_state.setdefault("running", False)
st.session_state.setdefault("cancel_event", None)
st.session_state.setdefault("agent_queue", None)
st.session_state.setdefault("agent_thread", None)
st.session_state.setdefault("task_input", "")


def _load_memory() -> list[dict]:
    if not os.path.exists(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _to_dataframe(data):
    if isinstance(data, list) and data and isinstance(data[0], dict):
        try:
            return pd.DataFrame(data)
        except Exception:
            return None
    return None


def _result_downloads(data, key_prefix: str) -> None:
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
            c3.caption("Excel braucht `pip install openpyxl`")


with st.sidebar:
    st.title("🔎 AI Research Agent")
    st.caption("Lokal via LM Studio")
    model = st.selectbox("Modell", _MODELS, index=0)
    os.environ["MODEL_NAME"] = model

    st.divider()
    st.subheader("Beispiel-Tasks")
    examples = [
        "Finde 5 bekannte deutsche KI-Startups (name, website, description, source_url).",
        "Recherchiere die 10 größten Wohnungsbaugesellschaften in Berlin als JSON.",
        "Finde 5 Berliner Immobilien-Dienstleister mit E-Mail und Telefon. Nutze extract_contacts.",
        "Top 10 Proptech-Startups DACH mit name, funding, website, source_url.",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True, key=f"ex_{hash(ex)}"):
            st.session_state["task_input"] = ex
            st.rerun()


tab_new, tab_history = st.tabs(["🆕 Neuer Task", "📚 Verlauf"])


with tab_new:
    st.title("AI Research Agent")
    st.caption("Gib einen Task ein — der Agent recherchiert autonom, validiert Quellen und liefert strukturiertes JSON.")

    task = st.text_area(
        "Dein Task",
        key="task_input",
        placeholder="z.B. Finde 5 deutsche KI-Startups mit name, website, description, source_url.",
        height=100,
        disabled=st.session_state["running"],
    )

    col_start, col_stop, col_clear = st.columns([2, 2, 1])
    start_disabled = st.session_state["running"] or not task.strip()
    start = col_start.button(
        "🚀 Starten", type="primary", use_container_width=True, disabled=start_disabled
    )
    stop = col_stop.button(
        "⏹️ Stop", use_container_width=True, disabled=not st.session_state["running"]
    )
    clear = col_clear.button("🧹", use_container_width=True, help="Clear result")

    if clear and not st.session_state["running"]:
        st.session_state["last_result"] = None
        st.session_state["last_events"] = []
        st.rerun()

    if stop and st.session_state["cancel_event"] is not None:
        st.session_state["cancel_event"].set()
        st.toast("⏹️ Stop-Signal gesendet", icon="⏹️")

    def _run_agent_thread(t: str, q: queue.Queue, cancel: threading.Event) -> None:
        def on_event(ev: dict) -> None:
            q.put(("event", ev))
        try:
            result = agent_run(t, verbose=False, on_event=on_event, cancel_event=cancel)
            q.put(("done", result))
        except Exception as e:
            q.put(("error", str(e)))

    if start and task.strip() and not st.session_state["running"]:
        st.session_state["last_events"] = []
        st.session_state["last_result"] = None
        cancel_event = threading.Event()
        q: queue.Queue = queue.Queue()
        t = threading.Thread(target=_run_agent_thread, args=(task, q, cancel_event), daemon=True)
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
            if etype == "plan":
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
                    if etype == "plan":
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
            import time as _t
            _t.sleep(0.5)
            st.rerun()

    if not st.session_state["running"] and st.session_state["last_result"]:
        result = st.session_state["last_result"]
        st.divider()

        if "error" in result:
            st.error(f"Fehler: {result['error']}")
        else:
            st.subheader("📦 Ergebnis")
            data = result.get("result")
            df = _to_dataframe(data)
            if df is not None:
                st.dataframe(df, use_container_width=True)
                with st.expander("JSON"):
                    st.json(data)
            else:
                st.json(data)
            _result_downloads(data, "new")

        with st.expander("Gesehene Quellen"):
            for u in result.get("sources_seen", []) or []:
                st.markdown(f"- {u}")
        with st.expander("Trace-Log"):
            st.caption(result.get("trace_log", ""))


with tab_history:
    st.title("📚 Verlauf")
    st.caption("Alle früheren Tasks (aus memory.json)")

    items = _load_memory()
    if not items:
        st.info("Noch keine abgeschlossenen Tasks.")
    else:
        for i, it in enumerate(reversed(items)):
            label = f"🕒 {it.get('ts','?')}  ·  {it.get('task','')[:120]}"
            with st.expander(label):
                st.write("**Task:**", it.get("task", ""))
                data = it.get("result")
                df = _to_dataframe(data)
                if df is not None:
                    st.dataframe(df, use_container_width=True)
                else:
                    st.json(data)
                _result_downloads(data, f"hist_{i}")
                srcs = it.get("sources") or []
                if srcs:
                    with st.expander("Quellen"):
                        for u in srcs:
                            st.markdown(f"- {u}")

    st.divider()
    st.caption(f"📁 Ergebnis-Dateien in {RESULTS_DIR}")
    if os.path.isdir(RESULTS_DIR):
        for f in sorted(os.listdir(RESULTS_DIR), reverse=True)[:20]:
            st.caption(f"📄 {f}")
