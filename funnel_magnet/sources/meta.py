"""Load a Meta Ads daily CSV export into canonical columns and validate it."""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from funnel_magnet.metrics import OPTIONAL_STAGES, Totals, compute_metrics

ERROR = "error"
WARNING = "warning"

# Account-level exports have no campaign column, so every row gets this label.
ACCOUNT_LEVEL_CAMPAIGN = "All campaigns"

REQUIRED_MEASURES = ("spend", "impressions", "link_clicks", "purchases", "purchase_value")
ACTION_MEASURES = ("link_clicks", "purchases", "purchase_value")

# Tried against the whole column, so one value such as 13/06/2026 settles the order.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
)
MIDNIGHT_SUFFIX = r"[T ]00:00(:00)?$"

NUMBER_PATTERNS = {
    ".": re.compile(r"-?(\d+|\d{1,3}(,\d{3})+)(\.\d+)?|-?\.\d+"),
    ",": re.compile(r"-?(\d+|\d{1,3}(\.\d{3})+)(,\d+)?|-?,\d+"),
}

# Headers are compared in lower case with repeated spaces collapsed.
ALIASES = {
    "campaign": ("campaign name", "campaign"),
    "impressions": ("impressions",),
    "link_clicks": ("link clicks",),
    "purchases": ("purchases", "website purchases"),
    "purchase_value": (
        "purchases conversion value",
        "purchase conversion value",
        "website purchases conversion value",
        "purchase value",
    ),
    "landing_page_views": ("website landing page views",),
    "add_to_carts": ("adds to cart", "website adds to cart", "add to cart"),
    "checkouts_initiated": ("checkouts initiated", "website checkouts initiated"),
    "attribution_setting": ("attribution setting",),
    "currency": ("currency",),
    "reported_roas": (
        "purchase roas (return on ad spend)",
        "website purchase roas (return on ad spend)",
    ),
    "reported_cpa": ("cost per purchase",),
    "reported_cpc": ("cpc (cost per link click)",),
    "reported_cpm": ("cpm (cost per 1,000 impressions)",),
    "reported_ctr": ("ctr (link click-through rate)",),
}

# When both are exported, the first header is used for the funnel and the second is
# only compared with it, so a disagreement is reported rather than dropped.
PREFERRED_COLUMNS = {
    "landing_page_views": ("website landing page views", "landing page views"),
}

SPEND_HEADER = re.compile(r"^(amount spent( \((?P<currency>[a-z]{3})\))?|spend)$")
ALL_CLICKS_HEADERS = ("clicks (all)", "clicks")
BREAKDOWN_HEADERS = {"ad set name": "ad_set", "ad name": "ad"}

# Meta shows CTR as a percentage, so the recalculated value is multiplied to match.
REPORTED_CHECKS = {
    "reported_roas": ("roas", 1),
    "reported_cpa": ("cpa", 1),
    "reported_cpc": ("cpc", 1),
    "reported_cpm": ("cpm", 1),
    "reported_ctr": ("ctr", 100),
}
RELATIVE_GAP = 0.01
ROUNDING_GAP = 0.006


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str


@dataclass
class LoadResult:
    data: pd.DataFrame | None = None
    issues: list[Issue] = field(default_factory=list)
    currency: str | None = None

    @property
    def ok(self):
        return self.data is not None

    @property
    def errors(self):
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def warnings(self):
        return [issue for issue in self.issues if issue.severity == WARNING]

    def add(self, severity, code, message):
        self.issues.append(Issue(severity, code, message))


def _clean_number(value):
    """Strip symbols and spacing from a cell. Blank gives None."""
    if value is None or pd.isna(value):
        return None
    text = re.sub(r"[\s€$£₹%]", "", str(value))
    return None if text in ("", "-") else text


def parse_number(value, decimal="."):
    """Parse a cell using a decimal separator of "." or ",".

    Blank gives None, and a value that doesn't fit the separator raises ValueError.
    """
    text = _clean_number(value)
    if text is None:
        return None
    if not NUMBER_PATTERNS[decimal].fullmatch(text):
        raise ValueError(value)

    thousands = "," if decimal == "." else "."
    return float(text.replace(thousands, "").replace(decimal, "."))


