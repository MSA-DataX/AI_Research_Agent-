from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ValidationReport(BaseModel):
    confidence: int = 0
    label: str = ""
    total: int = 0
    supported: int = 0
    n_sources: int = 0
    supporting_sources: List[str] = Field(default_factory=list)
    per_item: Optional[List[dict]] = None
    per_row: Optional[List[dict]] = None
    methods_used: Optional[List[str]] = None
    methods_summary: Optional[Dict[str, dict]] = None
    mode: Optional[str] = None
    verified_at: Optional[str] = None
    dedup: Optional[Dict[str, int]] = None


class ResearchBoxOut(BaseModel):
    id: str
    task: str
    status: str
    sources: List[str] = Field(default_factory=list)
    visited_sources: List[str] = Field(default_factory=list)
    extracted_data: Optional[Any] = None
    entities: Dict[str, Any] = Field(default_factory=dict)
    validation: Optional[Dict[str, Any]] = None
    iterations: int = 0
    output_fields: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class TaskIn(BaseModel):
    task: str = Field(..., min_length=1, description="Task prompt for the agent")
    output_fields: Optional[List[str]] = Field(
        default=None,
        description="Required field names per item (e.g. ['name','date','source_url']).",
    )


class RunResult(BaseModel):
    rb_id: str
    result: Optional[Any] = None
    validation: Optional[Dict[str, Any]] = None
    visited_sources: Optional[List[str]] = None
    sources_seen: Optional[List[str]] = None
    trace_log: Optional[str] = None
    error: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    kind: str
    status: str = Field(..., description="pending | running | completed | error | cancelled")
    rb_id: Optional[str] = None
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class JobAck(BaseModel):
    job_id: str
    status: str
    poll_url: str


class PagedResearchBoxes(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[ResearchBoxOut]


class PagedJobs(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[JobStatus]


class ValidationMethods(BaseModel):
    methods: Dict[str, str]


class MessageOut(BaseModel):
    message: str


class DeletedOut(BaseModel):
    deleted: str
