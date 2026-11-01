import pandas as pd
import pytest

from funnel_magnet.periods import (
    MONTHLY,
    WEEKLY,
    InsufficientPeriodsError,
    aggregate,
    latest_two_complete,
    list_complete_periods,
)


def daily_data(start, end, **extra):
    dates = pd.date_range(start, end)
    frame = pd.DataFrame(
        {
            "date": dates,
            "campaign": "A",
            "spend": 10.0,
            "impressions": 1000.0,
            "link_clicks": 20.0,
            "purchases": 1.0,
            "purchase_value": 50.0,
        }
    )
    return frame.assign(**extra)


def test_weeks_run_monday_to_sunday_and_exclude_partial_weeks():
    # 2026-08-05 is a Wednesday and 2026-08-27 is a Thursday.
    periods = list_complete_periods(daily_data("2026-08-05", "2026-08-27"), WEEKLY)

    assert [p.start.strftime("%Y-%m-%d") for p in periods] == ["2026-08-10", "2026-08-17"]
    assert all(p.start.weekday() == 0 and p.days == 7 for p in periods)


def test_months_exclude_partial_months():
    periods = list_complete_periods(daily_data("2026-05-15", "2026-08-30"), MONTHLY)

    assert [p.label for p in periods] == ["2026-06", "2026-07"]


def test_february_in_a_leap_year_is_one_period():
    periods = list_complete_periods(daily_data("2028-02-01", "2028-02-29"), MONTHLY)

    assert [(p.label, p.days) for p in periods] == [("2028-02", 29)]


def test_latest_two_complete_periods_are_chosen():
    prior, current = latest_two_complete(daily_data("2026-06-01", "2026-08-31"), MONTHLY)

    assert (prior.label, current.label) == ("2026-07", "2026-08")


def test_too_little_data_raises_a_clear_error():
    with pytest.raises(InsufficientPeriodsError, match="two complete periods"):
        latest_two_complete(daily_data("2026-06-01", "2026-06-10"), WEEKLY)


def test_unknown_granularity_is_rejected():
    with pytest.raises(ValueError):
        list_complete_periods(daily_data("2026-06-01", "2026-06-10"), "daily")


def test_aggregate_sums_only_rows_in_the_period():
    data = daily_data("2026-06-01", "2026-06-14")
    period = list_complete_periods(data, WEEKLY)[0]
    totals = aggregate(data, period)

    assert totals.spend == 70.0
    assert totals.purchases == 7.0
    assert totals.landing_page_views is None


def test_aggregate_filters_to_one_campaign():
    data = pd.concat(
        [daily_data("2026-06-01", "2026-06-07"), daily_data("2026-06-01", "2026-06-07", campaign="B")]
    )
    period = list_complete_periods(data, WEEKLY)[0]

    assert aggregate(data, period).spend == 140.0
    assert aggregate(data, period, campaign="B").spend == 70.0


def test_a_stage_with_no_values_in_a_period_is_unavailable_not_zero():
    data = daily_data("2026-06-01", "2026-06-14", landing_page_views=float("nan"))
    data.loc[data["date"] >= "2026-06-08", "landing_page_views"] = 15.0
    first, second = list_complete_periods(data, WEEKLY)

    assert aggregate(data, first).landing_page_views is None
    assert aggregate(data, second).landing_page_views == 105.0
