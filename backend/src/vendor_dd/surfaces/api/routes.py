from __future__ import annotations

import contextvars
import sqlite3
import threading

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.events import EntityResolved
from vendor_dd.engine.history import HISTORY_DIMENSIONS
from vendor_dd.engine.pipeline import ReportEngine
from vendor_dd.engine.schemas import Dimension, EntityCard, Section, SourceType
from vendor_dd.engine.synthesis import assemble_verdict
from vendor_dd.surfaces.api.dashboard import DashboardStats, VendorRow, compute_dashboard
from vendor_dd.surfaces.api.runs import DONE, RunRegistry
from vendor_dd.surfaces.api.schemas import (
    ChosenIn, DimensionDelta, DimensionScore, ProjectDetail, ProjectIn, ProjectOut, VendorIn,
    VendorOut, VendorReport, VendorSummary,
)
from vendor_dd.surfaces.api.sse import to_sse_frame
from vendor_dd.surfaces.api.store import Store, Vendor
from vendor_dd.surfaces.api.trend import TrendResponse, TrendSeries, VendorOption, bucket_monthly

router = APIRouter()

EXPECTED_SECTIONS = len([d for d in Dimension if d is not Dimension.SNAPSHOT])


def _store(request: Request) -> Store:
    return request.app.state.store


def _cache(request: Request) -> SQLiteCache:
    return SQLiteCache(request.app.state.deps.cache_path)


def _report_sections(cache: SQLiteCache, vendor_key: str):
    """Sections keyed by dimension (excluding the SNAPSHOT entity slot), PLUS the raw
    entity-snapshot content — both read TTL-free from one all_sections call. Reading the
    snapshot here (rather than a separate TTL-applied cache.get) keeps the report header
    consistent with the also-TTL-free sections: an old-but-displayed report shows its
    company card instead of blanking it out at the 30-day snapshot TTL."""
    stored = cache.all_sections(vendor_key)
    sections = {d: (Section.model_validate(c), ts)
                for d, (c, ts) in stored.items() if d is not Dimension.SNAPSHOT}
    snap = stored.get(Dimension.SNAPSHOT)
    return sections, (snap[0] if snap else None)


def _summarize(vendor: Vendor, cache: SQLiteCache) -> VendorSummary:
    parsed, _ = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else ({}, None)
    if not parsed:
        return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                             generated=False, verdict_score=None, verdict_reasoning=None,
                             dimensions=[], sections_present=0, sections_expected=EXPECTED_SECTIONS,
                             chosen=vendor.chosen)
    sections = [sec for sec, _ in parsed.values()]
    score, reasoning = assemble_verdict(sections)
    dims = [DimensionScore(dimension=d, score=sec.score, as_of=ts.isoformat())
            for d, (sec, ts) in parsed.items()]
    return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                         generated=True, verdict_score=score, verdict_reasoning=reasoning,
                         dimensions=dims, sections_present=len(parsed), sections_expected=EXPECTED_SECTIONS,
                         chosen=vendor.chosen)


def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    return name


def _parse_project_ids(projects: str | None) -> set[int] | None:
    """None -> all projects. Else the set of valid ints; unknown ids filtered later.
    Raises 422 on non-integer tokens."""
    if projects is None or projects.strip() == "":
        return None
    try:
        return {int(tok) for tok in projects.split(",") if tok.strip() != ""}
    except ValueError:
        raise HTTPException(status_code=422, detail="projects must be comma-separated integers")


def _count_sources(sections: dict) -> tuple[int, int]:
    independent = self_reported = 0
    for sec, _ in sections.values():
        for f in sec.findings:
            if f.citation.source_type is SourceType.INDEPENDENT:
                independent += 1
            else:
                self_reported += 1
    return independent, self_reported


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    p = _store(request).create_project(_clean_name(body.name))
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request):
    store = _store(request)
    counts = store.vendor_counts()
    return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at,
                       vendor_count=counts.get(p.id, 0))
            for p in store.list_projects()]


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
                     vendor_key=v.vendor_key, created_at=v.created_at, existed=existed,
                     chosen=v.chosen)


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


@router.patch("/vendors/{vendor_id}/chosen", response_model=VendorOut)
def set_chosen(vendor_id: int, body: ChosenIn, request: Request):
    v = _store(request).set_chosen(vendor_id, body.chosen)
    if v is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    return _vendor_out(v, existed=True)


