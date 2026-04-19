from __future__ import annotations

import csv
import io
import json

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

import research_box as rb_store
from agent import run as agent_run
from agent import validate_rb as agent_validate_rb
from agent import verify_rb as agent_verify_rb
from config import API_HOST, API_PORT

app = FastAPI(
    title="AI Research Agent API",
    description="Access and manage Research Boxes",
    version="1.0",
)


@app.get("/research_box")
def list_boxes(limit: int = Query(100, ge=1, le=500)):
    return [rb.to_dict() for rb in rb_store.list_all(limit=limit)]


@app.get("/research_box/{rb_id}")
def get_box(rb_id: str):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    return rb.to_dict()


@app.post("/research_box")
def create_and_run(payload: dict):
    task = (payload or {}).get("task", "").strip()
    if not task:
        raise HTTPException(400, "'task' field is required")
    return agent_run(task, verbose=False)


@app.post("/research_box/{rb_id}/validate")
def validate(rb_id: str):
    v = agent_validate_rb(rb_id)
    if "error" in v:
        raise HTTPException(404, v["error"])
    return v


@app.post("/research_box/{rb_id}/verify")
def verify(rb_id: str):
    v = agent_verify_rb(rb_id)
    if "error" in v:
        raise HTTPException(404, v["error"])
    return v


@app.post("/research_box/{rb_id}/extend")
def extend(rb_id: str):
    rb = rb_store.load(rb_id)
    if rb is None:
        raise HTTPException(404, "research_box not found")
    return agent_run(rb.task, rb_id=rb_id, extend=True, verbose=False)


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


@app.delete("/research_box/{rb_id}")
def delete(rb_id: str):
    if not rb_store.delete(rb_id):
        raise HTTPException(404, "research_box not found")
    return {"deleted": rb_id}


if __name__ == "__main__":
    uvicorn.run(app, host=API_HOST, port=API_PORT)
