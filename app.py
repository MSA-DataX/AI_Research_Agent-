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
from agent import analyze_rows_rb, extend_rb, run as agent_run
from agent import validate_rb, verify_rb
from cleanup import auto_cleanup
from config import API_PORT, RESULTS_DIR
from validation import compute as validation_compute
from validators import METHODS as VALIDATION_METHODS

_AUTO_CLEANUP_RESULT = auto_cleanup()

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
st.session_state.setdefault("opened_rb", None)


def _to_dataframe(data):
    if isinstance(data, list) and data and isinstance(data[0], dict):
        try:
            return pd.DataFrame(data)
        except Exception:
            return None
    return None


def _records_from_editor(edited_df) -> list[dict]:
    """Convert edited DataFrame to clean JSON-serializable records.

    Handles NaN/NaT/numpy types that otherwise break json.dumps and validation.
    """
    if edited_df is None or edited_df.empty:
        return []
    import numpy as np
    cleaned = edited_df.where(pd.notna(edited_df), None)
    records = cleaned.to_dict("records")
    out: list[dict] = []
    for r in records:
        row = {}
        for k, v in r.items():
            if v is None:
                row[k] = None
            elif isinstance(v, float) and (v != v):
                row[k] = None
            elif isinstance(v, (np.integer,)):
                row[k] = int(v)
            elif isinstance(v, (np.floating,)):
                row[k] = None if np.isnan(v) else float(v)
            elif isinstance(v, np.bool_):
                row[k] = bool(v)
            else:
                row[k] = v
        out.append(row)
    return out


def _verdict_icon(label: str) -> str:
    return {"high": "🟢", "medium": "🟡", "low": "🟠", "unverified": "🔴"}.get(label, "⚪")


def _enrich_df_with_validation(df, validation: dict):
    """Prepend verdict/confidence columns if validation has per-row data.

    Returns (enriched_df, method_columns_added).
    """
    if df is None or not validation:
        return df, []
    per_row = validation.get("per_row")
    per_item = validation.get("per_item")

    if per_row and len(per_row) == len(df):
        df = df.copy()
        df.insert(0, "Conf", [f"{r.get('confidence', 0)}%" for r in per_row])
        df.insert(0, "✓", [_verdict_icon(r.get("verdict", "")) for r in per_row])
        methods = validation.get("methods_used") or []
        for m in methods:
            df[m] = [
                "✅" if r["methods"].get(m, {}).get("supported") else "❌"
                for r in per_row
            ]
        return df, methods

    if per_item and len(per_item) == len(df):
        df = df.copy()
        df.insert(0, "✓", ["🟢" if it.get("supported") else "🔴" for it in per_item])
        df.insert(1, "check", [it.get("reason", "")[:40] for it in per_item])
        return df, []

    return df, []