def detect_decimal(columns, result):
    """Return the one decimal separator that reads every numeric cell in the file.

    Returns None, recording an error, when no separator or more than one reading fits.
    """
    all_cells = set()
    for name, series in columns.items():
        cells = {text for text in map(_clean_number, series) if text is not None}
        invalid = sum(
            not any(pattern.fullmatch(text) for pattern in NUMBER_PATTERNS.values())
            for text in cells
        )
        if invalid:
            result.add(ERROR, "invalid_number", f"{name} has {invalid} value(s) that are not numbers.")
        all_cells |= cells
    if result.errors:
        return None

    candidates = [
        decimal
        for decimal, pattern in NUMBER_PATTERNS.items()
        if all(pattern.fullmatch(text) for text in all_cells)
    ]
    if not candidates:
        result.add(
            ERROR,
            "inconsistent_number_format",
            "The file mixes comma and point decimal formats. Export it with one number format.",
        )
        return None

    if len(candidates) == 2:
        ambiguous = sorted(
            text for text in all_cells if parse_number(text, ".") != parse_number(text, ",")
        )
        if ambiguous:
            result.add(
                ERROR,
                "ambiguous_number",
                f"Values such as {ambiguous[0]} could use either a decimal or a thousands separator, "
                "and nothing else in the file settles which. Export numbers without thousands separators.",
            )
            return None
    return candidates[0]


def parse_dates(series):
    """Return (dates, None) when one reading fits the column, else (None, error code)."""
    text = series.str.strip().str.replace(MIDNIGHT_SUFFIX, "", regex=True)
    readings = []
    for date_format in DATE_FORMATS:
        parsed = pd.to_datetime(text, format=date_format, errors="coerce")
        if parsed.notna().all():
            readings.append(parsed)

    if not readings:
        return None, "invalid_date"
    if any(not reading.equals(readings[0]) for reading in readings):
        return None, "ambiguous_date"
    return readings[0], None


def _normalise_header(header):
    return " ".join(str(header).replace("﻿", "").lower().split())


def _read_text(source):
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source.read()
    return raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw


def _read_raw(source, result):
    try:
        text = _read_text(source)
    except UnicodeDecodeError:
        result.add(ERROR, "encoding", "The file could not be read as UTF-8 text.")
        return None

    first_line = text.splitlines()[0] if text.strip() else ""
    separator = max(",;\t", key=first_line.count)
    try:
        return pd.read_csv(io.StringIO(text), dtype=str, sep=separator, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError):
        result.add(ERROR, "unreadable", "The file could not be read as a CSV.")
        return None


def _map_columns(raw, result):
    """Return {canonical name: original header}, recording anything unusable."""
    headers = {}
    for original in raw.columns:
        headers.setdefault(_normalise_header(original), []).append(original)

    mapping = {}

    def claim(canonical, normalised_headers):
        found = [original for name in normalised_headers for original in headers.get(name, [])]
        if len(found) > 1:
            result.add(
                ERROR,
                "ambiguous_column",
                f"Several columns could be {canonical}: {', '.join(found)}.",
            )
        elif found:
            mapping[canonical] = found[0]

    for canonical, aliases in ALIASES.items():
        claim(canonical, aliases)
    for canonical, (preferred, other) in PREFERRED_COLUMNS.items():
        if preferred not in headers:
            claim(canonical, [other])
        elif other in headers:
            claim(f"{canonical}_compared", [other])
    claim("spend", [name for name in headers if SPEND_HEADER.match(name)])
    for header, canonical in BREAKDOWN_HEADERS.items():
        claim(canonical, [header])

    for name in headers:
        match = SPEND_HEADER.match(name)
        if match and match["currency"]:
            result.currency = match["currency"].upper()

    for date_header in ("day", "date"):
        if date_header in headers:
            mapping["date"] = headers[date_header][0]
            break
    else:
        _map_reporting_dates(raw, headers, mapping, result)

    missing = [name for name in REQUIRED_MEASURES if name not in mapping]
    if "link_clicks" in missing and any(name in headers for name in ALL_CLICKS_HEADERS):
        result.add(
            ERROR,
            "link_clicks_required",
            "The file has all-clicks data but no Link clicks column. "
            "Export Link clicks, because every click-based metric here uses them.",
        )
        missing.remove("link_clicks")
    if missing:
        result.add(ERROR, "missing_columns", f"Required columns are missing: {', '.join(missing)}.")
    return mapping


def _map_reporting_dates(raw, headers, mapping, result):
    starts = headers.get("reporting starts")
    ends = headers.get("reporting ends")
    if not starts:
        result.add(ERROR, "missing_columns", "Required columns are missing: date.")
        return
    if ends and (raw[starts[0]] != raw[ends[0]]).any():
        result.add(
            ERROR,
            "not_daily",
            "Rows cover more than one day. Export the report with a daily breakdown.",
        )
        return
    mapping["date"] = starts[0]


def _parse_numeric_column(series, name, decimal, result):
    numbers = pd.to_numeric(
        series.map(lambda value: parse_number(value, decimal)), errors="coerce"
    ).astype(float)
    if (numbers < 0).any():
        result.add(ERROR, "negative_value", f"{name} has negative values.")
        return None
    return numbers


