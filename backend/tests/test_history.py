from datetime import date

import pytest

from vendor_dd.engine.history import ScoreHistory


def _hist(tmp_path, today=date(2026, 7, 11)):
    return ScoreHistory(tmp_path / "db.sqlite", clock=lambda: today)


def test_records_and_reads_back_a_dimension_score(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    assert h.scores_for("acme.com") == {"legal": (8, "2026-07-11")}


def test_same_day_rerun_replaces_the_row(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    h.record("acme.com", "legal", 4)   # same (key, dim, day) -> REPLACE
    assert h.scores_for("acme.com")["legal"] == (4, "2026-07-11")


def test_distinct_days_are_separate_rows(tmp_path):
    early = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 5, 1))
    early.record("acme.com", "legal", 8)
    late = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 7, 11))
    late.record("acme.com", "legal", 4)
    # scores_for returns the LATEST row per dimension...
    assert late.scores_for("acme.com")["legal"] == (4, "2026-07-11")
    # ...and previous() returns the prior distinct-day score
    assert late.previous("acme.com", "legal") == (8, "2026-05-01")


def test_no_previous_when_only_one_day(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    assert h.previous("acme.com", "legal") is None


def test_verdict_is_a_valid_dimension(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "verdict", 6)
    assert h.scores_for("acme.com")["verdict"] == (6, "2026-07-11")


def test_rejects_unknown_dimension(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "bogus", 5)


def test_rejects_out_of_range_score(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "legal", 11)


def test_snapshot_is_not_a_valid_history_dimension(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "snapshot", 5)


def test_series_for_returns_every_recorded_day_oldest_first(tmp_path):
    early = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 5, 1))
    early.record("acme.com", "legal", 8)
    late = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 7, 11))
    late.record("acme.com", "legal", 4)
    assert late.series_for("acme.com", "legal") == [
        ("2026-05-01", 8), ("2026-07-11", 4),
    ]


def test_series_for_is_empty_when_nothing_recorded(tmp_path):
    h = _hist(tmp_path)
    assert h.series_for("acme.com", "legal") == []


def test_series_for_only_returns_the_requested_vendor_and_dimension(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    h.record("acme.com", "safety", 3)
    h.record("other.com", "legal", 5)
    assert h.series_for("acme.com", "legal") == [("2026-07-11", 8)]