def _downloads(data, key_prefix: str) -> None:
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "⬇️ JSON",
        data=json.dumps(data, ensure_ascii=False, indent=2),
        file_name="agent_result.json",
        mime="application/json",
        key=f"{key_prefix}_json",
        width="stretch",
    )
    df = _to_dataframe(data)
    if df is not None:
        c2.download_button(
            "⬇️ CSV",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name="agent_result.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
            width="stretch",
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
                width="stretch",
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


def _trigger_run(task: str, rb_id: str | None = None, extend: bool = False, rounds: int = 1) -> None:
    st.session_state["pending_run"] = {"task": task, "rb_id": rb_id, "extend": extend, "rounds": int(rounds)}
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
        if st.button(ex, width="stretch", key=f"ex_{hash(ex)}"):
            _trigger_run(ex)
            st.rerun()

    st.divider()
    st.caption(f"📡 API: http://localhost:{API_PORT}")
    st.caption("Start: `python api.py`")

    if st.session_state.get("running"):
        alive = False
        t = st.session_state.get("agent_thread")
        if t is not None:
            try:
                alive = t.is_alive()
            except Exception:
                alive = False
        if not alive:
            st.warning("⚠️ Zombie-Status erkannt (Thread tot, UI noch 'running')")
            if st.button("🔧 Reset", width="stretch", key="zombie_reset"):
                for k in ("running", "cancel_event", "agent_queue", "agent_thread",
                         "pending_run", "last_events"):
                    st.session_state[k] = None if k != "last_events" else []
                st.session_state["running"] = False
                st.rerun()

    st.divider()
    with st.expander("🧹 Aufräumen"):
        import cleanup as _cleanup
        if _AUTO_CLEANUP_RESULT and not _AUTO_CLEANUP_RESULT.get("error"):
            r = _AUTO_CLEANUP_RESULT
            st.success(
                f"Auto-Cleanup lief: {r['logs_removed'] + r['results_removed']} Dateien, "
                f"{r['rbs_removed']} RBs entfernt"
            )
        s = _cleanup.stats()

        def _fmt(b: int) -> str:
            for unit in ("B", "KB", "MB", "GB"):
                if b < 1024:
                    return f"{b:.1f} {unit}"
                b /= 1024
            return f"{b:.1f} TB"

        st.caption(f"📂 logs: {s['logs']['files']} · {_fmt(s['logs']['bytes'])}")
        st.caption(f"📂 results: {s['results']['files']} · {_fmt(s['results']['bytes'])}")
        st.caption(f"🗄️ db: {_fmt(s['database']['bytes'])} ({s['database']['rb_count']} RBs)")
        st.caption(f"**total:** {_fmt(s['total_bytes'])}")

        days = st.number_input("Älter als (Tage)", min_value=0, max_value=365, value=30, key="cleanup_days")
        dry = _cleanup.prune_logs(days, dry_run=True).count + _cleanup.prune_results(days, dry_run=True).count
        st.caption(f"→ {dry} Dateien wären betroffen")

        if st.button("🗑️ Jetzt löschen", width="stretch", disabled=dry == 0):
            r1 = _cleanup.prune_logs(days, dry_run=False)
            r2 = _cleanup.prune_results(days, dry_run=False)
            st.toast(f"Gelöscht: {r1.count + r2.count} Dateien · {_fmt(r1.freed_bytes + r2.freed_bytes)}", icon="🗑️")
            st.rerun()


tab_new, tab_history, tab_analyse = st.tabs(["🆕 Neuer Task", "📚 Research Boxes", "📊 Analyse"])


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
    fields_raw = st.text_input(
        "Gewünschte Felder pro Zeile (optional, kommagetrennt)",
        key="output_fields_input",
        placeholder="name, date, source_url",
        disabled=st.session_state["running"],
        help="Erzwingt ein exaktes Schema pro Item. Leer lassen = LLM entscheidet selbst.",
    )

    col_start, col_stop, col_clear = st.columns([2, 2, 1])
    start = col_start.button(
        "🚀 Starten",
        type="primary",
        width="stretch",
        disabled=st.session_state["running"] or not task.strip(),
    )
    stop = col_stop.button(
        "⏹️ Stop", width="stretch", disabled=not st.session_state["running"]
    )
    clear = col_clear.button("🧹", width="stretch", help="Clear result")

    if clear and not st.session_state["running"]:
        st.session_state["last_result"] = None
        st.session_state["last_events"] = []
        st.session_state["current_rb_id"] = None
        st.rerun()

    if stop and st.session_state["cancel_event"] is not None:
        st.session_state["cancel_event"].set()
        st.toast("⏹️ Stop-Signal gesendet", icon="⏹️")

    def _run_thread(
        t: str,
        q: queue.Queue,
        cancel: threading.Event,
        rb_id: str | None,
        extend: bool,
        output_fields: list[str] | None,
        rounds: int,
    ) -> None:
        import traceback as _tb
        def on_event(ev: dict) -> None:
            q.put(("event", ev))
        try:
            if extend and rb_id and rounds > 1:
                result = extend_rb(rb_id, rounds=rounds, on_event=on_event, cancel_event=cancel)
            else:
                result = agent_run(
                    t, verbose=False, on_event=on_event, cancel_event=cancel,
                    rb_id=rb_id, extend=extend, output_fields=output_fields,
                )
            q.put(("done", result))
        except Exception as e:
            q.put(("error", f"{type(e).__name__}: {e}\n\n{_tb.format_exc()[-1500:]}"))

    pending = st.session_state.get("pending_run")
    if start and task.strip() and not st.session_state["running"]:
        of = [f.strip() for f in fields_raw.split(",") if f.strip()] if fields_raw else None
        pending = {"task": task, "rb_id": None, "extend": False, "output_fields": of, "rounds": 1}
    if pending and not st.session_state["running"]:
        st.session_state["pending_run"] = None
        st.session_state["last_events"] = []
        st.session_state["last_result"] = None
        cancel_event = threading.Event()
        q: queue.Queue = queue.Queue()
        t = threading.Thread(
            target=_run_thread,
            args=(
                pending["task"],
                q,
                cancel_event,
                pending.get("rb_id"),
                pending.get("extend", False),
                pending.get("output_fields"),
                pending.get("rounds", 1),
            ),
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
            elif etype == "extend_round":
                container.info(f"🔁 Extend-Runde {ev.get('current')}/{ev.get('total')}")
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
                    elif etype == "extend_round":
                        container.info(f"🔁 Extend-Runde {ev.get('current')}/{ev.get('total')}")
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
            err = str(result["error"])
            st.error(f"❌ Fehler: {err.splitlines()[0][:200]}")
            if result.get("rb_id"):
                st.caption(f"Research Box: `{result['rb_id']}`")
            if "\n" in err or len(err) > 200:
                with st.expander("Technische Details"):
                    st.code(err)
            low = err.lower()
            if "connection" in low or "refused" in low or "reachable" in low:
                st.info("💡 Prüfe: LM Studio läuft? Modell geladen? `lms load qwen3-14b` → dann Task neu starten.")
            elif "invalid model" in low or "not loaded" in low:
                st.info("💡 Modell nicht geladen. Führe aus: `lms load qwen3-14b`")
        else:
            rb_id = result.get("rb_id")
            if rb_id:
                st.markdown(f"**📦 Research Box:** `{rb_id}`")
            _confidence_badge(result.get("validation"))

            st.subheader("📦 Ergebnis")
            data = result.get("result")
            df = _to_dataframe(data)
            v = result.get("validation") or {}
            _rb_id_for_edit = result.get("rb_id")
            if df is not None:
                enriched, methods = _enrich_df_with_validation(df, v)
                st.dataframe(enriched, width="stretch")
                if _rb_id_for_edit:
                    with st.expander(f"✏️ Bearbeiten ({len(df)} Zeilen)"):
                        st.caption("Zellen editieren · `+` neue Zeile · Häkchen+Entf löscht.")
                        edited_df_new = st.data_editor(
                            df, width="stretch", num_rows="dynamic",
                            key=f"editor_new_{_rb_id_for_edit}",
                        )
                        ec1, ec2 = st.columns([1, 3])
                        if ec1.button("💾 Speichern", key=f"save_new_{_rb_id_for_edit}", type="primary"):
                            try:
                                rb_edit = rb_store.load(_rb_id_for_edit)
                                if rb_edit is None:
                                    st.error("RB nicht gefunden.")
                                else:
                                    new_data = _records_from_editor(edited_df_new)
                                    rb_edit.extracted_data = new_data
                                    rb_edit.validation = validation_compute(new_data, rb_edit.visited_sources)
                                    rb_edit.validation["mode"] = "validate"
                                    rb_edit.validation["edited"] = True
                                    rb_edit.append_validation_snapshot()
                                    rb_edit.save()
                                    st.session_state["last_result"]["result"] = new_data
                                    st.session_state["last_result"]["validation"] = rb_edit.validation
                                    st.success(f"✅ {len(new_data)} Zeilen gespeichert")
                                    st.rerun()
                            except Exception as e:
                                import traceback as _tb
                                st.error(f"❌ Speichern fehlgeschlagen: {type(e).__name__}: {e}")
                                with st.expander("Technische Details"):
                                    st.code(_tb.format_exc())
                        ec2.caption("→ Für exakte Methoden-Confidence: 🔬 Deep-Analyse danach")
                if methods:
                    summary = v.get("methods_summary") or {}
                    legend = " · ".join(
                        f"`{m}` {summary.get(m, {}).get('ratio', '?')}%" for m in methods
                    )
                    st.caption(f"Methoden: {legend}")
                elif v.get("per_item"):
                    st.caption("🟢 = Quelle bestätigt Item · 🔴 = nicht bestätigt (Verify)")
                with st.expander("JSON"):
                    st.json(data)
            else:
                st.json(data)
            _downloads(data, "new")

            st.divider()
            st.subheader("🎛️ Commands")
            bc1, bc2, bc3, bc4 = st.columns(4)
            extend_rounds_new = bc1.number_input(
                "Runden", min_value=1, max_value=5, value=1,
                key="extend_rounds_new",
                label_visibility="collapsed",
                help="Wie viele Extend-Runden hintereinander ausführen",
            )
            if bc1.button("➕ Nur neue Quellen", width="stretch", key="cmd_extend",
                          help="Sucht weiter, blendet bereits besuchte URLs strikt aus"):
                rb = rb_store.load(rb_id)
                if rb:
                    _trigger_run(rb.task, rb_id=rb.id, extend=True, rounds=int(extend_rounds_new))
                    st.rerun()
            if bc2.button("🔍 Verifizieren", width="stretch", key="cmd_verify",
                          help="Lädt jede Quell-URL neu & prüft ob die Fakten in der Seite stehen"):
                with st.spinner("Verifiziere Quellen..."):
                    v = verify_rb(rb_id)
                st.session_state["last_result"]["validation"] = v
                st.toast(f"Verify: {v.get('confidence')}% ({v.get('label')})", icon="🔍")
                st.rerun()
            if bc3.button("🔬 Deep-Analyse (pro Zeile)", width="stretch", key="cmd_analyze",
                          help="Wendet alle Validierungs-Methoden pro Zeile an"):
                with st.spinner("Analysiere jede Zeile mit mehreren Methoden..."):
                    v = analyze_rows_rb(rb_id)
                st.session_state["last_result"]["validation"] = v
                st.toast(f"Deep: {v.get('confidence')}% ({v.get('label')})", icon="🔬")
                st.rerun()
            if bc4.button("✅ Re-Validate", width="stretch", key="cmd_validate",
                          help="Schnelle Neuberechnung ohne Neu-Fetch"):
                v = validate_rb(rb_id)
                st.session_state["last_result"]["validation"] = v
                st.toast("Validation neu berechnet", icon="✅")
                st.rerun()

        v = result.get("validation") or {}
        if v.get("mode") == "deep_analyze" and v.get("per_row"):
            st.divider()
            st.subheader("🔬 Pro-Zeile Analyse")
            methods_used = v.get("methods_used", [])
            rows = []
            for r in v["per_row"]:
                row = {
                    "#": r["row_index"],
                    "Name": r["name"][:60],
                    "Verdict": r["verdict"],
                    "Conf": f"{r['confidence']}%",
                }
                for m in methods_used:
                    mr = r["methods"].get(m, {})
                    row[m] = "✅" if mr.get("supported") else "❌"
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), width="stretch")

            st.markdown("**Methoden-Zusammenfassung:**")
            for m, s in (v.get("methods_summary") or {}).items():
                st.caption(f"• `{m}` → {s['supported']}/{s['total']} ({s['ratio']}%) — {VALIDATION_METHODS.get(m, '')}")

            with st.expander("Details pro Zeile (Raw)"):
                st.json(v["per_row"])
        else:
            with st.expander("Validation-Report"):
                st.json(v)
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

    all_boxes = rb_store.list_all(limit=1000)

    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    search_q = fc1.text_input("🔎 Suche im Task", key="rb_search", placeholder="z.B. 'berlin' oder 'startup'")
    status_options = sorted({rb.status for rb in all_boxes}) if all_boxes else []
    status_filter = fc2.multiselect("Status", options=status_options, default=[], key="rb_status_filter")
    min_conf = fc3.slider("Min. Confidence %", 0, 100, 0, key="rb_min_conf")
    sort_by = fc4.selectbox(
        "Sortierung",
        ["neueste", "älteste", "höchste Confidence", "niedrigste Confidence"],
        key="rb_sort",
    )

    def _conf_of(rb) -> int:
        c = (rb.validation or {}).get("confidence")
        return int(c) if isinstance(c, (int, float)) else -1

    filtered = all_boxes
    if search_q:
        q = search_q.lower().strip()
        filtered = [rb for rb in filtered if q in (rb.task or "").lower()]
    if status_filter:
        filtered = [rb for rb in filtered if rb.status in status_filter]
    if min_conf > 0:
        filtered = [rb for rb in filtered if _conf_of(rb) >= min_conf]

    if sort_by == "älteste":
        filtered.sort(key=lambda r: r.updated_at)
    elif sort_by == "höchste Confidence":
        filtered.sort(key=lambda r: -_conf_of(r))
    elif sort_by == "niedrigste Confidence":
        filtered.sort(key=lambda r: _conf_of(r) if _conf_of(r) >= 0 else 9999)

    if not filtered:
        if all_boxes:
            st.info(f"Keine RBs matchen die Filter ({len(all_boxes)} gesamt). Filter zurücksetzen?")
        else:
            st.info("Noch keine Research Boxes.")
        boxes = []
    else:
        if len(filtered) < len(all_boxes):
            st.caption(f"🔍 {len(filtered)} von {len(all_boxes)} RBs gefiltert")
        boxes = filtered

    if boxes:
        for rb in boxes:
            conf = (rb.validation or {}).get("confidence", "—")
            label = (rb.validation or {}).get("label", "")
            status_icon = {"completed": "✅", "running": "⏳", "cancelled": "⏹️", "error": "❌", "max_iterations": "⚠️", "verified": "🔬"}.get(rb.status, "•")
            header = f"{status_icon} `{rb.id}`  ·  conf {conf}% {label}  ·  iter {rb.iterations}  ·  {rb.updated_at}  ·  {rb.task[:80]}"
            is_open = st.session_state.get("opened_rb") == rb.id
            with st.expander(header, expanded=is_open):
                st.write("**Task:**", rb.task)
                if rb.validation:
                    _confidence_badge(rb.validation)
                data = rb.extracted_data
                df = _to_dataframe(data)
                if df is not None:
                    enriched, methods = _enrich_df_with_validation(df, rb.validation or {})
                    st.dataframe(enriched, width="stretch")
                    if methods:
                        summary = rb.validation.get("methods_summary") or {}
                        legend = " · ".join(
                            f"`{m}` {summary.get(m, {}).get('ratio', '?')}%" for m in methods
                        )
                        st.caption(f"Methoden: {legend}")
                    elif (rb.validation or {}).get("per_item"):
                        st.caption("🟢 = Quelle bestätigt Item · 🔴 = nicht bestätigt (Verify)")

                    with st.expander(f"✏️ Bearbeiten ({len(df)} Zeilen)"):
                        st.caption("Zellen editieren, Zeilen hinzufügen (`+`) oder löschen (Häkchen + Entf).")
                        edit_key = f"editor_{rb.id}"
                        edited_df = st.data_editor(
                            df,
                            width="stretch",
                            num_rows="dynamic",
                            key=edit_key,
                        )
                        ec1, ec2 = st.columns([1, 3])
                        if ec1.button("💾 Speichern", key=f"save_rows_{rb.id}", type="primary"):
                            try:
                                new_data = _records_from_editor(edited_df)
                                rb_fresh = rb_store.load(rb.id)
                                if rb_fresh is None:
                                    st.error("RB nicht gefunden (evtl. gelöscht).")
                                else:
                                    rb_fresh.extracted_data = new_data
                                    rb_fresh.validation = validation_compute(new_data, rb_fresh.visited_sources)
                                    rb_fresh.validation["mode"] = "validate"
                                    rb_fresh.validation["edited"] = True
                                    rb_fresh.append_validation_snapshot()
                                    rb_fresh.save()
                                    st.session_state["opened_rb"] = rb_fresh.id
                                    st.success(f"✅ {len(new_data)} Zeilen gespeichert · Confidence = {rb_fresh.validation.get('confidence', '—')}%")
                                    st.rerun()
                            except Exception as e:
                                import traceback as _tb
                                st.error(f"❌ Speichern fehlgeschlagen: {type(e).__name__}: {e}")
                                with st.expander("Technische Details"):
                                    st.code(_tb.format_exc())
                        ec2.caption("→ Validation wird nach Save neu berechnet (für exakte Confidence: 🔬 Deep nochmal laufen lassen)")
                else:
                    st.json(data)
                _downloads(data, f"hist_{rb.id}")

                c1, c2, c3, c4, c5 = st.columns(5)
                rounds_hist = c1.number_input(
                    "Runden", min_value=1, max_value=5, value=1,
                    key=f"rounds_{rb.id}",
                    label_visibility="collapsed",
                )
                if c1.button("➕ Neue Quellen", key=f"ext_{rb.id}", width="stretch"):
                    st.session_state["opened_rb"] = rb.id
                    _trigger_run(rb.task, rb_id=rb.id, extend=True, rounds=int(rounds_hist))
                    st.rerun()
                if c2.button("🔍 Verify", key=f"ver_{rb.id}", width="stretch"):
                    st.session_state["opened_rb"] = rb.id
                    with st.spinner("Verifiziere..."):
                        verify_rb(rb.id)
                    st.toast("Quellen verifiziert", icon="🔍")
                    st.rerun()
                if c3.button("🔬 Deep", key=f"deep_{rb.id}", width="stretch",
                             help="Deep-Analyse mit allen 4 Methoden pro Zeile"):
                    st.session_state["opened_rb"] = rb.id
                    with st.spinner("Deep-Analyse läuft (alle 4 Methoden)..."):
                        analyze_rows_rb(rb.id)
                    st.toast("Deep-Analyse fertig", icon="🔬")
                    st.rerun()
                if c4.button("✅ Re-Validate", key=f"val_{rb.id}", width="stretch"):
                    st.session_state["opened_rb"] = rb.id
                    validate_rb(rb.id)
                    st.toast("Validation aktualisiert", icon="✅")
                    st.rerun()
                if c5.button("🗑️ Löschen", key=f"del_{rb.id}", width="stretch"):
                    if st.session_state.get("opened_rb") == rb.id:
                        st.session_state["opened_rb"] = None
                    rb_store.delete(rb.id)
                    st.rerun()

                v = rb.validation or {}
                per_row = v.get("per_row") or []
                if per_row:
                    with st.expander(f"🔬 Pro-Zeile-Details ({len(per_row)} Zeilen)"):
                        for r in per_row:
                            c_icon = _verdict_icon(r.get("verdict", ""))
                            st.markdown(f"**{c_icon} `{r.get('name','?')}` · Conf {r.get('confidence',0)}%**")
                            for m, mr in (r.get("methods") or {}).items():
                                tick = "✅" if mr.get("supported") else "❌"
                                reason = mr.get("reason", "")
                                extras = []
                                for k in ("confidence", "n_domains", "score", "fields_checked", "fields_missing", "filled", "total"):
                                    if k in mr:
                                        extras.append(f"{k}={mr[k]}")
                                extras_str = f"  ·  {' · '.join(extras)}" if extras else ""
                                st.caption(f"{tick} `{m}` — {reason}{extras_str}")
                            st.markdown("---")

                hist = rb.validation_history or []
                if hist:
                    with st.expander(f"📜 Validation-Verlauf ({len(hist)} Einträge)"):
                        hist_rows = [
                            {
                                "ts": h.get("ts", "")[-19:-3],
                                "mode": h.get("mode", "?"),
                                "conf%": h.get("confidence", "-"),
                                "label": h.get("label", ""),
                                "total": h.get("total", ""),
                                "supported": h.get("supported", ""),
                                "methods": ", ".join(h.get("methods_used") or []),
                            }
                            for h in reversed(hist)
                        ]
                        st.dataframe(pd.DataFrame(hist_rows), width="stretch", hide_index=True)

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


