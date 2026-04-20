# AI Research Agent

Lokaler, autonomer Research-Agent mit Web-UI, REST-API und SQLite-basiertem Research-Box-System. LLM läuft lokal via **LM Studio**.

Gibst du ihm einen Task wie *„Finde 5 deutsche KI-Startups"*, sucht er im Web, extrahiert strukturierte Daten, validiert Quellen mit 8 Methoden und speichert alles. Kein Cloud-Call, keine API-Kosten, deine Daten bleiben lokal.

---

## Schnellstart

**1. Voraussetzungen**
- Python 3.9+
- [LM Studio](https://lmstudio.ai) mit Chat-Modell (empfohlen `qwen3-14b` oder `qwen3-32b`) + `text-embedding-nomic-embed-text-v1.5`

**2. Setup**
```powershell
cd D:\AI_agent
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**3. Modell laden**
```powershell
lms load qwen3-14b --ttl 0
```
(TTL 0 = Modell wird nicht automatisch entladen)

**4. Agent starten**
```powershell
python start.py
```
Öffnet: UI http://localhost:8501 · API http://localhost:8000/docs

Oder einzeln: `python api.py` · `streamlit run app.py` · `python main.py "dein Task"`

---

## Features

- **Autonomer ReAct-Loop** mit Planning, Tool-Calling und Auto-Fetch-Fallback
- **6 Agent-Tools**: `web_search`, `web_search_parallel`, `fetch_url`, `extract_contacts`, `save_json`, `finish`
- **Research Boxes** (SQLite) — zentrale DB, wiederverwendbar, kontinuierlich erweiterbar
- **8 Validierungs-Methoden** mit gewichteter Confidence:
  - `name_substring` · `all_fields` · `cross_source` · `llm_semantic`
  - `domain_trust` · `field_completeness` · `consistency` · `relationship_validation`
- **Commands**: `extend` (Multi-Round) · `verify` · `analyze_rows` (Deep) · `validate` · `export`
- **Task-Schema** (optional) — erzwingt exakte Output-Felder pro Item
- **URL-Kanonisierung + Dedup** beim Erweitern
- **Async Jobs** für lange Tasks (non-blocking API via `?background=true`)
- **Validation-History** pro RB (Timeline aller Validations-Runs)
- **Per-Zeilen-Details** (zeigt jede Methode + Begründung für jede Zeile)
- **Row-Editing** direkt in der UI
- **Semantic Memory** via nomic-Embeddings (ähnliche Tasks werden wiederverwendet)
- **Auto-Cleanup** von alten Logs/Results
- **Security**: optionaler API-Key, Rate-Limit, CORS, Swagger-Authorize
- **126 Tests** (<2s), mockt LM Studio komplett

---

## Umgebungsvariablen

| ENV | Default | Zweck |
|---|---|---|
| `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio Endpoint |
| `MODEL_NAME` | `qwen3-14b` | Chat-Modell |
| `EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Embedding-Modell |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | FastAPI |
| `API_KEY` | `""` (aus) | Wenn gesetzt: jeder API-Call braucht `X-API-Key`-Header |
| `RATE_LIMIT_PER_MINUTE` | `0` (aus) | Req/min pro IP |
| `CORS_ORIGINS` | `http://localhost:8501,http://127.0.0.1:8501` | Komma-Liste |
| `AUTO_CLEANUP` | `1` | `0` deaktiviert Auto-Aufräumen |
| `AUTO_CLEANUP_LOGS_DAYS` | `30` | Logs-Retention |
| `AUTO_CLEANUP_RESULTS_DAYS` | `30` | Result-JSONs-Retention |
| `AUTO_CLEANUP_RBS_DAYS` | `90` | Fehlgeschlagene RBs löschen älter als N Tage |
| `MAX_ITERATIONS` | `15` | Max Tool-Steps pro Run |
| `SIMILARITY_REUSE_THRESHOLD` | `0.72` | ab wann `find_similar` alte RB wiederverwendet |

---

## API

Swagger: **http://localhost:8000/docs**

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/` | Endpoint-Index + Security-Status |
| `GET` | `/research_box?offset=&limit=` | Paginierte RB-Liste |
| `GET` | `/research_box/{id}` | Einzelne RB |
| `POST` | `/research_box` | Task starten (`?background=true` → Job-ID) |
| `POST` | `/research_box/{id}/validate` | Strukturelle Re-Validation |
| `POST` | `/research_box/{id}/verify` | Quellen neu laden + Name-Substring-Check |
| `POST` | `/research_box/{id}/analyze_rows` | Deep-Analyse (alle 8 Methoden) |
| `POST` | `/research_box/{id}/extend` | Search More (`?background=true` möglich) |
| `GET` | `/research_box/{id}/validation` | Nur Validation-Daten |
| `GET` | `/research_box/{id}/export?fmt=csv\|json` | Export |
| `DELETE` | `/research_box/{id}` | Löschen |
| `GET` | `/jobs?offset=&limit=` | Paginierte Job-Liste |
| `GET` | `/jobs/{job_id}` | Job-Status (pending/running/completed/error) |
| `GET` | `/validation_methods` | Liste aller 8 Validierungs-Methoden |

**Beispiel — Task mit Schema:**
```bash
curl -X POST http://localhost:8000/research_box \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Finde 5 deutsche KI-Startups",
    "output_fields": ["name", "website", "description", "source_url"]
  }'
