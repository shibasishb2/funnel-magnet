"""Tests for dashboard display logic."""

import pytest

from funnel_magnet.diagnose import Status, diagnose_totals, run_diagnosis
from funnel_magnet.metrics import Totals
from funnel_magnet.periods import MONTHLY, WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import charts, render, view

SAMPLES = view.load_samples()
PRIOR = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0, 1600.0, 400.0, 100.0)


def diagnose(index, granularity=WEEKLY, **options):
    data = load_meta_csv(SAMPLES[index].path).data
    return run_diagnosis(data, granularity, **options)


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def find(root, metric):
    return next(node for node in walk(root) if node.metric == metric)


def test_all_samples_are_listed_with_readable_names():
    names = [sample.name for sample in SAMPLES]

    assert names[0] == "CPM inflation"
    assert names[1] == "CTR decline"
    assert names[4] == "AOV decline"
    assert len(names) == 7
    assert all(sample.path.exists() for sample in SAMPLES)


@pytest.mark.parametrize(
    "metric, label",
    [
        ("roas", "ROAS"),
        ("link_clicks_to_landing_page_views", "Link clicks → Landing page views"),
        ("landing_page_views_to_add_to_carts", "Landing page views → Add to cart"),
        ("add_to_carts_to_checkouts_initiated", "Add to cart → Checkout"),
        ("checkouts_initiated_to_purchases", "Checkout → Purchase"),
        ("link_clicks_to_add_to_carts", "Link clicks → Add to cart"),
        ("link_clicks_to_purchases", "Link clicks → Purchase"),
    ],
)
def test_metric_labels(metric, label):
    assert view.metric_label(metric) == label


def test_humanize_keeps_everyday_words():
    assert view.humanize("The prior period has zero purchases.") == "The prior period has zero purchases."


@pytest.mark.parametrize(
    "metric, value, currency, expected",
    [
        ("roas", 3.456, None, "3.46"),
        ("cpa", 1234.5, "EUR", "€1,234.50"),
        ("cpa", 12.5, "USD", "$12.50"),
        ("cpa", 12.5, "SEK", "SEK 12.50"),
        ("cpa", 12.5, None, "12.50"),
        ("cvr", 0.0538, None, "5.38%"),
        ("checkouts_initiated_to_purchases", 0.7, None, "70.0%"),
        ("roas", None, None, "n/a"),
        ("roas", 0.0, None, "0.00"),
    ],
)
def test_format_value(metric, value, currency, expected):
    assert view.format_value(metric, value, currency) == expected


def test_format_change_marks_undefined():
    assert view.format_change(0.123) == "+12.3%"
    assert view.format_change(-0.5) == "-50.0%"
    assert view.format_change(None) == "n/a"


def test_kpis_cover_all_seven_metrics_for_both_periods():
    cards = view.kpi_cards(diagnose(0), "EUR")

    assert [card.label for card in cards] == ["ROAS", "AOV", "CPA", "CPC", "CVR", "CPM", "CTR"]
    assert all(card.prior != "n/a" and card.current != "n/a" for card in cards)


@pytest.mark.parametrize(
    "index, expected",
    [
        (0, {"roas", "cpa", "cpc", "cpm"}),
        (1, {"roas", "cpa", "cpc", "ctr"}),
        (2, {"roas", "cpa", "cvr", "link_clicks_to_landing_page_views"}),
        (3, {"roas", "cpa", "cvr", "checkouts_initiated_to_purchases"}),
        (4, {"roas", "aov"}),
        (5, {"roas", "cpa", "cpc", "cpm"}),
        (6, {"roas"}),
    ],
)
@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
def test_tree_highlights_exactly_the_engine_path(index, expected, granularity):
    root = view.build_tree(diagnose(index, granularity))

    assert {node.metric for node in walk(root) if node.on_path} == expected


def test_tree_marks_both_branches_when_both_run():
    current = Totals(1000.0, 100_000.0, 1600.0, 24.0, 1440.0, 1280.0, 320.0, 48.0)
    root = view.build_tree(diagnose_totals(PRIOR, current))

    assert find(root, "cpc").on_path and find(root, "cvr").on_path


def test_main_driver_badge_goes_to_the_engine_top_driver():
    diagnosis = diagnose(0)
    root = view.build_tree(diagnosis)

    assert find(root, diagnosis.adverse_drivers[0].metric).badge == "Main driver"
    assert sum(node.badge is not None for node in walk(root)) == len(view.driver_badges(diagnosis))


