from __future__ import annotations

import contextvars
import sqlite3
import threading

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.events import EntityResolved
from vendor_dd.engine.pipeline import ReportEngine
from vendor_dd.engine.schemas import Dimension, EntityCard, Section
from vendor_dd.engine.synthesis import assemble_verdict
from vendor_dd.surfaces.api.runs import DONE, RunRegistry
from vendor_dd.surfaces.api.schemas import (
    DimensionScore, ProjectDetail, ProjectIn, ProjectOut, VendorIn, VendorOut,
    VendorReport, VendorSummary,
)
from vendor_dd.surfaces.api.sse import to_sse_frame
from vendor_dd.surfaces.api.store import Store, Vendor

router = APIRouter()

EXPECTED_SECTIONS = len([d for d in Dimension if d is not Dimension.SNAPSHOT])


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


def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    return name


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    p = _store(request).create_project(_clean_name(body.name))
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
    # Flag domain-level duplicates: name-based dedup (see add_vendor) can't catch
    # "Voith" vs "Voith Hydro" — both resolve to the same domain once entity
    # resolution runs, and silently share one cached report. list_vendors/_summarize
    # iterate in id order (oldest first), so the first vendor to claim a vendor_key
    # is the canonical one; later vendors sharing it get flagged, purely informational.
    seen_keys: dict[str, str] = {}
    for summ in vendors:
        if not summ.vendor_key:
            continue
        if summ.vendor_key in seen_keys:
            summ.duplicate_of = seen_keys[summ.vendor_key]
        else:
            seen_keys[summ.vendor_key] = summ.name
    return ProjectDetail(id=project.id, name=project.name, created_at=project.created_at,
                         vendors=vendors)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    store.remove_project(project_id)
    return {"ok": True}


def _vendor_out(v: Vendor, *, existed: bool) -> VendorOut:
    return VendorOut(id=v.id, project_id=v.project_id, name=v.name,
                     vendor_key=v.vendor_key, created_at=v.created_at, existed=existed)


@router.post("/projects/{project_id}/vendors", response_model=VendorOut)
def add_vendor(project_id: int, body: VendorIn, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    name = _clean_name(body.name)
    # Idempotent add: the same vendor name in this project returns the existing row
    # (no duplicate, no re-triggered research) instead of erroring or inserting again.
    existing = store.find_vendor(project_id, name)
    if existing is not None:
        return _vendor_out(existing, existed=True)
    try:
        v = store.add_vendor(project_id, name)
    except sqlite3.IntegrityError:
        # lost a race with a concurrent identical add — return the winner's row
        winner = store.find_vendor(project_id, name)
        if winner is None:   # can't happen: the constraint that fired proves the row exists
            raise HTTPException(status_code=409, detail="vendor already added")
        return _vendor_out(winner, existed=True)
    return _vendor_out(v, existed=False)


@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
def rename_vendor(vendor_id: int, body: VendorIn, request: Request):
    store = _store(request)
    name = _clean_name(body.name)
    try:
        v = store.rename_vendor(vendor_id, name)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409,
                            detail="a vendor with that name is already in this project")
    if v is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    return _vendor_out(v, existed=False)


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
        # snapshot is keyed by domain (vendor_key), stable across renames; we only reach
        # here when sections exist, which requires vendor_key to be set.
        entity_raw = cache.get(vendor.vendor_key, Dimension.SNAPSHOT)
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

    # Single-flight per vendor: only the caller that CREATES the run starts a
    # generation thread. A second stream (another tab, a refresh) subscribes to
    # the same run — history replayed, then live events — instead of kicking off
    # a duplicate generation that would double Tavily/LLM spend and race the cache.
    registry: RunRegistry = request.app.state.runs
    run, created = registry.get_or_create(vendor_id)

    if created:
        project = store.get_project(vendor.project_id)
        session_id = project.session_id if project else None
        engine = ReportEngine(request.app.state.deps, mode="parallel", session_id=session_id,
                              domain_locks=request.app.state.domain_locks)

        # Decouple the WORK from the STREAM. Generation runs in a background thread
        # and drains to completion regardless of listeners; the pipeline caches each
        # section as it lands, so a broken SSE still yields a finished, cached report.
        def generate() -> None:
            key: str | None = None
            try:
                for ev in engine.iter_events(vendor.name):
                    if isinstance(ev, EntityResolved):
                        key = (ev.entity.domain or ev.entity.name).strip().lower()
                        if store.get_vendor(vendor_id) is not None:
                            store.set_vendor_key(vendor_id, key)  # backfill for the read model
                    run.publish(ev)
            finally:
                # The vendor may have been deleted while we worked. Its DELETE already
                # evicted the cache — but we kept writing sections afterwards. Now that
                # every write is done, evict again (refcount rules still apply, so a
                # same-key vendor in another project keeps its report).
                if store.get_vendor(vendor_id) is None:
                    store.evict_unreferenced(vendor.name, key)
                registry.remove(vendor_id)
                run.finish()

        # copy_context so the request's correlation id follows the work into the thread
        ctx = contextvars.copy_context()
        threading.Thread(target=lambda: ctx.run(generate),
                         name=f"report-{vendor_id}", daemon=True).start()

    subscription = run.subscribe()

    def event_source():
        while True:
            ev = subscription.get()
            if ev is DONE:
                break
            yield to_sse_frame(ev)

    return EventSourceResponse(event_source())
