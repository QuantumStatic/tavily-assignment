from __future__ import annotations

import contextvars
import queue
import threading

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.events import EntityResolved
from vendor_dd.engine.pipeline import ReportEngine
from vendor_dd.engine.schemas import Dimension, EntityCard, Section
from vendor_dd.engine.synthesis import assemble_verdict
from vendor_dd.surfaces.api.schemas import (
    DimensionScore, ProjectDetail, ProjectIn, ProjectOut, VendorIn, VendorOut,
    VendorReport, VendorSummary,
)
from vendor_dd.surfaces.api.sse import to_sse_frame
from vendor_dd.surfaces.api.store import Store, Vendor

router = APIRouter()

EXPECTED_SECTIONS = len([d for d in Dimension if d is not Dimension.SNAPSHOT])

_STREAM_DONE = object()   # queue sentinel: report generation finished


def _store(request: Request) -> Store:
    return request.app.state.store


def _cache(request: Request) -> SQLiteCache:
    return SQLiteCache(request.app.state.deps.cache_path)


def _report_sections(cache: SQLiteCache, vendor_key: str):
    """Parsed sections for a vendor (excludes the SNAPSHOT entity slot), keyed by dimension."""
    stored = cache.all_sections(vendor_key)
    return {d: (Section.model_validate(c), ts)
            for d, (c, ts) in stored.items() if d is not Dimension.SNAPSHOT}


def _summarize(vendor: Vendor, cache: SQLiteCache) -> VendorSummary:
    parsed = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else {}
    if not parsed:
        return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                             generated=False, verdict_score=None, verdict_reasoning=None,
                             dimensions=[], sections_present=0, sections_expected=EXPECTED_SECTIONS)
    sections = [sec for sec, _ in parsed.values()]
    score, reasoning = assemble_verdict(sections)
    dims = [DimensionScore(dimension=d, score=sec.score, as_of=ts.isoformat())
            for d, (sec, ts) in parsed.items()]
    return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                         generated=True, verdict_score=score, verdict_reasoning=reasoning,
                         dimensions=dims, sections_present=len(parsed), sections_expected=EXPECTED_SECTIONS)


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    p = _store(request).create_project(body.name)
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request):
    return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at)
            for p in _store(request).list_projects()]


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, request: Request):
    store = _store(request)
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    cache = _cache(request)
    try:
        vendors = [_summarize(v, cache) for v in store.list_vendors(project_id)]
    finally:
        cache.close()
    return ProjectDetail(id=project.id, name=project.name, created_at=project.created_at,
                         vendors=vendors)


@router.post("/projects/{project_id}/vendors", response_model=VendorOut)
def add_vendor(project_id: int, body: VendorIn, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    v = store.add_vendor(project_id, body.name)
    return VendorOut(id=v.id, project_id=v.project_id, name=v.name,
                     vendor_key=v.vendor_key, created_at=v.created_at)


@router.delete("/vendors/{vendor_id}")
def remove_vendor(vendor_id: int, request: Request):
    store = _store(request)
    if store.get_vendor(vendor_id) is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    store.remove_vendor(vendor_id)
    return {"ok": True}


@router.get("/vendors/{vendor_id}/report", response_model=VendorReport)
def get_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    cache = _cache(request)
    try:
        parsed = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else {}
        if not parsed:
            return VendorReport(generated=False, vendor_key=vendor.vendor_key, entity=None,
                                verdict_score=None, verdict_reasoning=None, sections=[],
                                sections_present=0, sections_expected=EXPECTED_SECTIONS)
        sections = [sec for sec, _ in parsed.values()]
        score, reasoning = assemble_verdict(sections)
        entity_raw = cache.get(vendor.name.strip().lower(), Dimension.SNAPSHOT)
        entity = EntityCard.model_validate(entity_raw) if entity_raw else None
    finally:
        cache.close()
    return VendorReport(generated=True, vendor_key=vendor.vendor_key, entity=entity,
                        verdict_score=score, verdict_reasoning=reasoning, sections=sections,
                        sections_present=len(parsed), sections_expected=EXPECTED_SECTIONS)


@router.get("/vendors/{vendor_id}/report/stream")
def stream_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    project = store.get_project(vendor.project_id)
    session_id = project.session_id if project else None
    engine = ReportEngine(request.app.state.deps, mode="parallel", session_id=session_id)

    # Decouple the WORK from the STREAM. Generation runs in a background thread and drains
    # to completion regardless of whether the client is still listening; the pipeline
    # caches each section as it lands. So if the SSE breaks (e.g. the user switches
    # projects) the report still finishes and is fully cached — coming back shows it done.
    # The SSE below just tails the thread's events via a queue.
    events: queue.Queue = queue.Queue()

    def generate() -> None:
        try:
            for ev in engine.iter_events(vendor.name):
                if isinstance(ev, EntityResolved):
                    key = (ev.entity.domain or ev.entity.name).strip().lower()
                    store.set_vendor_key(vendor_id, key)  # backfill so the read model can join
                events.put(ev)
        finally:
            events.put(_STREAM_DONE)

    # copy_context so the request's correlation id follows the work into the thread
    ctx = contextvars.copy_context()
    threading.Thread(target=lambda: ctx.run(generate),
                     name=f"report-{vendor_id}", daemon=True).start()

    def event_source():
        while True:
            ev = events.get()
            if ev is _STREAM_DONE:
                break
            yield to_sse_frame(ev)

    return EventSourceResponse(event_source())