def test_masked_problem_labels_the_aov_gain_as_an_offset():
    badges = view.driver_badges(diagnose(5))

    assert badges == {"cpm": "Main driver", "aov": "Offset"}


def test_healthy_control_has_no_badges():
    assert view.driver_badges(diagnose(6)) == {}


def test_tree_without_funnel_data_has_no_stage_nodes():
    prior = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0)
    current = Totals(1000.0, 100_000.0, 2000.0, 40.0, 2400.0)
    root = view.build_tree(diagnose_totals(prior, current))

    assert find(root, "cvr").children == []
    assert find(root, "cvr").on_path


@pytest.mark.parametrize("index", range(7))
def test_waterfall_steps_sum_to_the_current_roas(index):
    diagnosis = diagnose(index)
    steps = view.waterfall_steps(diagnosis)
    roas = diagnosis.changes["roas"]

    assert steps[0].measure == "absolute" and steps[-1].measure == "total"
    assert steps[0].value + sum(step.value for step in steps[1:-1]) == pytest.approx(roas.current)


def test_waterfall_groups_metrics_below_the_threshold():
    labels = [step.label for step in view.waterfall_steps(diagnose(0))]

    assert labels == ["2026-W34", "CPM", "Other (below threshold)", "2026-W35"]


def test_waterfall_is_unavailable_when_contributions_are_undefined():
    current = Totals(1000.0, 100_000.0, 2000.0, 0.0, 0.0, 1600.0, 400.0, 100.0)
    diagnosis = diagnose_totals(PRIOR, current)

    assert view.waterfall_steps(diagnosis) is None
    assert any("not calculated" in line for line in view.build_findings(diagnosis).drivers)


def test_zero_spend_comparison_still_produces_display_values():
    empty = Totals(0.0, 0.0, 0.0, 0.0, 0.0)
    diagnosis = diagnose_totals(PRIOR, empty)
    root = view.build_tree(diagnosis)

    assert root.status is Status.UNDEFINED
    assert root.current == "n/a"
    assert view.waterfall_steps(diagnosis) is None


def test_waterfall_figure_uses_the_steps():
    steps = view.waterfall_steps(diagnose(0))
    trace = charts.contribution_waterfall(steps).data[0]

    assert list(trace.x) == [step.label for step in steps]
    assert list(trace.measure) == [step.measure for step in steps]


def test_findings_separate_observations_from_drivers_and_causes():
    findings = view.build_findings(diagnose(0), "EUR")

    assert findings.observations[0].startswith("ROAS fell 14.3%")
    assert any(line.startswith("CPM contributed") for line in findings.drivers)
    assert any("Competitor activity" in line for line in findings.evidence_needed)


def test_healthy_control_reports_no_drivers():
    findings = view.build_findings(diagnose(6))

    assert "No lowest-level metric changed" in findings.drivers[0]
    assert findings.evidence_needed == []


@pytest.mark.parametrize(
    "message, group",
    [
        ("These funnel stages are unavailable", "Funnel data"),
        ("landing_page_views is missing in 1 row(s)", "Funnel data"),
        ("add_to_carts_to_checkouts_initiated is above 100%", "Funnel data"),
        ("At least one period has fewer than 30 purchases, so treat results as directional.", "Low purchase volume"),
        ("Attribution settings differ", "Attribution"),
        ("late-attributed purchases may still be added", "Attribution"),
        ("The file has no attribution setting", "Attribution"),
        ("The prior period has zero purchases", "Undefined metrics"),
        ("Results combine several campaigns.", "Other data checks"),
    ],
)
def test_warnings_are_grouped(message, group):
    assert list(view.group_warnings([message])) == [group]


def test_html_escapes_data_text():
    cards = [view.Kpi("roas", "<b>x</b>", "1", "2", "+1%", Status.STABLE)]

    assert "<b>x</b>" not in render.kpis_html(cards, "<i>p</i>", "c")


def test_findings_html_labels_each_kind_of_statement():
    html = render.findings_html(view.build_findings(diagnose(0)))

    assert "Confirmed observations" in html
    assert "Mathematical drivers" in html
    assert "cannot show causes" in html


def test_tree_html_marks_path_and_driver():
    html = render.tree_html(view.build_tree(diagnose(0)))

    assert html.count("fm-node fm-on-path") == 4
    assert "Main driver" in html
