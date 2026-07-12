from vendor_dd.surfaces.api.trend import TrendPoint, bucket_monthly


def test_one_row_per_month_passes_through():
    rows = [("2026-05-01", 8), ("2026-06-15", 6)]
    assert bucket_monthly(rows) == [
        TrendPoint(month="2026-05", score=8),
        TrendPoint(month="2026-06", score=6),
    ]


def test_multiple_days_in_a_month_keep_the_latest():
    rows = [("2026-07-01", 5), ("2026-07-20", 8), ("2026-07-10", 6)]
    assert bucket_monthly(rows) == [TrendPoint(month="2026-07", score=8)]


def test_empty_input_gives_empty_output():
    assert bucket_monthly([]) == []


def test_output_is_sorted_by_month_regardless_of_input_order():
    rows = [("2026-07-01", 5), ("2026-05-01", 8), ("2026-06-01", 6)]
    assert [p.month for p in bucket_monthly(rows)] == ["2026-05", "2026-06", "2026-07"]
