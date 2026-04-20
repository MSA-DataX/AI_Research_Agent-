# AI Research Agent — Projektstruktur

Lokaler, autonomer Research-Agent mit Streamlit-UI, REST-API (FastAPI) und SQLite-basiertem Research-Box-System. LLM läuft lokal über LM Studio.

---

## 📂 Verzeichnis-Baum

```
D:\AI_agent\
├── app.py                 # Streamlit Web-UI (Port 8501)
├── api.py                 # FastAPI REST-API (Port 8000)
├── main.py                # CLI-Einstiegspunkt
├── start.py               # Combined Launcher (API + UI, Port-Check, Model-Preload)
│
├── agent.py               # Agent-Loop (ReAct + Planning + Tool-Calls + Auto-Fetch)
├── research_box.py        # SQLite-Persistenz + Embedding-Reuse + Validation-History
├── validation.py          # Strukturelle Re-Validation (schnell, offline)
├── validators.py          # 8 Deep-Validation-Methoden + Gewichtung
├── tools.py               # web_search, fetch_url, extract_contacts, save_json + FIFO-Cache + Retry
├── llm_client.py          # OpenAI-kompatibler Client für LM Studio
├── embeddings.py          # Cosine-Similarity + nomic-Embed Wrapper
├── url_utils.py           # URL-Kanonisierung (www/tracking params/trailing slash)
├── dedup.py               # Item-Merge mit Name- und Source-basiertem Dedup
├── jobs.py                # Async-Job-Queue (Thread-basiert, FIFO 500, thread-safe)
├── security.py            # API-Key-Middleware + Rate-Limit (Sliding-Window)
├── cleanup.py             # Auto-Cleanup Logs/Results/RBs + Throttle-Marker
├── api_models.py          # Pydantic-Response-Models für typed API
├── config.py              # ENV-basierte Settings (zentral)
│
├── requirements.txt       # pip-Dependencies (pydantic 2.7 pinned)
├── cleanup.bat            # Windows-Batch für Task-Scheduler
├── scripts/
│   └── register_cleanup_task.ps1  # Registriert wöchentlichen Cleanup-Job
├── .gitignore
│
├── research.db            # SQLite-DB (wird beim 1. Run erzeugt)
├── .last_cleanup          # Timestamp der letzten Auto-Cleanup-Durchführung
│
├── tests/                 # 126 pytest-Tests (keine LM Studio nötig)
│   ├── conftest.py        # Fixtures: tmp_db, fake_embed
│   ├── agent_fakes.py     # Mock-Helper: fake_chat_response, queued_chat_with_tools
│   ├── test_agent.py      # 17 Tests für agent.py
│   ├── test_api.py        # API-Endpoint-Tests (TestClient)
│   ├── test_cleanup.py    # Cleanup + Auto-Cleanup Tests
│   ├── test_dedup.py      # Item-Merge + Dedup
│   ├── test_integration.py # E2E mit gemockten Tools
│   ├── test_jobs.py       # Async-Job-Queue
│   ├── test_research_box.py # SQLite CRUD + History
│   ├── test_security.py   # API-Key + Rate-Limit + CORS
│   ├── test_url_utils.py  # URL-Kanonisierung
│   ├── test_validation.py # Strukturelle Validation
│   └── test_validators.py # 8 Deep-Methoden + Gewichtung
│
├── results/               # JSON-Ergebnisse (save_json-Tool)
├── logs/                  # Trace-Logs pro Run (run_YYYYMMDD_HHMMSS.json)
└── venv/                  # Python Virtual Environment
```

---

## 🧩 Modul-Abhängigkeiten

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  app.py  │  │  api.py  │  │ main.py  │  │ start.py │
│(Streamlit)  │(FastAPI) │  │  (CLI)   │  │(Launcher)│
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │             │             │
     │       ┌─────┴─────┐       │             │
     │       │api_models │       │             │
     │       │security.py│       │             │
     │       │ jobs.py   │       │             │
     │       └─────┬─────┘       │             │
     │             │             │             │
     └─────────────┴─────────────┴─────────────┘
                   │
                   ▼
            ┌─────────────┐       ┌──────────────┐
            │  agent.py   │◄──────┤ cleanup.py   │
            │  run()      │       └──────────────┘
            │  verify_rb  │
            │  analyze_   │
            │  extend_rb  │
            └──────┬──────┘
     ┌────────────┬┴───┬─────────────┬─────────────┐
     ▼            ▼    ▼             ▼             ▼
