"""Regression tests for missing values and for date and number formats."""

import io

import pandas as pd
import pytest

from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import WEEKLY, aggregate, list_complete_periods
from funnel_magnet.sources.meta import load_meta_csv

HEADER = "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value"
FUNNEL_HEADER = HEADER + ",Landing page views,Adds to cart"


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def load(text, **options):
    return load_meta_csv(io.StringIO(text), **options)


def codes(result):
    return [issue.code for issue in result.issues]


def row(day, lpv, carts=20):
    return f"{day},A,100.00,10000,200,5,300.00,{lpv},{carts}"


def two_weeks(blank_day=None, zero_day=None):
    rows = []
    for day in pd.date_range("2026-06-01", "2026-06-14").strftime("%Y-%m-%d"):
        lpv = "" if day == blank_day else "0" if day == zero_day else "160"
        rows.append(row(day, lpv))
    return csv_text(*rows, header=FUNNEL_HEADER)


# Missing values and genuine zeros


def test_blank_funnel_cell_stays_missing_and_a_zero_stays_zero():
    result = load(csv_text(row("2026-06-01", ""), row("2026-06-02", "0"), header=FUNNEL_HEADER))

    assert result.ok
    assert pd.isna(result.data.iloc[0]["landing_page_views"])
    assert result.data.iloc[1]["landing_page_views"] == 0
    assert "funnel_values_missing" in codes(result)
    assert "blanks_as_zero" not in codes(result)


def test_period_with_a_missing_funnel_value_has_no_stage_total():
    data = load(two_weeks(blank_day="2026-06-10")).data
    first, second = list_complete_periods(data, WEEKLY)

    assert aggregate(data, first).landing_page_views == 1120.0
    assert aggregate(data, second).landing_page_views is None


def test_period_with_a_genuine_zero_keeps_its_stage_total():
    data = load(two_weeks(zero_day="2026-06-10")).data
    _, second = list_complete_periods(data, WEEKLY)

    assert aggregate(data, second).landing_page_views == 960.0


def test_missing_value_creates_no_false_decline():
    result = run_diagnosis(load(two_weeks(blank_day="2026-06-10")).data, WEEKLY)

    assert result.paths == [("roas",)]
    assert "link_clicks_to_landing_page_views" not in result.changes
    assert "link_clicks_to_add_to_carts" in result.changes
    assert any("excluded rather than counted as zero" in item for item in result.warnings)


def test_genuine_zero_is_reported_as_a_real_stage_decline():
    data = load(two_weeks(zero_day="2026-06-10")).data
    data.loc[data["date"] >= "2026-06-08", "add_to_carts"] = 12.0
    data.loc[data["date"] >= "2026-06-08", "purchases"] = 3.0
    data.loc[data["date"] >= "2026-06-08", "purchase_value"] = 180.0

    result = run_diagnosis(data, WEEKLY)

    assert result.changes["link_clicks_to_landing_page_views"].pct_change == pytest.approx(-1 / 7)


@pytest.mark.parametrize(
    "blank_row, column",
    [
        ("2026-06-02,A,100,10000,,5,300", "link_clicks"),
        ("2026-06-02,A,100,10000,200,,300", "purchases"),
        ("2026-06-02,A,100,10000,200,5,", "purchase_value"),
    ],
)
def test_blank_required_action_is_rejected(blank_row, column):
    result = load(csv_text("2026-06-01,A,100.00,10000,200,5,300.00", blank_row))

    assert not result.ok
    assert column in result.errors[0].message
    assert "missing_values" in codes(result)


def test_blank_value_with_zero_purchases_is_logically_zero():
    result = load(csv_text("2026-06-01,A,100.00,10000,200,5,300.00", "2026-06-02,A,100,10000,200,0,"))

    assert result.ok
    assert result.data.iloc[1]["purchase_value"] == 0
    assert "value_zero_from_purchases" in codes(result)


def test_confirmation_reads_blanks_as_zero_and_records_it():
    text = csv_text(
        "2026-06-01,A,100.00,10000,200,5,300.00,160,20",
        "2026-06-02,A,100.00,10000,200,,,,",
        header=FUNNEL_HEADER,
    )

    result = load(text, blank_actions_as_zero=True)

    assert result.ok
    assert result.data.iloc[1]["purchases"] == 0
    assert result.data.iloc[1]["landing_page_views"] == 0
    assert codes(result).count("blanks_as_zero") == 4


def test_confirmation_does_not_fill_spend():
    result = load(
        csv_text("2026-06-01,A,100.00,10000,200,5,300.00", "2026-06-02,A,,10000,200,5,300"),
        blank_actions_as_zero=True,
    )

    assert not result.ok


def test_confirmation_does_not_invent_a_stage_with_no_values():
    result = load(csv_text(row("2026-06-01", ""), header=FUNNEL_HEADER), blank_actions_as_zero=True)

    assert "landing_page_views" not in result.data
    assert "funnel_stage_unavailable" in codes(result)


# Dates


