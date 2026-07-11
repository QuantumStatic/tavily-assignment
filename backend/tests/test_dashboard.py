from vendor_dd.surfaces.api.dashboard import VendorRow, compute_dashboard
from vendor_dd.engine.schemas import Dimension


def _row(id, name, pid, pname, key, verdict, dims, chosen=False):
    return VendorRow(vendor_id=id, name=name, project_id=pid, project_name=pname,
                     vendor_key=key, chosen=chosen, verdict=verdict, dims=dims)


FULL = {"legal": 7, "safety": 4, "financial": 6, "backlog": 8, "certifications": 9, "news": 5}


def test_counts_and_coverage():
    rows = [_row(1, "A", 1, "P", "a.com", 8, FULL),
            _row(2, "B", 1, "P", "b.com", None, {})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.vendors_total == 2 and d.vendors_generated == 1


def test_avg_verdict_only_over_generated():
    rows = [_row(1, "A", 1, "P", "a.com", 8, FULL),
            _row(2, "B", 1, "P", "b.com", 4, FULL),
            _row(3, "C", 1, "P", "c.com", None, {})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.avg_verdict == 6.0   # (8+4)/2, the None ignored


def test_risk_bands_boundaries():
    rows = [_row(1, "A", 1, "P", "a.com", 3, FULL),   # high (<=3)
            _row(2, "B", 1, "P", "b.com", 4, FULL),   # watch
            _row(3, "C", 1, "P", "c.com", 6, FULL),   # watch
            _row(4, "D", 1, "P", "d.com", 7, FULL)]   # cleared (>=7)
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert (d.risk_high, d.risk_watch, d.risk_cleared) == (1, 2, 1)


def test_verdict_histogram_indexes_by_score():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL),
            _row(2, "B", 1, "P", "b.com", 7, FULL),
            _row(3, "C", 1, "P", "c.com", 2, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert len(d.verdict_histogram) == 11
    assert d.verdict_histogram[7] == 2 and d.verdict_histogram[2] == 1


def test_weakest_dimension_is_lowest_average():
    rows = [_row(1, "A", 1, "P", "a.com", 6, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.weakest_dimension == Dimension.SAFETY   # 4 is the min in FULL


def test_weakest_dimension_ties_break_by_enum_order():
    dims = {"legal": 4, "safety": 4, "financial": 9, "backlog": 9,
            "certifications": 9, "news": 9}
    rows = [_row(1, "A", 1, "P", "a.com", 6, dims)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.weakest_dimension == Dimension.LEGAL   # legal precedes safety in enum order


def test_duplicate_domain_counts_once_for_domain_stats():
    rows = [_row(1, "Voith", 1, "P", "voith.com", 8, FULL),
            _row(2, "Voith Hydro", 2, "Q", "voith.com", 8, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=2, projects_selected=2)
    # domain-level: one entry in the histogram, but vendors_total counts both rows
    assert d.vendors_total == 2
    assert sum(d.verdict_histogram) == 1


def test_red_flag_low_dimension_named():
    rows = [_row(1, "Fluor", 1, "P", "fluor.com", 6,
                 {**FULL, "safety": 2})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    flags = [f for f in d.red_flags if f.kind == "dimension"]
    assert any(f.ref.name == "Fluor" and f.dimension == Dimension.SAFETY and f.score == 2
               for f in flags)


def test_delivery_risk_combo_triggers_only_when_both_weak():
    weak = _row(1, "Aecon", 1, "P", "aecon.com", 5,
                {**FULL, "backlog": 4, "financial": 3})
    ok = _row(2, "Bech", 1, "P", "bech.com", 6, {**FULL, "backlog": 4, "financial": 5})
    d = compute_dashboard([weak, ok], {}, {}, {}, projects_total=1, projects_selected=1)
    combos = [f for f in d.red_flags if f.kind == "delivery_risk"]
    assert [f.ref.name for f in combos] == ["Aecon"]


def test_shortlist_top_three_by_verdict_with_delta():
    rows = [_row(i, f"V{i}", 1, "P", f"v{i}.com", v, FULL)
            for i, v in [(1, 9), (2, 8), (3, 7), (4, 6)]]
    d = compute_dashboard(rows, {"v1.com": 6}, {}, {}, projects_total=1, projects_selected=1)
    assert [e.ref.name for e in d.shortlist] == ["V1", "V2", "V3"]
    assert d.shortlist[0].previous_verdict == 6


def test_most_trusted_ranks_by_chosen_count():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL),
            _row(2, "B", 1, "P", "b.com", 9, FULL)]
    d = compute_dashboard(rows, {}, {"a.com": 4, "b.com": 1}, {"a.com": 5, "b.com": 2},
                          projects_total=1, projects_selected=1)
    assert d.most_trusted[0].name == "A" and d.most_trusted[0].chosen_count == 4
    assert d.most_trusted[0].project_count == 5


def test_source_counts_passed_through():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1,
                          independent_sources=8, self_reported_sources=2)
    assert (d.independent_sources, d.self_reported_sources) == (8, 2)


def test_empty_selection_is_all_zeros_no_crash():
    d = compute_dashboard([], {}, {}, {}, projects_total=3, projects_selected=0)
    assert d.avg_verdict is None and d.weakest_dimension is None
    assert d.risk_high == 0 and d.shortlist == [] and sum(d.verdict_histogram) == 0