def _drop_summary_rows(raw, mapping, result):
    summary = raw[mapping["date"]].str.strip() == ""
    if "campaign" in mapping:
        summary &= raw[mapping["campaign"]].str.strip() == ""
    if summary.any():
        result.add(
            WARNING,
            "summary_rows_removed",
            f"Removed {int(summary.sum())} row(s) with no date or campaign, such as a totals row.",
        )
    return raw[~summary].reset_index(drop=True)


def _build_frame(raw, mapping, result):
    frame = pd.DataFrame(index=raw.index)

    dates, date_error = parse_dates(raw[mapping["date"]])
    if date_error == "ambiguous_date":
        result.add(
            ERROR,
            "ambiguous_date",
            "Dates could be read as day/month or month/day, and no value in the file settles which. "
            "Export dates as YYYY-MM-DD.",
        )
    elif date_error:
        result.add(
            ERROR,
            "invalid_date",
            "Some dates are blank or use an unrecognised format, or the formats vary. "
            "Use one four-digit-year format, such as YYYY-MM-DD.",
        )
    frame["date"] = dates

    if "campaign" in mapping:
        frame["campaign"] = raw[mapping["campaign"]].str.strip()
        if (frame["campaign"] == "").any():
            result.add(ERROR, "missing_campaign", "Some rows have no campaign name.")
    else:
        frame["campaign"] = ACCOUNT_LEVEL_CAMPAIGN
        result.add(
            WARNING,
            "account_level",
            "The file has no campaign column, so it is analysed as one account-level total labelled "
            f"{ACCOUNT_LEVEL_CAMPAIGN}. Campaign-level results aren't available.",
        )

    for column in ("ad_set", "ad", "attribution_setting", "currency"):
        if column in mapping:
            frame[column] = raw[mapping[column]].str.strip()

    compared = [f"{name}_compared" for name in PREFERRED_COLUMNS]
    numeric = [
        column
        for column in (*REQUIRED_MEASURES, *OPTIONAL_STAGES, *REPORTED_CHECKS, *compared)
        if column in mapping
    ]
    decimal = detect_decimal({mapping[c]: raw[mapping[c]] for c in numeric}, result)
    if decimal is None:
        return frame

    for column in numeric:
        values = _parse_numeric_column(raw[mapping[column]], mapping[column], decimal, result)
        if values is not None:
            frame[column] = values

    for canonical in PREFERRED_COLUMNS:
        if canonical in frame and f"{canonical}_compared" in frame:
            _compare_columns(frame, canonical, mapping, result)
    return frame


def _compare_columns(frame, canonical, mapping, result):
    """Report rows where the preferred column and its alternative disagree, then drop the alternative."""
    used, other = frame[canonical], frame.pop(f"{canonical}_compared")
    differs = ~((used == other) | (used.isna() & other.isna()))
    if differs.any():
        result.add(
            WARNING,
            "conflicting_columns",
            f"{mapping[canonical]} and {mapping[canonical + '_compared']} differ in {int(differs.sum())} "
            f"row(s), totalling {used.sum():,.0f} and {other.sum():,.0f}. The funnel uses "
            f"{mapping[canonical]}, because it counts website visits only.",
        )


def _fill_confirmed_blanks(frame, column, blanks, result):
    result.add(
        WARNING,
        "blanks_as_zero",
        f"{column} is blank in {blanks} row(s). These were read as 0, as confirmed at upload.",
    )
    frame[column] = frame[column].fillna(0.0)


def _handle_blanks(frame, result, blank_actions_as_zero):
    """Keep missing observations apart from zeros.

    Meta can leave a zero action blank, but a blank can also be missing data, so
    the file alone can't settle it. Blanks become 0 only when logic requires it
    or the caller has confirmed it.
    """
    for column in REQUIRED_MEASURES:
        blanks = int(frame[column].isna().sum())
        if blanks == len(frame):
            result.add(ERROR, "empty_column", f"{column} has no values.")
        elif blanks and column not in ACTION_MEASURES:
            result.add(ERROR, "missing_values", f"{column} is blank in {blanks} row(s).")

    no_sale = frame["purchase_value"].isna() & (frame["purchases"] == 0)
    if no_sale.any() and not frame["purchase_value"].isna().all():
        frame.loc[no_sale, "purchase_value"] = 0.0
        result.add(
            WARNING,
            "value_zero_from_purchases",
            f"purchase_value is blank in {int(no_sale.sum())} row(s) with 0 purchases, so it was read as 0.",
        )

    for column in ACTION_MEASURES:
        blanks = int(frame[column].isna().sum())
        if not blanks or blanks == len(frame):
            continue
        if blank_actions_as_zero:
            _fill_confirmed_blanks(frame, column, blanks, result)
        else:
            result.add(
                ERROR,
                "missing_values",
                f"{column} is blank in {blanks} row(s). A blank could mean 0 or missing data. "
                "Export zeros, or confirm that blanks mean 0.",
            )

    for column in OPTIONAL_STAGES:
        if column not in frame:
            continue
        blanks = int(frame[column].isna().sum())
        if blanks == len(frame):
            frame = frame.drop(columns=column)
            result.add(WARNING, "funnel_stage_unavailable", f"{column} has no values, so it is unavailable.")
        elif blanks and blank_actions_as_zero:
            _fill_confirmed_blanks(frame, column, blanks, result)
        elif blanks:
            result.add(
                WARNING,
                "funnel_values_missing",
                f"{column} is blank in {blanks} row(s). These stay missing, so any period "
                "containing them excludes this stage.",
            )
    return frame


