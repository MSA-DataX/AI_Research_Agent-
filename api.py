from __future__ import annotations

import csv
import io
import json

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import PlainTextResponse, Response

import jobs
import research_box as rb_store
from agent import analyze_rows_rb as agent_analyze_rows
from agent import run as agent_run
from agent import validate_rb as agent_validate_rb
from agent import verify_rb as agent_verify_rb
from api_models import (
    DeletedOut,
    JobAck,
    JobStatus,
    PagedJobs,
    PagedResearchBoxes,
    ResearchBoxOut,
    RunResult,
    TaskIn,
    ValidationMethods,
    ValidationReport,
)
from cleanup import auto_cleanup
from security import cors_origins, security_middleware
from validators import METHODS as VALIDATION_METHODS

auto_cleanup()

app = FastAPI(
    title="AI Research Agent API",
    description="Research Box System: autonome Recherche, Validierung, Persistenz.",
    version="2.0",
)

app.middleware("http")(security_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cors_origins()),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def _custom_openapi():
    from security import api_key_enabled
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    if api_key_enabled():
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        for path, methods in schema.get("paths", {}).items():
            if path in ("/", "/docs", "/redoc", "/openapi.json"):
                continue
            for op in methods.values():
                if isinstance(op, dict) and "operationId" in op:
                    op["security"] = [{"ApiKeyHeader": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


def _rb_out(rb: rb_store.ResearchBox) -> ResearchBoxOut:
    return ResearchBoxOut(**rb.to_dict())


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    from security import api_key_enabled, current_rate_limit
    return {
        "service": "AI Research Agent API",
        "version": "2.0",
        "docs": "/docs",
        "security": {
            "api_key_required": api_key_enabled(),
            "rate_limit_per_minute": current_rate_limit(),
            "cors_origins": list(cors_origins()),
        },
        "endpoints": {
            "GET  /research_box": "Liste (paginiert) aller Research Boxes",
            "GET  /research_box/{id}": "Einzelne Research Box",
            "POST /research_box": "Neuen Task starten (sync oder background=true)",
            "POST /research_box/{id}/validate": "Re-Validation (schnell)",
            "POST /research_box/{id}/verify": "Quellen re-fetchen & gegenprüfen",
            "POST /research_box/{id}/analyze_rows": "Deep-Analyse (alle 4 Methoden)",
            "POST /research_box/{id}/extend": "Search More (nur neue Quellen)",
            "GET  /research_box/{id}/validation": "Nur Validation abrufen",
            "GET  /research_box/{id}/export?fmt=csv|json": "Export",
            "DELETE /research_box/{id}": "Löschen",
            "GET  /jobs": "Liste aller Hintergrund-Jobs",
            "GET  /jobs/{job_id}": "Status eines Jobs",
            "GET  /validation_methods": "Liste aller Validierungs-Methoden",
        },
    }


@app.get("/research_box", response_model=PagedResearchBoxes)
def list_boxes(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    all_rbs = rb_store.list_all(limit=10000)
    total = len(all_rbs)
    page = all_rbs[offset : offset + limit]
    return PagedResearchBoxes(
        total=total,
        offset=offset,
        limit=limit,
        items=[_rb_out(rb) for rb in page],
    )


@app.get("/research_box/{rb_id}", response_model=ResearchBoxOut)
def get_box(rb_id: str):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    return _rb_out(rb)


@app.post("/research_box")
def create_and_run(
    payload: TaskIn,
    background: bool = Query(False, description="If true, returns job_id immediately"),
):
    if background:
        job_id = jobs.create_job("research")
        jobs.run_async(
            job_id,
            agent_run,
            payload.task.strip(),
            verbose=False,
            output_fields=payload.output_fields,
        )
        return JobAck(job_id=job_id, status="pending", poll_url=f"/jobs/{job_id}")
    return RunResult(**agent_run(
        payload.task.strip(),
        verbose=False,
        output_fields=payload.output_fields,
    ))


@app.post("/research_box/{rb_id}/validate", response_model=ValidationReport)
def validate(rb_id: str):
    v = agent_validate_rb(rb_id)
    if "error" in v:
        raise HTTPException(404, v["error"])
    return ValidationReport(**v)


@app.post("/research_box/{rb_id}/verify", response_model=ValidationReport)
def verify(rb_id: str):
    v = agent_verify_rb(rb_id)
    if "error" in v:
        raise HTTPException(404, v["error"])
    return ValidationReport(**v)


@app.get("/validation_methods", response_model=ValidationMethods)
def list_methods():
    return ValidationMethods(methods=VALIDATION_METHODS)


@app.post("/research_box/{rb_id}/analyze_rows", response_model=ValidationReport)
def analyze_rows(
    rb_id: str,
    methods: str = Query("", description="Comma-separated method names; empty = all"),
):
    method_list = [m.strip() for m in methods.split(",") if m.strip()] or None
    v = agent_analyze_rows(rb_id, methods=method_list)
    if "error" in v:
        raise HTTPException(404, v["error"])
    return ValidationReport(**v)


@app.get("/research_box/{rb_id}/validation")
def get_validation(rb_id: str):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    return {
        "rb_id": rb.id,
        "task": rb.task,
        "status": rb.status,
        "validation": rb.validation,
    }


@app.post("/research_box/{rb_id}/extend")
def extend(
    rb_id: str,
    background: bool = Query(False, description="If true, returns job_id immediately"),
):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    if background:
        job_id = jobs.create_job("extend")
        jobs.run_async(job_id, agent_run, rb.task, rb_id=rb_id, extend=True, verbose=False)
        return JobAck(job_id=job_id, status="pending", poll_url=f"/jobs/{job_id}")
    return RunResult(**agent_run(rb.task, rb_id=rb_id, extend=True, verbose=False))


@app.get("/research_box/{rb_id}/export", response_class=PlainTextResponse)
def export(rb_id: str, fmt: str = "json"):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    data = rb.extracted_data
    if fmt == "json":
        return PlainTextResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    if fmt == "csv":
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise HTTPException(400, "data is not a list of dicts; cannot export as CSV")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        w.writeheader()
        for row in data:
            w.writerow({k: row.get(k, "") for k in data[0].keys()})
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")
    raise HTTPException(400, "fmt must be 'json' or 'csv'")


@app.delete("/research_box/{rb_id}", response_model=DeletedOut)
def delete(rb_id: str):
    if not rb_store.delete(rb_id):
        raise HTTPException(404, "research_box not found")
    return DeletedOut(deleted=rb_id)


@app.get("/jobs", response_model=PagedJobs)
def list_all_jobs(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    total, items = jobs.list_jobs(offset=offset, limit=limit)
    return PagedJobs(
        total=total,
        offset=offset,
        limit=limit,
        items=[JobStatus(**j) for j in items],
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job_status(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return JobStatus(**job)


if __name__ == "__main__":
    from config import API_HOST, API_PORT
    uvicorn.run(app, host=API_HOST, port=API_PORT)