@pytest.mark.parametrize(
    "first, second",
    [
        ("2026-06-01", "2026-06-13"),
        ("2026/06/01", "2026/06/13"),
        ("2026-06-01T00:00:00", "2026-06-13T00:00:00"),
        ("2026-06-01 00:00:00", "2026-06-13 00:00:00"),
        ("2026-06-01 00:00", "2026-06-13 00:00"),
        ("01/06/2026", "13/06/2026"),
        ("06/01/2026", "06/13/2026"),
        ("01-06-2026", "13-06-2026"),
        ("01.06.2026", "13.06.2026"),
        ("1 Jun 2026", "13 Jun 2026"),
        ("1 June 2026", "13 June 2026"),
        ("01-Jun-2026", "13-Jun-2026"),
        ('"Jun 1, 2026"', '"Jun 13, 2026"'),
        ('"June 1, 2026"', '"June 13, 2026"'),
    ],
)
def test_unambiguous_date_formats_are_accepted(first, second):
    result = load(csv_text(f"{first},A,100.00,10000,200,5,300.00", f"{second},A,100.00,10000,200,5,300.00"))

    assert result.ok
    assert list(result.data["date"].dt.strftime("%Y-%m-%d")) == ["2026-06-01", "2026-06-13"]


def test_one_value_settles_the_day_and_month_order_for_the_file():
    result = load(csv_text("05/06/2026,A,1.50,10000,200,5,300", "25/06/2026,A,1.50,10000,200,5,300"))

    assert result.data.iloc[0]["date"] == pd.Timestamp("2026-06-05")


@pytest.mark.parametrize("day", ["01/06/2026", "01-06-2026"])
def test_day_month_order_that_cannot_be_settled_is_rejected(day):
    result = load(csv_text(f"{day},A,100.00,10000,200,5,300.00"))

    assert not result.ok
    assert "ambiguous_date" in codes(result)
    assert "YYYY-MM-DD" in result.errors[0].message


def test_same_day_and_month_has_only_one_reading():
    assert load(csv_text("05/05/2026,A,100.00,10000,200,5,300.00")).ok


@pytest.mark.parametrize(
    "rows",
    [
        ("01/06/26,A,1.50,10000,200,5,300",),
        ("13/06/2026,A,1.50,10000,200,5,300", "06/14/2026,A,1.50,10000,200,5,300"),
        ("2026-06-01,A,1.50,10000,200,5,300", "02/06/2026,A,1.50,10000,200,5,300"),
        ("2026-06-01 09:30:00,A,1.50,10000,200,5,300",),
        ("not a date,A,1.50,10000,200,5,300",),
        (",A,1.50,10000,200,5,300",),
    ],
)
def test_unusable_dates_are_rejected(rows):
    assert "invalid_date" in codes(load(csv_text(*rows)))


# Decimal and thousands formats


def test_decimal_point_with_thousands_commas():
    result = load(csv_text('2026-06-01,A,"1,234.50","1,234,567","1,234",5,300.00'))
    row_data = result.data.iloc[0]

    assert (row_data["spend"], row_data["impressions"], row_data["link_clicks"]) == (1234.5, 1234567, 1234)


def test_decimal_comma_with_thousands_points():
    header = HEADER.replace(",", ";")
    result = load(csv_text("2026-06-01;A;1.234,50;1.234.567;1.234;5;300,00", header=header))
    row_data = result.data.iloc[0]

    assert (row_data["spend"], row_data["impressions"], row_data["link_clicks"]) == (1234.5, 1234567, 1234)


def test_other_columns_settle_a_value_that_is_ambiguous_alone():
    result = load(csv_text('2026-06-01,A,12.50,"1,234",200,5,300'))

    assert result.data.iloc[0]["impressions"] == 1234


def test_decimal_commas_settle_a_three_decimal_value():
    header = HEADER.replace(",", ";")
    result = load(csv_text("2026-06-01;A;12,50;1000;200;5;1,234", header=header))

    assert result.data.iloc[0]["purchase_value"] == 1.234


@pytest.mark.parametrize("value", ['"1,234"', "1.234"])
def test_value_with_no_settling_evidence_is_rejected(value):
    result = load(csv_text(f"2026-06-01,A,{value},10000,200,5,300"))

    assert not result.ok
    assert "ambiguous_number" in codes(result)


def test_mixed_decimal_formats_are_rejected():
    result = load(csv_text('2026-06-01,A,12.50,10000,200,5,"300,25"'))

    assert "inconsistent_number_format" in codes(result)


def test_tab_separated_files_are_accepted():
    header = HEADER.replace(",", "\t")

    assert load(csv_text("2026-06-01\tA\t100.00\t10000\t200\t5\t300.00", header=header)).ok


def test_symbols_and_percent_signs_do_not_change_the_value():
    header = HEADER + ",CTR (link click-through rate)"
    result = load(csv_text("2026-06-01,A,€100.00,10000,200,5,300.00,2.00%", header=header))

    assert result.data.iloc[0]["spend"] == 100.0
    assert "reported_metric_gap" not in codes(result)
