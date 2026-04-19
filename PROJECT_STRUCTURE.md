# AI Research Agent — Projektstruktur

Lokaler, autonomer Research-Agent mit Streamlit-UI, REST-API (FastAPI) und SQLite-basiertem Research-Box-System. LLM läuft lokal über LM Studio.

---

## 📂 Verzeichnis-Baum

```
D:\AI_agent\
├── app.py                 # Streamlit Web-UI        (Port 8501)
├── api.py                 # FastAPI REST-API         (Port 8000)
├── main.py                # CLI-Einstiegspunkt
├── start.py               # Combined Launcher (API + UI parallel)
│
├── agent.py               # Agent-Loop (ReAct + Planning + Tool-Calls)
├── research_box.py        # SQLite-Persistenz + Embedding-Reuse
├── validation.py          # Confidence-Scoring (strukturell)
├── tools.py               # web_search, fetch_url, extract_contacts, save_json, ...
├── llm_client.py          # OpenAI-kompatibler Client für LM Studio
├── embeddings.py          # Cosine-Similarity + nomic-Embed Wrapper
├── config.py              # ENV-basierte Settings
│
├── requirements.txt       # pip-Dependencies
├── .gitignore
│
├── research.db            # SQLite-DB (Research Boxes) — wird beim 1. Run erzeugt
├── memory.json            # ⚠️ Altbestand (kann gelöscht werden)
│
├── results/               # JSON-Ergebnisdateien aus save_json
├── logs/                  # Trace-Logs pro Run (run_YYYYMMDD_HHMMSS.json)
└── venv/                  # Python Virtual Environment
```

---

## 🧩 Modul-Abhängigkeiten

```
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   app.py      │     │   api.py      │     │   main.py     │
│ (Streamlit)   │     │  (FastAPI)    │     │    (CLI)      │
└───────┬───────┘     └───────┬───────┘     └───────┬───────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                      ┌───────────────┐
                      │   agent.py    │  ◄──── Agent-Loop
                      │  run, verify, │
                      │  validate_rb  │
                      └───────┬───────┘
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
      ┌──────────────┐ ┌──────────────┐ ┌────────────────┐
      │   tools.py   │ │ llm_client.py│ │research_box.py │
      │  web_search, │ │ chat,        │ │ ResearchBox,   │
      │  fetch_url,  │ │ chat_with_   │ │ create, load,  │
      │  extract_    │ │ tools        │ │ find_similar   │
      │  contacts    │ └──────┬───────┘ └────────┬───────┘
      └──────────────┘        │                  │
                              ▼                  ▼
                      ┌───────────────┐  ┌──────────────┐
                      │ LM Studio     │  │ embeddings.py│
                      │ :1234/v1      │  │ (cosine,     │
                      │               │  │  embed)      │
                      └───────────────┘  └──────────────┘
                              ▼
                      ┌───────────────┐
                      │  validation.py│
                      │   compute()   │
                      └───────────────┘
```

---

## 📄 Datei-für-Datei

### Einstiegspunkte

| Datei | Rolle | Start-Befehl |
|---|---|---|
| [app.py](app.py) | Streamlit Web-UI mit Live-Fortschritt, Tabs, Commands | `streamlit run app.py` |
| [api.py](api.py) | FastAPI REST-Server mit `/research_box/*` Endpoints | `python api.py` |
| [main.py](main.py) | CLI (1 Task → 1 Ergebnis als JSON) | `python main.py "dein Task"` |
| [start.py](start.py) | Startet API + UI parallel | `python start.py` |

### Kern-Logik

| Datei | Inhalt |
|---|---|
| [agent.py](agent.py) | `run()` (ReAct-Loop mit Tool-Calling), `_plan()` (Planner), `verify_rb()` (Re-Fetch + Substring-Check), `validate_rb()`, `extend_rb()` |
| [research_box.py](research_box.py) | `ResearchBox`-Klasse, SQLite-Schema, `create / load / list_all / delete / find_similar / recall_hints` |
| [validation.py](validation.py) | `compute()` — Confidence basierend auf `source_url ∈ visited_sources × min(1, n_sources/3)` |
| [tools.py](tools.py) | 5 Agent-Tools + Tool-Schemas für OpenAI-Function-Calling + FIFO-Caches |

### Infrastruktur

| Datei | Inhalt |
|---|---|
| [llm_client.py](llm_client.py) | OpenAI-Client (LM Studio), `chat()`, `chat_with_tools()`, liest `MODEL_NAME` dynamisch aus env |
| [embeddings.py](embeddings.py) | `embed()` (nomic via LM Studio), `cosine()` |
| [config.py](config.py) | ENV-Konstanten: `LM_STUDIO_BASE_URL`, `MODEL_NAME`, `EMBEDDING_MODEL`, `DB_PATH`, `API_PORT`, `MAX_ITERATIONS`, Thresholds |

### Runtime-Daten

| Pfad | Zweck |
|---|---|
| `research.db` | SQLite — alle Research Boxes (tasks, sources, visited, extracted_data, entities, validation, embedding) |
| `results/*.json` | Strukturierte Ergebnisdateien (`save_json`-Tool) |
| `logs/run_*.json` | Vollständige Trace-Logs jedes Runs (jeder Schritt) |
| `memory.json` | ⚠️ Altbestand aus früherer Version — ersetzt durch `research.db` |

