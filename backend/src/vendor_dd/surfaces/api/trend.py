from __future__ import annotations

from pydantic import BaseModel


class VendorOption(BaseModel):
    vendor_key: str
    name: str


class TrendPoint(BaseModel):
    month: str    # "YYYY-MM"
    score: int


class TrendSeries(BaseModel):
    vendor_key: str
    name: str
    points: list[TrendPoint]


class TrendResponse(BaseModel):
    dimension: str
    series: list[TrendSeries]


def bucket_monthly(rows: list[tuple[str, int]]) -> list[TrendPoint]:
    """Collapse daily (recorded_on, score) rows into one point per calendar month —
    the score from the latest day recorded within that month. Rows need not be sorted."""
    latest_by_month: dict[str, tuple[str, int]] = {}
    for recorded_on, score in rows:
        month = recorded_on[:7]
        prev = latest_by_month.get(month)
        if prev is None or recorded_on > prev[0]:
            latest_by_month[month] = (recorded_on, score)
    return [TrendPoint(month=m, score=latest_by_month[m][1]) for m in sorted(latest_by_month)]