```

**Beispiel — async:**
```bash
curl -X POST "http://localhost:8000/research_box?background=true" \
  -H "Content-Type: application/json" \
  -d '{"task": "Top 10 Proptech DACH"}'
# -> {"job_id": "a1b2c3...", "status": "pending", "poll_url": "/jobs/a1b2c3..."}

curl http://localhost:8000/jobs/a1b2c3...
# pending -> running -> completed
```

---

## Validierungs-Methoden

Gewichtete Confidence-Berechnung. Alle Weights summieren ~1.0:

| Methode | Weight | Was sie prüft |
|---|---:|---|
| `llm_semantic` | 0.22 | LLM liest Seite: *„Bestätigt die Seite das Item?"* |
| `consistency` | 0.18 | LLM prüft interne Feld-Konsistenz (z.B. Berlin ≠ Brandenburg) |
| `cross_source` | 0.14 | Web-Suche: wie viele verschiedene Domains bestätigen den Namen |
| `relationship_validation` | 0.14 | LLM prüft bekannte Beziehungen (city→state, company→industry) |
| `domain_trust` | 0.12 | Autorität der Quelle (Wikipedia/Staat/Medien > Quiz/Listicle) |
| `all_fields` | 0.10 | Alle String-Felder müssen auf der Seite vorkommen |
| `name_substring` | 0.05 | Name steht als Substring auf der Seite |
| `field_completeness` | 0.05 | % der Felder die ausgefüllt sind |

**Confidence-Labels:** `high` ≥ 85% · `medium` ≥ 60% · `low` ≥ 30% · `unverified` < 30%

---

## Projektstruktur

```
D:\AI_agent\
├── app.py              # Streamlit UI (3 Tabs: Neu / Research Boxes / Analyse)
├── api.py              # FastAPI REST-Server (12 Endpoints + Swagger-Auth)
├── main.py             # CLI
├── start.py            # Combined Launcher (API + UI, Port-Check, Model-Preload)
│
├── agent.py            # ReAct-Loop: run(), verify_rb, analyze_rows_rb, extend_rb(rounds)
├── research_box.py     # SQLite + ResearchBox-Klasse, validation_history
├── validators.py       # 8 Deep-Validation-Methoden + Gewichtung
├── validation.py       # Schnelle strukturelle Re-Validation
├── tools.py            # Agent-Tools + FIFO-Caches + Retries
├── llm_client.py       # OpenAI-Client für LM Studio (dynamisches MODEL_NAME)
├── embeddings.py       # Nomic-Embeddings + Cosine-Similarity
├── url_utils.py        # URL-Kanonisierung (www, tracking params, trailing /)
├── dedup.py            # Item-Merge mit Name-/Source-Dedup
├── jobs.py             # Async-Job-Queue (Thread-basiert, FIFO 500)
├── security.py         # API-Key-Middleware + Rate-Limit (Sliding-Window)
├── cleanup.py          # Auto-Cleanup von Logs/Results/RBs
├── api_models.py       # Pydantic-Response-Models
├── config.py           # ENV-Settings zentral
│
├── tests/              # pytest (126 Tests, alle Mocks — kein LM Studio nötig)
├── results/            # JSON-Ergebnisse (save_json-Tool)
├── logs/               # Trace-Logs pro Run
├── research.db         # SQLite (wird beim 1. Run erzeugt)
└── venv/
```

Mehr Details: [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

---

## Entwicklung

**Tests laufen (ohne LM Studio):**
```powershell
python -m pytest tests/                    # alle 126
python -m pytest tests/test_agent.py -v    # einzelne Datei
python -m pytest tests/ -k "consistency"   # nach Pattern
```

**Neue Validierungs-Methode hinzufügen:**
1. Funktion `method_<name>` in [validators.py](validators.py)
2. Eintrag in `METHODS`-Dict + `METHOD_WEIGHTS` (Gewichte re-balancieren zu Summe 1.0)
3. Wiring in [agent.py](agent.py) `analyze_rows_rb()`
4. Test in [tests/test_validators.py](tests/test_validators.py)

**Neues Agent-Tool:**
1. Funktion in [tools.py](tools.py)
2. Schema-Eintrag in `TOOL_SCHEMAS` + Registrierung in `TOOLS`-Dict
3. Ggf. Event-Emit in [agent.py](agent.py) für UI-Live-Anzeige

---

## Agent-Modi

| Command | UI | Was passiert |
|---|---|---|
| 🚀 **Neuer Task** | Tab 🆕 Neu | Neue RB (oder `find_similar`-Reuse), voller Search-Loop |
| ➕ **Neue Quellen** (1-5 Runden) | Tab 📚 / Commands | Visited URLs werden strikt ausgeblendet, N Extend-Runden |
| 🔍 **Verifizieren** | Commands | Jede `source_url` neu laden + Substring-Check |
| 🔬 **Deep-Analyse** | Commands | Alle 8 Methoden pro Zeile, LLM-Calls parallelisiert |
| ✅ **Re-Validate** | Commands | Schnelle strukturelle Prüfung (ohne Netz) |
| ✏️ **Bearbeiten** | In RB-Expander | Einzelne Zellen editieren, Zeilen löschen |
| ⬇️ **Export** | Download-Buttons | JSON / CSV / Excel |

---

## Cleanup

Auto-Cleanup läuft beim Start (1×/Tag via Throttle-Marker). Manuell:
```powershell
python cleanup.py --stats                      # Größen anzeigen
python cleanup.py --logs-days 30 --apply       # wirklich löschen
python cleanup.py --rbs-days 90 --rbs-status error,cancelled --apply
```

UI: Tab **📊 Analyse** → **🗑️ Cleanup jetzt erzwingen (force)**

---

## Troubleshooting

**`No models loaded`** → `lms load qwen3-14b --ttl 0`

**`src property must be a valid json object`** → Modell produziert kaputtes JSON. Nimm `qwen3-32b` oder präziseren Task-Prompt.

**Port 8000 belegt:**
```powershell
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

**Swagger /docs lädt nicht** → `pydantic==2.7.4` ist gepinnt (neuere Versionen inkompatibel mit Python 3.9)

**Pydantic-Core-Mismatch beim Install** → alle Python/Streamlit-Prozesse beenden, dann `pip install --force-reinstall pydantic==2.7.4`

**Confidence bleibt 0%** → klick **✅ Re-Validate** (URL-Kanonisierung wurde nachgezogen, alte Validation ist stale)

**Agent halluziniert source_url ohne zu fetchen** → Auto-Fetch-Fallback holt bis zu 5 URLs nach, falls das Modell sie nicht selbst gefetcht hat

---

## License

Privat-Projekt, keine Lizenz vergeben.