---

## 🛠️ Agent-Tools (verfügbar für das LLM)

| Tool | Zweck | Return |
|---|---|---|
| `web_search(query, max_results, region)` | DDGS-Suche, mit FIFO-Cache | `list[{title, url, snippet}]` |
| `web_search_parallel(queries, max_results)` | N Suchen parallel (Threads) | `dict[query → results]` |
| `fetch_url(url, max_chars)` | HTML holen, script/nav entfernen | gereinigter Text |
| `extract_contacts(text)` | Regex für E-Mail/Telefon/PLZ-Adresse | `{emails, phones, addresses}` |
| `save_json(filename, data)` | JSON in `results/` speichern | Dateipfad |
| `finish(result)` | Task beenden, Ergebnis zurückgeben | Final-Result |

---

## 🌐 API-Endpoints (FastAPI)

Swagger: **http://localhost:8000/docs**

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/research_box` | Alle RBs listen |
| `GET` | `/research_box/{id}` | Einzelne RB |
| `POST` | `/research_box` | Neue RB aus `{"task": "..."}` |
| `POST` | `/research_box/{id}/validate` | Re-Validate (strukturell) |
| `POST` | `/research_box/{id}/verify` | Verify (re-fetch + Substring-Check) |
| `POST` | `/research_box/{id}/extend` | Erweitern (nur neue Quellen) |
| `GET` | `/research_box/{id}/export?fmt=csv\|json` | Export |
| `DELETE` | `/research_box/{id}` | Löschen |

---

## 🗄️ Research-Box Datenmodell (SQLite)

```sql
CREATE TABLE research_box (
    id              TEXT PRIMARY KEY,       -- 12-char uuid hex
    task            TEXT NOT NULL,
    status          TEXT NOT NULL,          -- running|completed|verified|error|cancelled|max_iterations
    sources         TEXT NOT NULL,          -- JSON: alle entdeckten URLs
    visited_sources TEXT NOT NULL,          -- JSON: per fetch_url tatsächlich besuchte
    extracted_data  TEXT,                   -- JSON: finales Ergebnis
    entities        TEXT,                   -- JSON: {emails, phones, addresses, ...}
    validation      TEXT,                   -- JSON: {confidence, label, per_item, ...}
    iterations      INTEGER DEFAULT 0,
    embedding       TEXT,                   -- JSON: nomic-Embedding des Tasks
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_rb_updated ON research_box(updated_at);
```

---

## ⚙️ Konfiguration (config.py)

| ENV-Variable | Default | Zweck |
|---|---|---|
| `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio Endpoint |
| `LM_STUDIO_API_KEY` | `lm-studio` | Dummy-Key |
| `MODEL_NAME` | `local-model` | Chat-Modell |
| `EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Embedding-Modell |
| `API_HOST` | `127.0.0.1` | FastAPI Host |
| `API_PORT` | `8000` | FastAPI Port |
| `MAX_ITERATIONS` | `15` | Max Tool-Steps pro Run |
| `SIMILARITY_REUSE_THRESHOLD` | `0.72` | ab wann find_similar eine alte RB wiederverwendet |

---

## 🚀 Schnellstart

```powershell
# 1. venv aktivieren
cd D:\AI_agent
.\venv\Scripts\Activate.ps1

# 2. LM Studio: qwen3-14b laden, Server auf :1234 starten
lms load qwen3-14b

# 3. Variante A: beides zusammen
python start.py

# 3. Variante B: einzeln
python api.py                    # API  → http://localhost:8000/docs
streamlit run app.py             # UI   → http://localhost:8501
```

---

## 🎛️ Agent-Modi

| Modus | UI-Button | Was passiert |
|---|---|---|
| **Neuer Task** | 🚀 Starten | Neue RB (oder find_similar reuse), voller Search-Loop |
| **Nur neue Quellen** | ➕ | `extend=True` — visited_sources werden aus allen Suchergebnissen gefiltert, bevor das LLM sie sieht |
| **Verifizieren** | 🔍 | Für jede `source_url` in `extracted_data`: fetch_url + Substring-Check ob Item-Name in der Seite steht |
| **Re-Validate** | ✅ | Schnelle strukturelle Prüfung (ohne Netzwerk) |
| **Export** | ⬇️ JSON/CSV/Excel | Download aus UI oder `GET /export?fmt=...` |

---

## 🧪 Was noch fehlt (ehrliche Liste)

- [ ] **Tests** (pytest) für `validation.py`, `research_box.py`, API-Endpoints
- [ ] **memory.json** entfernen (Altbestand)
- [ ] Verify **schärfen** — nicht nur Namen-Substring, sondern einzelne Felder (Adresse, Funding, etc.) gezielt prüfen
- [ ] **Retries** bei transienten DDGS/HTTP-Fehlern
- [ ] **LLM-Streaming** in der UI
- [ ] **Kontext-Kompression** bei langen Runs (jetzt wächst die Message-History linear)
- [ ] **Skalierung** von `find_similar` ab ~1000 RBs (aktuell lädt alle in RAM)

---

*Stand: 2026-04-19*