def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


with tab_analyse:
    import time as _t
    import cleanup as _cleanup
    from collections import Counter
    from config import AUTO_CLEANUP_MARKER, AUTO_CLEANUP_INTERVAL_HOURS

    st.title("📊 System-Analyse")
    st.caption("Live-Übersicht: Cleanup, Research Boxes, Quellen, Confidence")

    st.subheader("🧹 Auto-Cleanup")
    col_a, col_b, col_c = st.columns(3)
    if os.path.exists(AUTO_CLEANUP_MARKER):
        last = _t.localtime(os.path.getmtime(AUTO_CLEANUP_MARKER))
        last_str = _t.strftime("%Y-%m-%d %H:%M:%S", last)
        age_h = (_t.time() - os.path.getmtime(AUTO_CLEANUP_MARKER)) / 3600
        col_a.metric("Letzter Lauf", last_str)
        col_b.metric("Vor (Stunden)", f"{age_h:.1f}")
    else:
        col_a.metric("Letzter Lauf", "—")
        col_b.metric("Vor (Stunden)", "—")
    col_c.metric("Intervall", f"{AUTO_CLEANUP_INTERVAL_HOURS}h")

    if _AUTO_CLEANUP_RESULT and not _AUTO_CLEANUP_RESULT.get("error"):
        r = _AUTO_CLEANUP_RESULT
        st.success(
            f"Letzter Lauf dieser Session: "
            f"{r['logs_removed']} logs · {r['results_removed']} results · {r['rbs_removed']} RBs · "
            f"{_fmt_bytes(r['bytes_freed'])} freigegeben"
        )
    else:
        st.caption("(Kein Cleanup in dieser Session nötig — wurde bereits durchgeführt oder deaktiviert)")

    if st.button("🗑️ Cleanup jetzt erzwingen (force)", width="content"):
        r = _cleanup.auto_cleanup(force=True)
        if r and not r.get("error"):
            st.success(
                f"Entfernt: {r['logs_removed']} logs, {r['results_removed']} results, "
                f"{r['rbs_removed']} RBs ({_fmt_bytes(r['bytes_freed'])})"
            )
        elif r and r.get("error"):
            st.error(f"Fehler: {r['error']}")
        else:
            st.info("Cleanup ist deaktiviert (AUTO_CLEANUP=0)")
        st.rerun()

    st.divider()
    st.subheader("🗄️ Speicherplatz")
    s = _cleanup.stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Logs", f"{s['logs']['files']}", _fmt_bytes(s['logs']['bytes']))
    c2.metric("Results", f"{s['results']['files']}", _fmt_bytes(s['results']['bytes']))
    c3.metric("Database", "—", _fmt_bytes(s['database']['bytes']))
    c4.metric("Total", "—", _fmt_bytes(s['total_bytes']))

    st.divider()
    st.subheader("📦 Research Boxes")
    boxes = rb_store.list_all(limit=10000)
    if not boxes:
        st.info("Noch keine Research Boxes.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Gesamt", len(boxes))
        completed = [b for b in boxes if b.status == "completed" or b.status == "verified"]
        col2.metric("Completed", len(completed))
        errored = [b for b in boxes if b.status in ("error", "cancelled", "max_iterations")]
        col3.metric("Failed", len(errored))
        confs = [
            (b.validation or {}).get("confidence")
            for b in boxes
            if isinstance((b.validation or {}).get("confidence"), int)
        ]
        avg_conf = round(sum(confs) / len(confs), 1) if confs else 0
        col4.metric("Ø Confidence", f"{avg_conf}%")

        st.markdown("**Status-Verteilung**")
        status_counts = Counter(b.status for b in boxes)
        status_df = pd.DataFrame(
            [{"status": k, "count": v} for k, v in status_counts.most_common()]
        )
        st.dataframe(status_df, width="stretch", hide_index=True)

        st.markdown("**Top besuchte Domains**")
        from urllib.parse import urlparse
        domain_counts: Counter = Counter()
        for b in boxes:
            for u in (b.visited_sources or []):
                try:
                    host = urlparse(u).netloc.lower().lstrip("www.")
                    if host:
                        domain_counts[host] += 1
                except Exception:
                    pass
        top = domain_counts.most_common(10)
        if top:
            dom_df = pd.DataFrame(top, columns=["Domain", "Besuche"])
            st.dataframe(dom_df, width="stretch", hide_index=True)
            st.bar_chart(dom_df.set_index("Domain"))
        else:
            st.caption("Noch keine besuchten Quellen.")

        st.markdown("**Iterations-Verteilung** (wie oft wurden RBs erweitert?)")
        iter_counts = Counter(b.iterations for b in boxes)
        iter_df = pd.DataFrame(
            [{"iterations": k, "count": v} for k, v in sorted(iter_counts.items())]
        )
        st.dataframe(iter_df, width="stretch", hide_index=True)

    st.divider()
    st.subheader("📋 Letzte 5 Runs")
    for b in boxes[:5]:
        conf = (b.validation or {}).get("confidence", "—")
        st.caption(
            f"• `{b.id}` · {b.status} · conf {conf}% · {b.updated_at} · {b.task[:80]}"
        )
