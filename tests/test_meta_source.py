import io

import pytest

from funnel_magnet.sources.meta import load_meta_csv, parse_number

HEADER = "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value"
ROW = "2026-06-01,A,100.00,10000,200,5,300.00"


def load(text):
    return load_meta_csv(io.StringIO(text))


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def codes(result):
    return [issue.code for issue in result.issues]


def test_meta_headers_map_to_canonical_columns():
    result = load(csv_text(ROW))

    assert result.ok
    assert result.currency == "EUR"
    row = result.data.iloc[0]
    assert (row["spend"], row["link_clicks"], row["purchase_value"]) == (100.0, 200.0, 300.0)


def test_header_matching_ignores_case_and_spacing():
    header = "DAY,campaign  name,Spend,impressions,LINK CLICKS,Website purchases,purchase value"

    assert load(csv_text(ROW, header=header)).ok


def test_bytes_with_a_byte_order_mark_are_accepted():
    source = io.BytesIO(("﻿" + csv_text(ROW)).encode("utf-8"))

    assert load_meta_csv(source).ok


def test_semicolon_files_with_decimal_commas_are_accepted():
    text = "Day;Campaign name;Amount spent (EUR);Impressions;Link clicks;Purchases;Purchases conversion value\n"
    text += "2026-06-01;A;100,50;10000;200;5;300,25\n"

    result = load(text)

    assert result.data.iloc[0]["spend"] == 100.5


@pytest.mark.parametrize(
    "text, decimal, expected",
    [
        ("1,234.56", ".", 1234.56),
        ("1.234,56", ",", 1234.56),
        ("1,234", ".", 1234.0),
        ("1,234", ",", 1.234),
        ("€1,234,567", ".", 1234567.0),
        ("12,5", ",", 12.5),
        ("3.4%", ".", 3.4),
        ("", ".", None),
        ("-", ".", None),
    ],
)
def test_parse_number(text, decimal, expected):
    assert parse_number(text, decimal) == expected


@pytest.mark.parametrize("text", ["abc", "12x", "inf", "nan", "12,5", "1,23"])
def test_parse_number_rejects_text_that_does_not_fit(text):
    with pytest.raises(ValueError):
        parse_number(text, ".")


def test_all_clicks_cannot_replace_link_clicks():
    header = HEADER.replace("Link clicks", "Clicks (all)")

    result = load(csv_text(ROW, header=header))

    assert not result.ok
    assert "link_clicks_required" in codes(result)


def test_all_clicks_alongside_link_clicks_is_ignored():
    header = HEADER + ",Clicks (all)"

    result = load(csv_text(ROW + ",900", header=header))

    assert result.ok
    assert result.data.iloc[0]["link_clicks"] == 200.0


def test_missing_columns_are_named():
    result = load("Day,Campaign name\n2026-06-01,A\n")

    assert not result.ok
    assert "spend" in result.errors[0].message


def test_two_spend_columns_are_ambiguous():
    header = HEADER + ",Amount spent (USD)"

    assert "ambiguous_column" in codes(load(csv_text(ROW + ",110", header=header)))


def test_rows_covering_several_days_are_rejected():
    header = HEADER.replace("Day", "Reporting starts") + ",Reporting ends"

    result = load(csv_text(ROW + ",2026-06-30", header=header))

    assert "not_daily" in codes(result)


def test_daily_reporting_dates_are_accepted():
    header = HEADER.replace("Day", "Reporting starts") + ",Reporting ends"

    assert load(csv_text(ROW + ",2026-06-01", header=header)).ok


@pytest.mark.parametrize(
    "row, code",
    [
        ("2026-06-01,A,abc,10000,200,5,300", "invalid_number"),
        ("2026-06-01,A,-5,10000,200,5,300", "negative_value"),
        ("2026-06-01,A,,10000,200,5,300", "empty_column"),
        ("2026-06-01,,100,10000,200,5,300", "missing_campaign"),
    ],
)
def test_invalid_values_block_analysis(row, code):
    result = load(csv_text(row))

    assert not result.ok
    assert code in codes(result)


def test_blank_spend_in_some_rows_is_an_error():
    result = load(csv_text(ROW, "2026-06-02,A,,10000,200,5,300"))

    assert "missing_values" in codes(result)


def test_totals_rows_are_removed():
    result = load(csv_text(ROW, ",,100.00,10000,200,5,300.00"))

    assert len(result.data) == 1
    assert "summary_rows_removed" in codes(result)


def test_exact_duplicates_are_removed_once():
    result = load(csv_text(ROW, ROW))

    assert len(result.data) == 1
    assert "duplicates_removed" in codes(result)


def test_conflicting_rows_block_analysis():
    assert "conflicting_rows" in codes(load(csv_text(ROW, "2026-06-01,A,999,10000,200,5,300")))


def test_ad_set_rows_for_one_day_are_allowed():
    header = HEADER + ",Ad set name"

    result = load(csv_text(ROW + ",Set 1", "2026-06-01,A,50,5000,100,2,100,Set 2", header=header))

    assert result.ok
    assert len(result.data) == 2


def test_mixed_currencies_block_analysis():
    header = HEADER.replace("Amount spent (EUR)", "Amount spent") + ",Currency"

    result = load(csv_text(ROW + ",EUR", "2026-06-02,A,100,10000,200,5,300,USD", header=header))

    assert "mixed_currency" in codes(result)


@pytest.mark.parametrize(
    "row, code",
    [
        ("2026-06-01,A,100,10,200,5,300", "clicks_above_impressions"),
        ("2026-06-01,A,100,10000,200,5,0", "purchases_without_value"),
        ("2026-06-01,A,100,10000,200,0,300", "value_without_purchases"),
        ("2026-06-01,A,0,10000,200,5,300", "impressions_without_spend"),
    ],
)
def test_implausible_values_are_flagged(row, code):
    result = load(csv_text(row))

    assert result.ok
    assert code in codes(result)


def test_blank_funnel_column_is_treated_as_unavailable():
    header = HEADER + ",Landing page views"

    result = load(csv_text(ROW + ",", header=header))

    assert "landing_page_views" not in result.data


def test_reported_metrics_that_match_raise_no_warning():
    header = HEADER + ",Purchase ROAS (return on ad spend),CTR (link click-through rate)"

    assert "reported_metric_gap" not in codes(load(csv_text(ROW + ",3.00,2.00", header=header)))


def test_reported_ctr_on_all_clicks_is_flagged():
    header = HEADER + ",CTR (link click-through rate)"

    assert "reported_metric_gap" in codes(load(csv_text(ROW + ",4.50", header=header)))


def test_empty_file_is_an_error():
    assert not load("").ok


def test_header_only_file_is_an_error():
    assert "no_rows" in codes(load(HEADER + "\n"))