@router.get("/stats", response_model=DashboardStats)
def get_stats(request: Request, projects: str | None = None):
    store = _store(request)
    history = request.app.state.history

    wanted = _parse_project_ids(projects)
    all_projects = store.list_projects()
    projects_total = len(all_projects)
    valid_ids = {p.id for p in all_projects}
    selected_ids = valid_ids if wanted is None else (wanted & valid_ids)
    name_by_id = {p.id: p.name for p in all_projects}

    cache = _cache(request)
    try:
        rows: list[VendorRow] = []
        independent = self_reported = 0
        previous_verdict: dict[str, int] = {}
        # trust counts span ALL projects (by domain), so they're tallied for every
        # vendor before the selected-projects filter narrows the dashboard rows.
        chosen_counts: dict[str, int] = {}
        project_counts: dict[str, int] = {}
        for v in store.list_all_vendors():
            if v.vendor_key:
                project_counts[v.vendor_key] = project_counts.get(v.vendor_key, 0) + 1
                if v.chosen:
                    chosen_counts[v.vendor_key] = chosen_counts.get(v.vendor_key, 0) + 1
            if v.project_id not in selected_ids:
                continue
            dims: dict[str, int] = {}
            verdict: int | None = None
            if v.vendor_key:
                parsed, _snap = _report_sections(cache, v.vendor_key)
                if parsed:
                    dims = {d.value: sec.score for d, (sec, _ts) in parsed.items()}
                    score, _reasoning = assemble_verdict([sec for sec, _ in parsed.values()])
                    verdict = score
                    ind, self_r = _count_sources(parsed)
                    independent += ind
                    self_reported += self_r
                    prev = history.previous(v.vendor_key, "verdict")
                    if prev is not None:
                        previous_verdict[v.vendor_key] = prev[0]
            rows.append(VendorRow(
                vendor_id=v.id, name=v.name, project_id=v.project_id,
                project_name=name_by_id.get(v.project_id, ""), vendor_key=v.vendor_key,
                chosen=v.chosen, verdict=verdict, dims=dims))
    finally:
        cache.close()

    return compute_dashboard(
        rows, previous_verdict, chosen_counts, project_counts,
        projects_total=projects_total, projects_selected=len(selected_ids),
        independent_sources=independent, self_reported_sources=self_reported)


def _canonical_names(store: Store) -> dict[str, str]:
    """First-seen display name per vendor_key, across every project — the same
    canonical-name convention used elsewhere for domain-level dedup."""
    names: dict[str, str] = {}
    for v in store.list_all_vendors():
        if v.vendor_key and v.vendor_key not in names:
            names[v.vendor_key] = v.name
    return names


@router.get("/vendor-options", response_model=list[VendorOption])
def get_vendor_options(request: Request):
    """Every resolved vendor identity in the portfolio, for the trend chart's vendor
    picker — spans all projects, not just an Overview selection."""
    names = _canonical_names(_store(request))
    return [VendorOption(vendor_key=k, name=n) for k, n in names.items()]


@router.get("/trend", response_model=TrendResponse)
def get_trend(request: Request, vendor_keys: str = "", dimension: str = "verdict"):
    if dimension not in HISTORY_DIMENSIONS:
        raise HTTPException(status_code=422, detail="unknown dimension")
    keys = [k for k in vendor_keys.split(",") if k.strip()]
    store = _store(request)
    history = request.app.state.history
    names = _canonical_names(store)
    series = [
        TrendSeries(vendor_key=key, name=names.get(key, key),
                   points=bucket_monthly(history.series_for(key, dimension)))
        for key in keys
    ]
    return TrendResponse(dimension=dimension, series=series)


@router.get("/vendors/{vendor_id}/report", response_model=VendorReport)
def get_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    history = request.app.state.history
    cache = _cache(request)
    try:
        parsed, entity_raw = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else ({}, None)
        if not parsed:
            return VendorReport(generated=False, vendor_key=vendor.vendor_key, entity=None,
                                verdict_score=None, verdict_reasoning=None, sections=[],
                                sections_present=0, sections_expected=EXPECTED_SECTIONS,
                                chosen_count=0, projects_count=0, dimension_deltas={})
        sections = [sec for sec, _ in parsed.values()]
        score, reasoning = assemble_verdict(sections)
        # entity snapshot is keyed by domain (stable across renames) and read TTL-free,
        # consistent with the sections shown alongside it.
        entity = EntityCard.model_validate(entity_raw) if entity_raw else None

        # Trust counts span ALL projects (not just this vendor's own project), keyed
        # by the resolved vendor_key/domain — the same vendor may have been added
        # independently to several projects.
        chosen_count = projects_count = 0
        deltas: dict[str, DimensionDelta | None] = {}
        if vendor.vendor_key:
            for other in store.list_all_vendors():
                if other.vendor_key == vendor.vendor_key:
                    projects_count += 1
                    if other.chosen:
                        chosen_count += 1
            for d, (_sec, _ts) in parsed.items():
                prev = history.previous(vendor.vendor_key, d.value)
                deltas[d.value] = (DimensionDelta(score=prev[0], recorded_on=prev[1])
                                   if prev else None)
    finally:
        cache.close()
    return VendorReport(generated=True, vendor_key=vendor.vendor_key, entity=entity,
                        verdict_score=score, verdict_reasoning=reasoning, sections=sections,
                        sections_present=len(parsed), sections_expected=EXPECTED_SECTIONS,
                        chosen_count=chosen_count, projects_count=projects_count,
                        dimension_deltas=deltas)


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
                              domain_locks=request.app.state.domain_locks,
                              history=request.app.state.history)

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
