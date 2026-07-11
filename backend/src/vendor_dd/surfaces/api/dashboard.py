from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from vendor_dd.engine.schemas import Dimension

# scored dimensions in enum order (excludes SNAPSHOT); ties in "weakest" break by this order
SCORED_DIMS: list[Dimension] = [d for d in Dimension if d is not Dimension.SNAPSHOT]

HIGH_MAX = 3      # verdict <= 3 -> high risk
CLEARED_MIN = 7   # verdict >= 7 -> cleared
RED_FLAG_MAX = 3  # any dimension <= 3 -> named red flag
DELIVERY_MAX = 4  # backlog <= 4 AND financial <= 4 -> delivery-risk combo


@dataclass
class VendorRow:
    vendor_id: int
    name: str
    project_id: int
    project_name: str
    vendor_key: str | None
    chosen: bool
    verdict: int | None
    dims: dict[str, int]


class VendorRef(BaseModel):
    vendor_id: int
    name: str
    project_id: int
    project_name: str


class ShortlistEntry(BaseModel):
    ref: VendorRef
    verdict: int
    previous_verdict: int | None = None


class RedFlag(BaseModel):
    ref: VendorRef
    kind: Literal["dimension", "delivery_risk"]
    dimension: Dimension | None = None
    score: int
    detail: str


class TrustedVendor(BaseModel):
    name: str
    vendor_key: str
    chosen_count: int
    project_count: int
    verdict: int | None = None


class DashboardStats(BaseModel):
    projects_total: int
    projects_selected: int
    vendors_total: int
    vendors_generated: int
    avg_verdict: float | None
    risk_high: int
    risk_watch: int
    risk_cleared: int
    independent_sources: int
    self_reported_sources: int
    verdict_histogram: list[int]
    dimension_avgs: dict[Dimension, float]
    dimension_histograms: dict[Dimension, list[int]]
    weakest_dimension: Dimension | None
    weakest_low_count: int
    shortlist: list[ShortlistEntry]
    red_flags: list[RedFlag]
    most_trusted: list[TrustedVendor]


def _ref(v: VendorRow) -> VendorRef:
    return VendorRef(vendor_id=v.vendor_id, name=v.name,
                     project_id=v.project_id, project_name=v.project_name)


def compute_dashboard(
    vendors: list[VendorRow],
    previous_verdict: dict[str, int],
    chosen_counts: dict[str, int],
    project_counts: dict[str, int],
    *,
    projects_total: int,
    projects_selected: int,
    independent_sources: int = 0,
    self_reported_sources: int = 0,
) -> DashboardStats:
    # Domain-level dedupe: collapse same vendor_key to the earliest (first-seen) row for
    # all domain stats; row-level counts (vendors_total) still count every row.
    canonical: dict[str, VendorRow] = {}
    for v in vendors:
        key = v.vendor_key
        if key and key not in canonical:
            canonical[key] = v
    unique = list(canonical.values())
    generated = [v for v in unique if v.verdict is not None]

    verdicts = [v.verdict for v in generated]
    avg_verdict = round(sum(verdicts) / len(verdicts), 1) if verdicts else None

    risk_high = sum(1 for s in verdicts if s <= HIGH_MAX)
    risk_cleared = sum(1 for s in verdicts if s >= CLEARED_MIN)
    risk_watch = len(verdicts) - risk_high - risk_cleared

    verdict_histogram = [0] * 11
    for s in verdicts:
        verdict_histogram[s] += 1

    dimension_avgs: dict[Dimension, float] = {}
    dimension_histograms: dict[Dimension, list[int]] = {}
    for dim in SCORED_DIMS:
        vals = [v.dims[dim.value] for v in generated if dim.value in v.dims]
        hist = [0] * 11
        for s in vals:
            hist[s] += 1
        dimension_histograms[dim] = hist
        if vals:
            dimension_avgs[dim] = round(sum(vals) / len(vals), 1)

    weakest_dimension = None
    weakest_low_count = 0
    if dimension_avgs:
        # min average; ties broken by SCORED_DIMS order (min() is stable over the ordered list)
        weakest_dimension = min(
            (d for d in SCORED_DIMS if d in dimension_avgs),
            key=lambda d: dimension_avgs[d])
        weakest_low_count = sum(
            1 for v in generated
            if v.dims.get(weakest_dimension.value, 99) <= RED_FLAG_MAX)

    shortlist = [
        ShortlistEntry(ref=_ref(v), verdict=v.verdict,
                       previous_verdict=previous_verdict.get(v.vendor_key or ""))
        for v in sorted(generated, key=lambda v: v.verdict, reverse=True)[:3]
    ]

    red_flags: list[RedFlag] = []
    for v in generated:
        low = [(d, v.dims[d.value]) for d in SCORED_DIMS
               if v.dims.get(d.value, 99) <= RED_FLAG_MAX]
        for dim, score in low:
            red_flags.append(RedFlag(ref=_ref(v), kind="dimension", dimension=dim,
                                     score=score, detail=f"{dim.value.title()} {score}"))
        bl, fin = v.dims.get("backlog"), v.dims.get("financial")
        if bl is not None and fin is not None and bl <= DELIVERY_MAX and fin <= DELIVERY_MAX:
            red_flags.append(RedFlag(ref=_ref(v), kind="delivery_risk", dimension=None,
                                     score=min(bl, fin),
                                     detail=f"Backlog {bl} · Financial {fin}"))

    trusted = [
        TrustedVendor(name=v.name, vendor_key=v.vendor_key or "",
                      chosen_count=chosen_counts.get(v.vendor_key or "", 0),
                      project_count=project_counts.get(v.vendor_key or "", 0),
                      verdict=v.verdict)
        for v in unique if v.vendor_key
    ]
    trusted = [t for t in trusted if t.chosen_count > 0]
    trusted.sort(key=lambda t: (t.chosen_count, t.verdict or 0), reverse=True)

    return DashboardStats(
        projects_total=projects_total, projects_selected=projects_selected,
        vendors_total=len(vendors), vendors_generated=len(generated),
        avg_verdict=avg_verdict,
        risk_high=risk_high, risk_watch=risk_watch, risk_cleared=risk_cleared,
        independent_sources=independent_sources, self_reported_sources=self_reported_sources,
        verdict_histogram=verdict_histogram,
        dimension_avgs=dimension_avgs, dimension_histograms=dimension_histograms,
        weakest_dimension=weakest_dimension, weakest_low_count=weakest_low_count,
        shortlist=shortlist, red_flags=red_flags, most_trusted=trusted[:3],
    )