┌────────┐  ┌──────┐  ┌─────────┐  ┌──────────┐  ┌────────┐
│tools.py│  │llm_  │  │research_│  │validators│  │valid-  │
│        │  │client│  │box.py   │  │.py (8)   │  │ation   │
└────┬───┘  └──┬───┘  └────┬────┘  └────┬─────┘  │.py     │
     │         │           │            │         └────────┘
     ▼         ▼           ▼            ▼
┌─────────┐ ┌──────┐ ┌──────────┐  ┌──────┐
│url_utils│ │ LM   │ │embeddings│  │dedup │
│dedup.py │ │Studio│ │.py       │  │.py   │
└─────────┘ └──────┘ └──────────┘  └──────┘
```

---

## 📄 Datei-für-Datei

### Einstiegspunkte

| Datei | Rolle | Start |
|---|---|---|
| [app.py](app.py) | Streamlit UI mit 3 Tabs, Live-Progress, Commands, Row-Editing | `streamlit run app.py` |
| [api.py](api.py) | FastAPI mit 12 Endpoints, Swagger-Auth, Typed Responses | `python api.py` |
| [main.py](main.py) | CLI (1 Task → JSON) | `python main.py "task"` |
| [start.py](start.py) | Startet API + UI, prüft Port 8000, lädt Modell via `lms` | `python start.py` |

### Kern-Logik

| Datei | Inhalt |
|---|---|
| [agent.py](agent.py) | `run()` (ReAct+Planning+Auto-Fetch), `_plan()`, `verify_rb()`, `validate_rb()`, `extend_rb(rounds=N)`, `analyze_rows_rb(methods)` mit parallelen LLM-Calls |
| [research_box.py](research_box.py) | `ResearchBox`-Klasse mit 14 Spalten, `create / load / list_all / delete / find_similar / recall_hints`, `append_validation_snapshot()` für Timeline |
| [validators.py](validators.py) | 8 Methoden + `METHOD_WEIGHTS` + `verdict_for_row()` (gewichtet) + `TRUSTED_DOMAINS`-Liste |
| [validation.py](validation.py) | `compute()` — schnelle strukturelle Confidence (kein Netz, kein LLM) |
| [tools.py](tools.py) | 5 Tools + Schemas + FIFO-Cache (128 Einträge) + `_retry()` (Exponential Backoff) |
| [dedup.py](dedup.py) | `merge_items()` — Dedup per normalisiertem Name ODER kanonischer URL |
| [url_utils.py](url_utils.py) | `canonicalize_url()` — strip www/trailing-slash/UTM/fbclid, sortiert Query-Keys |

### Infrastruktur

| Datei | Inhalt |
|---|---|
| [llm_client.py](llm_client.py) | `chat()`, `chat_with_tools()`, `_current_model()` liest `MODEL_NAME` dynamisch aus env |
| [embeddings.py](embeddings.py) | `embed()` via nomic in LM Studio, `cosine()` (reine Python) |
| [config.py](config.py) | Zentrale ENV-Konstanten (12 Variablen) |
| [api_models.py](api_models.py) | `TaskIn`, `ResearchBoxOut`, `ValidationReport`, `JobStatus`, `PagedResearchBoxes`, etc. |
| [security.py](security.py) | `security_middleware()` — API-Key-Check + Rate-Limit, public-Endpoints exempt |
| [jobs.py](jobs.py) | In-Memory Job-Store (Lock-safe), `run_async()` spawnt Daemon-Thread |
| [cleanup.py](cleanup.py) | `prune_logs/results/rbs()`, `auto_cleanup()` mit 24h-Throttle |

### Tests (126)

| Datei | Tests |
|---|---:|
| test_agent.py | 17 |
| test_validators.py | 37 |
| test_research_box.py | 11 |
| test_security.py | 11 |
| test_url_utils.py | 11 |
| test_api.py | 9 |
| test_jobs.py | 8 |
| test_dedup.py | 8 |
| test_cleanup.py | 8 |
| test_validation.py | 5 |
| test_integration.py | 2 |

### Runtime-Daten

| Pfad | Zweck |
|---|---|
| `research.db` | SQLite — alle Research Boxes (task, sources, visited, extracted_data, entities, validation, validation_history, output_fields, embedding) |
| `results/*.json` | Strukturierte Ergebnis-Dateien vom `save_json`-Tool |
| `logs/run_*.json` | Trace-Logs pro Run (jeder Tool-Call, jede Observation) |
| `.last_cleanup` | Timestamp der letzten Auto-Cleanup-Durchführung (Throttle-Marker) |

---

## 🛠️ Agent-Tools

| Tool | Zweck | Cache? |
|---|---|:---:|
| `web_search(query, max_results, region)` | DDGS-Suche mit Retries | ✅ FIFO 128 |
| `web_search_parallel(queries, max_results)` | N Suchen parallel (Threads) | ❌ |
| `fetch_url(url, max_chars)` | HTML holen, scripts/nav entfernen | ✅ FIFO 128 |
| `extract_contacts(text)` | Regex: E-Mail/Telefon/PLZ-Adresse | ❌ |
| `save_json(filename, data)` | JSON in `results/` speichern | ❌ |
| `finish(result)` | Task beenden + Auto-Fetch-Fallback | — |

---

## 🌐 API-Endpoints (12)

Swagger: **http://localhost:8000/docs**

### Research Boxes
| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/` | Endpoint-Index + Security-Status |
| `GET` | `/research_box?offset=&limit=` | Paginiert |
| `GET` | `/research_box/{id}` | Einzel |
| `POST` | `/research_box?background=false` | Task starten (sync oder async) |
| `POST` | `/research_box/{id}/validate` | Strukturelle Re-Validation |
| `POST` | `/research_box/{id}/verify` | Quellen re-fetchen + Substring |
| `POST` | `/research_box/{id}/analyze_rows?methods=` | Deep-Analyse (8 Methoden) |
| `POST` | `/research_box/{id}/extend?background=false` | Search More |
| `GET` | `/research_box/{id}/validation` | Nur Validation-Daten |
| `GET` | `/research_box/{id}/export?fmt=csv\|json` | Export |
| `DELETE` | `/research_box/{id}` | Löschen |

### Jobs + Meta
| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/jobs?offset=&limit=` | Paginierte Job-Liste |
| `GET` | `/jobs/{job_id}` | Job-Status |
| `GET` | `/validation_methods` | Dict der 8 Methoden |

---

## 🔒 Validierungs-Methoden & Gewichtung

```
┌──────────────────────────────┬────────┬─────────────────────────────┐
│ Methode                      │ Weight │ Was sie prüft               │
├──────────────────────────────┼────────┼─────────────────────────────┤
│ llm_semantic                 │  0.22  │ LLM: stützt Seite Item?     │
│ consistency                  │  0.18  │ LLM: Felder intern passend? │
│ cross_source                 │  0.14  │ N Domains bestätigen Name   │
│ relationship_validation      │  0.14  │ LLM: Relationen korrekt?    │
│ domain_trust                 │  0.12  │ Wikipedia/Staat > Quiz      │
│ all_fields                   │  0.10  │ Alle Felder in Seite        │
│ name_substring               │  0.05  │ Name in Seite               │
│ field_completeness           │  0.05  │ % ausgefüllte Felder        │
├──────────────────────────────┼────────┤                             │
│ Σ                            │  1.00  │                             │
└──────────────────────────────┴────────┴─────────────────────────────┘
```

**LLM-Calls parallelisiert** in `analyze_rows_rb()`: `llm_semantic`, `consistency`, `relationship_validation` laufen für jede Zeile gleichzeitig (ThreadPoolExecutor, max 3 Workers).

**Labels:** `high` ≥ 85 · `medium` ≥ 60 · `low` ≥ 30 · `unverified` < 30

---

## 🗄️ Research-Box Datenmodell (SQLite)

```sql
CREATE TABLE research_box (
    id                  TEXT PRIMARY KEY,    -- 12-char uuid hex
    task                TEXT NOT NULL,
    status              TEXT NOT NULL,       -- running|completed|verified|error|cancelled|max_iterations
    sources             TEXT NOT NULL,       -- JSON: alle entdeckten URLs (kanonisiert)
    visited_sources     TEXT NOT NULL,       -- JSON: per fetch_url tatsächlich besuchte
    extracted_data      TEXT,                -- JSON: finales Ergebnis
    entities            TEXT,                -- JSON: {emails, phones, addresses, ...}
    validation          TEXT,                -- JSON: aktueller Validation-Report
    iterations          INTEGER DEFAULT 0,
    embedding           TEXT,                -- JSON: nomic-Embedding (task)
    output_fields       TEXT,                -- JSON: erzwungene Schema-Felder
    validation_history  TEXT,                -- JSON: letzte 20 Validation-Snapshots (Timeline)
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_rb_updated ON research_box(updated_at);
```

---

## ⚙️ Konfiguration (config.py)

| ENV | Default | Bereich |
|---|---|---|
| `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio |
| `MODEL_NAME` | `local-model` | LM Studio |
| `EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | LM Studio |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | FastAPI |
| `API_KEY` | `""` | Security |
| `RATE_LIMIT_PER_MINUTE` | `0` | Security |
| `CORS_ORIGINS` | `localhost:8501,127.0.0.1:8501` | Security |
| `AUTO_CLEANUP` | `1` | Cleanup |
| `AUTO_CLEANUP_INTERVAL_HOURS` | `24` | Cleanup |
| `AUTO_CLEANUP_LOGS_DAYS` | `30` | Cleanup |
| `AUTO_CLEANUP_RESULTS_DAYS` | `30` | Cleanup |
| `AUTO_CLEANUP_RBS_DAYS` | `90` | Cleanup |
| `MAX_ITERATIONS` | `15` | Agent |
| `SIMILARITY_REUSE_THRESHOLD` | `0.72` | Agent |

---

## 🚀 Schnellstart

```powershell
cd D:\AI_agent
.\venv\Scripts\Activate.ps1
lms load qwen3-14b --ttl 0
python start.py
```

Oder einzeln:
```powershell
python api.py                       # API
streamlit run app.py                # UI
python main.py "task"               # CLI
python -m pytest tests/             # Tests (kein LM Studio nötig)
```

---

## 🎛️ Agent-Modi

| Modus | UI-Button | Was passiert |
|---|---|---|
| **Neuer Task** | 🚀 Starten | Neue RB (oder find_similar), Search-Loop |
| **Multi-Round Extend** | ➕ Neue Quellen (1-5) | N Runden, Dedup + strikter Visited-Filter |
| **Verifizieren** | 🔍 Verify | Re-Fetch + Substring (schnell) |
| **Deep-Analyse** | 🔬 Deep | 8 Methoden, LLM-Calls parallel |
| **Re-Validate** | ✅ | Struktur-Check (ohne Netz) |
| **Bearbeiten** | ✏️ | Zellen inline editieren, Zeilen löschen |
| **Export** | ⬇️ JSON/CSV/Excel | Download oder `GET /export` |

---

## 🔐 Security-Features

| Feature | Aktivierung | Default |
|---|---|:---:|
| API-Key (`X-API-Key` Header) | `API_KEY=<token>` setzen | Aus |
| Rate-Limit (per IP, 60s-Window) | `RATE_LIMIT_PER_MINUTE=60` | 0 (aus) |
| CORS (spezifische Origins) | `CORS_ORIGINS=...` | localhost:8501 |
| Swagger-Authorize-Button | Automatisch wenn API_KEY gesetzt | — |

Public-Endpoints (keine Auth nötig): `/`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`

---

*Stand: 2026-04-20*
