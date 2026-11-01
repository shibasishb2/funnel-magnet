"""Weekly (Monday to Sunday) and calendar-month periods."""

from dataclasses import dataclass

import pandas as pd

from funnel_magnet.metrics import OPTIONAL_STAGES, Totals

WEEKLY = "weekly"
MONTHLY = "monthly"


class InsufficientPeriodsError(ValueError):
    """The data does not contain two complete periods to compare."""


@dataclass(frozen=True)
class Period:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str

    @property
    def days(self):
        return (self.end - self.start).days + 1


def _make_week(start):
    end = start + pd.Timedelta(days=6)
    iso = start.isocalendar()
    return Period(start, end, f"{iso.year}-W{iso.week:02d}")


def _make_month(start):
    return Period(start, start + pd.offsets.MonthEnd(0), start.strftime("%Y-%m"))


def list_complete_periods(data, granularity):
    """Return, oldest first, every period that falls fully inside the data's date range."""
    first, last = data["date"].min(), data["date"].max()

    if granularity == WEEKLY:
        start = first - pd.Timedelta(days=first.weekday())
        make, step = _make_week, pd.Timedelta(days=7)
    elif granularity == MONTHLY:
        start = first.replace(day=1)
        make, step = _make_month, pd.offsets.MonthBegin(1)
    else:
        raise ValueError(f"Unknown granularity: {granularity}")

    periods = []
    while start <= last:
        period = make(start)
        if period.start >= first and period.end <= last:
            periods.append(period)
        start = start + step
    return periods


def latest_two_complete(data, granularity):
    """Return (prior, current), the latest two complete periods."""
    periods = list_complete_periods(data, granularity)
    if len(periods) < 2:
        raise InsufficientPeriodsError(
            f"A {granularity} comparison needs two complete periods, but the data has {len(periods)}."
        )
    return periods[-2], periods[-1]


def select_rows(data, period, campaign=None):
    rows = data[(data["date"] >= period.start) & (data["date"] <= period.end)]
    if campaign is not None:
        rows = rows[rows["campaign"] == campaign]
    return rows


def _optional_total(rows, column):
    """Return a column's total, or None when any value in the period is missing.

    A partial total would look like a real decline, so it is not reported.
    """
    if column not in rows or rows.empty or rows[column].isna().any():
        return None
    return float(rows[column].sum())


def aggregate(data, period, campaign=None):
    """Sum daily rows into period totals."""
    rows = select_rows(data, period, campaign)
    return Totals(
        spend=float(rows["spend"].sum()),
        impressions=float(rows["impressions"].sum()),
        link_clicks=float(rows["link_clicks"].sum()),
        purchases=float(rows["purchases"].sum()),
        purchase_value=float(rows["purchase_value"].sum()),
        **{stage: _optional_total(rows, stage) for stage in OPTIONAL_STAGES},
    )
