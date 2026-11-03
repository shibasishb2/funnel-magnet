"""Account-level Meta exports, with no campaign column and both landing page view columns.

The fixture is synthetic, but its headers copy a real account-level INR export.
"""

import io
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.metrics import compute_metrics
from funnel_magnet.periods import MONTHLY, aggregate, latest_two_complete
from funnel_magnet.sources.meta import ACCOUNT_LEVEL_CAMPAIGN, load_meta_csv
from funnel_magnet.ui import report, view

FIXTURE = Path(__file__).parent / "fixtures" / "meta_account_level_inr.csv"
HEADER = "Day,Amount spent (INR),Impressions,Link clicks,Purchases,Purchases conversion value"
ROW = "2026-08-0{day},1000.00,50000,600,6,15000.00"


def load(text):
    return load_meta_csv(io.StringIO(text))


def codes(result):
    return [issue.code for issue in result.issues]


def messages(result):
    return " ".join(issue.message for issue in result.issues)


def account_csv(extra_header="", extra_values=(), days=3):
    rows = [ROW.format(day=day) + (f",{extra_values[day - 1]}" if extra_header else "") for day in range(1, days + 1)]
    return "\n".join([HEADER + extra_header, *rows]) + "\n"


# The realistic fixture


@pytest.fixture(scope="module")
def loaded():
    return load_meta_csv(FIXTURE)


def test_fixture_reproduces_the_rejected_headers():
    header = pd.read_csv(FIXTURE, nrows=0, encoding="utf-8-sig").columns

    assert {"Landing page views", "Website landing page views", "Amount spent (INR)"} <= set(header)
    assert not any("campaign" in name.lower() for name in header)


def test_account_level_export_loads(loaded):
    assert loaded.ok, messages(loaded)
    assert not loaded.errors
    assert set(loaded.data["campaign"]) == {ACCOUNT_LEVEL_CAMPAIGN}
    assert len(loaded.data) == 92
    assert loaded.data["date"].min() == pd.Timestamp("2026-08-01")
    assert loaded.data["date"].max() == pd.Timestamp("2026-10-31")


def test_account_level_export_is_disclosed(loaded):
    assert "account_level" in codes(loaded)
    assert "Campaign-level results aren't available" in messages(loaded)


def test_currency_is_inr(loaded):
    assert loaded.currency == "INR"
    assert view.format_value("cpm", 186.5, loaded.currency) == "₹186.50"


def test_identical_landing_page_columns_raise_no_conflict(loaded):
    assert "conflicting_columns" not in codes(loaded)
    assert "landing_page_views" in loaded.data
    assert "landing_page_views_compared" not in loaded.data


def test_blank_meta_columns_are_ignored(loaded):
    assert not {"Meta purchases", "Meta adds to cart"} & set(loaded.data.columns)
    assert loaded.data["purchases"].notna().all()


def test_reported_metrics_match_the_recalculation(loaded):
    assert "reported_metric_gap" not in codes(loaded)


def test_diagnosis_uses_the_same_metric_definitions(loaded):
    prior, current = latest_two_complete(loaded.data, MONTHLY)
    diagnosis = run_diagnosis(loaded.data, MONTHLY)
    expected = compute_metrics(aggregate(loaded.data, current))

    assert (diagnosis.prior_label, diagnosis.current_label) == ("2026-09", "2026-10")
    for metric, value in expected.items():
        assert diagnosis.changes[metric].current == pytest.approx(value)
    assert "landing_page_views_to_add_to_carts" in diagnosis.changes


def test_account_level_diagnosis_has_no_campaign_check(loaded):
    diagnosis = run_diagnosis(loaded.data, MONTHLY)
    evidence = build_evidence(diagnosis, loaded.currency, [w.message for w in loaded.warnings])

    assert not any("several campaigns" in text for text in diagnosis.warnings)
    assert not evidence.campaign_data_available


def test_report_shows_rupees_not_euros(loaded):
    diagnosis = run_diagnosis(loaded.data, MONTHLY)
    warnings = tuple(w.message for w in loaded.warnings)
    html = report.build_report(
        report.ReportInput(
            diagnosis, build_evidence(diagnosis, loaded.currency, warnings), "Export", "All campaigns",
            "a", "b", "c", date(2026, 11, 3), loaded.currency, warnings,
        )
    )

    assert "₹" in html and "€" not in html
    assert "INR" in html


# Landing page view columns


def test_website_column_is_preferred_and_a_conflict_is_reported():
    text = account_csv(",Website landing page views,Landing page views", ("500,520", "480,480", "450,470"))
    result = load(text)

    assert result.ok
    assert list(result.data["landing_page_views"]) == [500, 480, 450]
    warning = next(issue for issue in result.warnings if issue.code == "conflicting_columns")
    assert "differ in 2 row(s)" in warning.message
    assert "1,430 and 1,470" in warning.message
    assert "uses Website landing page views" in warning.message


def test_column_order_does_not_change_the_preference():
    text = account_csv(",Landing page views,Website landing page views", ("520,500", "480,480", "470,450"))

    assert list(load(text).data["landing_page_views"]) == [500, 480, 450]


def test_a_blank_in_one_column_counts_as_a_difference():
    text = account_csv(",Website landing page views,Landing page views", ("500,500", ",480", "450,450"))
    result = load(text)

    assert "differ in 1 row(s)" in messages(result)
    assert result.data["landing_page_views"].isna().sum() == 1


@pytest.mark.parametrize("header", ["Landing page views", "Website landing page views"])
def test_either_column_alone_is_used_without_a_warning(header):
    result = load(account_csv(f",{header}", ("500", "480", "450")))

    assert list(result.data["landing_page_views"]) == [500, 480, 450]
    assert "conflicting_columns" not in codes(result)


def test_both_columns_in_a_campaign_export_are_handled_too():
    text = (
        "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value,"
        "Landing page views,Website landing page views\n"
        "2026-06-01,A,100,10000,200,5,300,150,140\n"
    )
    result = load(text)

    assert result.ok
    assert list(result.data["landing_page_views"]) == [140]
    assert "conflicting_columns" in codes(result)


# Account-level rows


def test_totals_row_with_no_date_is_removed():
    text = account_csv() + ",3000.00,150000,1800,18,45000.00\n"
    result = load(text)

    assert len(result.data) == 3
    assert "summary_rows_removed" in codes(result)


def test_repeated_days_in_an_account_export_are_rejected():
    text = account_csv() + "2026-08-01,1200.00,50000,600,6,15000.00\n"

    assert "conflicting_rows" in codes(load(text))


def test_rupee_symbols_in_values_are_read():
    text = HEADER + "\n2026-08-01,₹1000.00,50000,600,6,₹15000.00\n"

    assert load(text).data["spend"].iloc[0] == 1000.0


def test_campaign_exports_still_need_campaign_names():
    text = "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value\n"
    text += "2026-06-01,,100,10000,200,5,300\n2026-06-02,A,100,10000,200,5,300\n"

    assert "missing_campaign" in codes(load(text))