def _handle_duplicates(frame, result):
    before = len(frame)
    frame = frame.drop_duplicates().reset_index(drop=True)
    if len(frame) < before:
        result.add(
            WARNING,
            "duplicates_removed",
            f"Removed {before - len(frame)} exact duplicate row(s).",
        )

    key = ["date", "campaign", *[c for c in ("ad_set", "ad") if c in frame]]
    conflicts = int(frame.duplicated(subset=key).sum())
    if conflicts:
        result.add(
            ERROR,
            "conflicting_rows",
            f"{conflicts} row(s) repeat a date and campaign with different values. "
            "Export one row per day, or include the ad set or ad name.",
        )
    return frame


def _check_currency(frame, result):
    if "currency" not in frame:
        return
    currencies = sorted(set(frame["currency"]) - {""})
    if len(currencies) > 1:
        result.add(ERROR, "mixed_currency", f"The file mixes currencies: {', '.join(currencies)}.")
    elif currencies:
        result.currency = currencies[0]


def _check_plausibility(frame, result):
    checks = (
        (
            frame["link_clicks"] > frame["impressions"],
            "clicks_above_impressions",
            "link clicks exceed impressions",
        ),
        (
            (frame["purchases"] > 0) & (frame["purchase_value"] == 0),
            "purchases_without_value",
            "there are purchases but no purchase value",
        ),
        (
            (frame["purchases"] == 0) & (frame["purchase_value"] > 0),
            "value_without_purchases",
            "there is purchase value but no purchases",
        ),
        (
            (frame["spend"] == 0) & (frame["impressions"] > 0),
            "impressions_without_spend",
            "there are impressions but no spend",
        ),
    )
    for mask, code, description in checks:
        count = int(mask.sum())
        if count:
            result.add(WARNING, code, f"In {count} row(s), {description}.")


def _check_reported_metrics(frame, result):
    """Compare Meta's reported ratios with the recalculated ones, row by row."""
    present = {k: v for k, v in REPORTED_CHECKS.items() if k in frame}
    if not present:
        return

    gaps = dict.fromkeys(present, 0)
    for row in frame.itertuples(index=False):
        metrics = compute_metrics(
            Totals(row.spend, row.impressions, row.link_clicks, row.purchases, row.purchase_value)
        )
        for column, (metric, multiplier) in present.items():
            reported = getattr(row, column)
            calculated = metrics[metric]
            if pd.isna(reported) or calculated is None:
                continue
            calculated *= multiplier
            allowed = max(RELATIVE_GAP * abs(calculated), ROUNDING_GAP)
            if abs(reported - calculated) > allowed:
                gaps[column] += 1

    for column, count in gaps.items():
        if count:
            result.add(
                WARNING,
                "reported_metric_gap",
                f"{column} differs from the recalculated value in {count} row(s). "
                "It may use a different definition, so recalculated values are used.",
            )


def load_meta_csv(source, blank_actions_as_zero=False):
    """Load a daily Meta Ads export from a path or file-like object.

    LoadResult.data is None when any error makes the file unsafe to analyse.
    Set blank_actions_as_zero only once the user confirms blanks mean 0.
    """
    result = LoadResult()
    raw = _read_raw(source, result)
    if raw is None:
        return result

    mapping = _map_columns(raw, result)
    if result.errors:
        return result

    raw = _drop_summary_rows(raw, mapping, result)
    if raw.empty:
        result.add(ERROR, "no_rows", "The file has no data rows.")
        return result

    frame = _build_frame(raw, mapping, result)
    if result.errors:
        return result

    frame = _handle_blanks(frame, result, blank_actions_as_zero)
    _check_currency(frame, result)
    if result.errors:
        return result

    frame = _handle_duplicates(frame, result)
    if result.errors:
        return result

    _check_plausibility(frame, result)
    _check_reported_metrics(frame, result)

    result.data = frame.sort_values(["date", "campaign"]).reset_index(drop=True)
    return result
