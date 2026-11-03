"""The ROAS diagnostic decision tree.

Everything here is mathematical. Results say which metrics moved and how much
each one contributed, never why. Causes are hypotheses for a later stage.

The tree follows every material change, better or worse, so an improvement is
explained too. It also looks beneath a stable metric whose inputs moved
materially, because opposing moves can cancel out in the parent.
"""

from dataclasses import dataclass, field
from enum import Enum

from funnel_magnet.decompose import Decomposition, Factor, decompose
from funnel_magnet.metrics import (
    HIGHER_IS_BETTER,
    OPTIONAL_STAGES,
    available_funnel_stages,
    compute_metrics,
    funnel_rates,
    rate_name,
)
from funnel_magnet.periods import aggregate, latest_two_complete, select_rows

DEFAULT_MATERIALITY_THRESHOLD = 0.05
MIN_PURCHASES_PER_PERIOD = 30
MAX_ATTRIBUTION_LAG_DAYS = 7

DRIVER_STATEMENT = (
    "Drivers show how the arithmetic of ROAS changed. They do not establish why it changed."
)

DATA_NEEDED = {
    "aov": "Order-level data, such as products, discounts and items per order, to explain the AOV change.",
    "cpm": "Competitor activity, seasonality, and audience and placement delivery data to explain the CPM change.",
    "ctr": "Frequency, ad-level CTR and creative launch dates to explain the CTR change.",
    "cvr": "Funnel-stage counts, plus site and tracking records, to locate the CVR change.",
    "link_clicks_to_landing_page_views": "Page speed, landing page changes and tracking records for this stage.",
    "landing_page_views_to_add_to_carts": "Landing page, pricing and stock change records for this stage.",
    "add_to_carts_to_checkouts_initiated": "Cart and on-site behaviour data for this stage.",
    "checkouts_initiated_to_purchases": "Checkout, payment and shipping records, plus tracking records, for this stage.",
}


class Status(str, Enum):
    IMPROVED = "improved"
    DETERIORATED = "deteriorated"
    STABLE = "stable"
    UNDEFINED = "undefined"


@dataclass(frozen=True)
class MetricChange:
    metric: str
    prior: float | None
    current: float | None
    pct_change: float | None
    status: Status


@dataclass(frozen=True)
class Step:
    node: str
    finding: str


@dataclass(frozen=True)
class Driver:
    metric: str
    contribution: float  # In ROAS points. All drivers sum to the ROAS change.
    weight: float
    pct_change: float
    role: str  # "adverse", "favorable" or "negligible"


@dataclass
class Diagnosis:
    threshold: float
    assessable: bool
    changes: dict[str, MetricChange]
    paths: list[tuple[str, ...]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    decompositions: dict[str, Decomposition] = field(default_factory=dict)
    drivers: list[Driver] = field(default_factory=list)
    drivers_undefined_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    data_needed: list[str] = field(default_factory=list)
    prior_label: str | None = None
    current_label: str | None = None
    driver_statement: str = DRIVER_STATEMENT

    @property
    def adverse_drivers(self):
        return [driver for driver in self.drivers if driver.role == "adverse"]

    @property
    def favorable_drivers(self):
        return [driver for driver in self.drivers if driver.role == "favorable"]


def validate_threshold(threshold):
    if not 0 <= threshold < 1:
        raise ValueError("The materiality threshold must be at least 0 and below 1, such as 0.05.")


def assess(metric, prior, current, threshold, higher_is_better):
    """Classify a change. A change of exactly the threshold counts as material."""
    if prior is None or current is None:
        return MetricChange(metric, prior, current, None, Status.UNDEFINED)
    if prior == current:
        return MetricChange(metric, prior, current, 0.0 if prior else None, Status.STABLE)

    pct_change = None if prior == 0 else (current - prior) / prior
    if pct_change is not None and round(abs(pct_change), 12) < threshold:
        return MetricChange(metric, prior, current, pct_change, Status.STABLE)

    good = (current > prior) == higher_is_better
    return MetricChange(
        metric, prior, current, pct_change, Status.IMPROVED if good else Status.DETERIORATED
    )


def _describe(change):
    if change.status is Status.UNDEFINED:
        return f"{change.metric} is undefined in at least one period."
    if change.pct_change is None:
        return f"{change.metric} {change.status.value}, moving from {change.prior:.4g} to {change.current:.4g}."
    return f"{change.metric} {change.status.value} ({change.pct_change:+.1%})."


def _all_changes(prior, current, stages, threshold):
    prior_metrics = compute_metrics(prior)
    current_metrics = compute_metrics(current)
    changes = {
        metric: assess(
            metric, prior_metrics[metric], current_metrics[metric], threshold, HIGHER_IS_BETTER[metric]
        )
        for metric in prior_metrics
    }
    prior_rates = funnel_rates(prior, stages)
    current_rates = funnel_rates(current, stages)
    for name in prior_rates:
        changes[name] = assess(name, prior_rates[name], current_rates[name], threshold, True)
    return changes


def _factor(changes, name, exponent=1):
    return Factor(name, changes[name].prior, changes[name].current, exponent)


def _decompositions(changes, rate_names):
    roas, cpa, cpc, cvr = (changes[name] for name in ("roas", "cpa", "cpc", "cvr"))
    results = {
        "roas": decompose(
            "roas", roas.prior, roas.current, [_factor(changes, "aov"), _factor(changes, "cpa", -1)]
        ),
        "cpa": decompose(
            "cpa", cpa.prior, cpa.current, [_factor(changes, "cpc"), _factor(changes, "cvr", -1)]
        ),
        "cpc": decompose(
            "cpc",
            cpc.prior,
            cpc.current,
            [_factor(changes, "cpm"), _factor(changes, "ctr", -1)],
            scale=0.001,
        ),
    }
    if len(rate_names) > 1:
        results["cvr"] = decompose(
            "cvr", cvr.prior, cvr.current, [_factor(changes, name) for name in rate_names]
        )
    return results


def _leaf_drivers(changes, rate_names, cvr_split_defined):
    """Split the ROAS change across every lowest-level metric.

    ROAS = AOV * CTR * CVR * 1000 / CPM, with CVR replaced by its funnel rates
    when they are usable, so all contributions share one scale and add up.
    """
    roas = changes["roas"]
    leaves = [_factor(changes, "aov"), _factor(changes, "cpm", -1), _factor(changes, "ctr")]
    if cvr_split_defined:
        leaves += [_factor(changes, name) for name in rate_names]
    else:
        leaves.append(_factor(changes, "cvr"))

    result = decompose("roas", roas.prior, roas.current, leaves, scale=1000)
    if not result.defined:
        return [], result.undefined_reason

    drivers = []
    for item in result.contributions:
        change = changes[item.name]
        if change.status is Status.STABLE:
            role = "negligible"
        else:
            role = "adverse" if item.contribution < 0 else "favorable"
        drivers.append(
            Driver(item.name, item.contribution, item.weight, change.pct_change, role)
        )
    drivers.sort(key=lambda driver: abs(driver.contribution), reverse=True)
    return drivers, None


def _material(change):
    return change.status in (Status.IMPROVED, Status.DETERIORATED)


def _moved(changes, metric, children=()):
    """A metric is followed if it, or any of its inputs, changed materially."""
    return _material(changes[metric]) or any(_material(changes[child]) for child in children)


def _need(diagnosis, metric):
    text = DATA_NEEDED[metric]
    if text not in diagnosis.data_needed:
        diagnosis.data_needed.append(text)


def _investigate_cpc(diagnosis):
    changes = diagnosis.changes
    diagnosis.steps.append(Step("cpc", _describe(changes["cpm"]) + " " + _describe(changes["ctr"])))

    paths = []
    for metric in ("cpm", "ctr"):
        if _material(changes[metric]):
            paths.append(("roas", "cpa", "cpc", metric))
            _need(diagnosis, metric)
    if not paths:
        diagnosis.steps.append(
            Step("cpc", "CPM and CTR each stayed below the threshold, so neither is a material driver alone.")
        )
        paths.append(("roas", "cpa", "cpc"))
    return paths


def _investigate_cvr(diagnosis, rate_names):
    changes = diagnosis.changes
    if len(rate_names) < 2:
        diagnosis.steps.append(Step("cvr", "No funnel-stage data is available between clicks and purchases."))
        _need(diagnosis, "cvr")
        return [("roas", "cpa", "cvr")]

    moved = [name for name in rate_names if _material(changes[name])]
    for name in rate_names:
        diagnosis.steps.append(Step("cvr", _describe(changes[name])))

    if not moved:
        diagnosis.steps.append(
            Step("cvr", "No single funnel stage changed by the threshold or more.")
        )
        return [("roas", "cpa", "cvr")]

    for name in moved:
        _need(diagnosis, name)
    return [("roas", "cpa", "cvr", name) for name in moved]


def _investigate_cpa(diagnosis, rate_names):
    changes = diagnosis.changes
    diagnosis.steps.append(Step("cpa", _describe(changes["cpc"]) + " " + _describe(changes["cvr"])))

    stages = rate_names if len(rate_names) > 1 else ()
    paths = []
    if _moved(changes, "cpc", ("cpm", "ctr")):
        paths += _investigate_cpc(diagnosis)
    if _moved(changes, "cvr", stages):
        paths += _investigate_cvr(diagnosis, rate_names)
    if not paths:
        diagnosis.steps.append(
            Step("cpa", "CPC and CVR each stayed below the threshold, so neither is a material driver alone.")
        )
        paths.append(("roas", "cpa"))
    return paths


def _add_zero_warnings(diagnosis, prior, current):
    for label, totals in (("prior", prior), ("current", current)):
        if totals.spend == 0:
            diagnosis.warnings.append(f"The {label} period has zero spend, so ROAS, CPA and CPC are undefined.")
        if totals.purchases == 0:
            diagnosis.warnings.append(
                f"The {label} period has zero purchases, so AOV and CPA are undefined. "
                "Contributions that depend on them are not calculated."
            )
        if totals.link_clicks == 0:
            diagnosis.warnings.append(f"The {label} period has zero link clicks, so CPC and CVR are undefined.")
        if totals.impressions == 0:
            diagnosis.warnings.append(f"The {label} period has zero impressions, so CPM and CTR are undefined.")


def _add_funnel_warnings(diagnosis, prior, current, stages):
    for label, totals in (("prior", prior), ("current", current)):
        for name, rate in funnel_rates(totals, stages).items():
            if rate is not None and rate > 1:
                diagnosis.warnings.append(
                    f"{name} is above 100% in the {label} period, which suggests a tracking or attribution mismatch."
                )

    missing = [
        stage
        for stage in ("landing_page_views", "add_to_carts", "checkouts_initiated")
        if stage not in stages
    ]
    if missing:
        diagnosis.warnings.append(
            "These funnel stages are unavailable, so neighbouring stages were combined: "
            + ", ".join(missing)
            + "."
        )


def diagnose_totals(prior, current, threshold=DEFAULT_MATERIALITY_THRESHOLD,
                    min_purchases=MIN_PURCHASES_PER_PERIOD):
    """Run the decision tree on two sets of period totals."""
    validate_threshold(threshold)

    stages = available_funnel_stages(prior, current)
    rate_names = [rate_name(a, b) for a, b in zip(stages, stages[1:])]
    changes = _all_changes(prior, current, stages, threshold)
    diagnosis = Diagnosis(threshold=threshold, assessable=True, changes=changes)

    _add_zero_warnings(diagnosis, prior, current)
    _add_funnel_warnings(diagnosis, prior, current, stages)
    if min(prior.purchases, current.purchases) < min_purchases:
        diagnosis.warnings.append(
            f"At least one period has fewer than {min_purchases} purchases, so treat results as directional."
        )

    diagnosis.decompositions = _decompositions(changes, rate_names)
    cvr_split = diagnosis.decompositions.get("cvr")
    diagnosis.drivers, diagnosis.drivers_undefined_reason = _leaf_drivers(
        changes, rate_names, cvr_split is not None and cvr_split.defined
    )

    # Step 1: without spend in both periods there is no ROAS to compare.
    diagnosis.steps.append(Step("roas", _describe(changes["roas"])))
    if changes["roas"].status is Status.UNDEFINED:
        diagnosis.assessable = False
        return diagnosis

    # Step 2: split ROAS into AOV and CPA.
    diagnosis.steps.append(Step("roas", _describe(changes["aov"]) + " " + _describe(changes["cpa"])))
    if _material(changes["aov"]):
        diagnosis.paths.append(("roas", "aov"))
        _need(diagnosis, "aov")

    # Steps 3 to 5 depend on CPA rather than ROAS, so a change in AOV cannot hide
    # one in CPA. Losing all purchases leaves CPA undefined but is still a decline.
    lost_all_purchases = current.purchases == 0
    below_cpa = ("cpc", "cvr", "cpm", "ctr", *(rate_names if len(rate_names) > 1 else ()))
    if _moved(changes, "cpa", below_cpa) or lost_all_purchases:
        diagnosis.paths += _investigate_cpa(diagnosis, rate_names)

    if not diagnosis.paths:
        diagnosis.paths.append(("roas",))
    return diagnosis


def _add_data_warnings(diagnosis, data, prior_period, current_period, campaign):
    for period, label in ((prior_period, "prior"), (current_period, "current")):
        rows = select_rows(data, period, campaign)
        days = rows["date"].nunique()
        if days < period.days:
            diagnosis.warnings.append(
                f"The {label} period has data for {days} of {period.days} days. "
                "Missing days are counted as no delivery."
            )
        for stage in OPTIONAL_STAGES:
            missing = int(rows[stage].isna().sum()) if stage in rows else 0
            if missing:
                diagnosis.warnings.append(
                    f"{stage} is missing in {missing} row(s) of the {label} period, "
                    "so that stage is excluded rather than counted as zero."
                )

    if "attribution_setting" in data:
        settings = [
            set(select_rows(data, period, campaign)["attribution_setting"]) - {""}
            for period in (prior_period, current_period)
        ]
        if settings[0] != settings[1] or len(settings[0]) > 1:
            diagnosis.warnings.append(
                "Attribution settings differ, so purchases may not be like-for-like across periods."
            )
    else:
        diagnosis.warnings.append("The file has no attribution setting, so comparability could not be checked.")

    if (data["date"].max() - current_period.end).days < MAX_ATTRIBUTION_LAG_DAYS:
        diagnosis.warnings.append(
            "The current period is recent, so late-attributed purchases may still be added."
        )

    if campaign is None and data["campaign"].nunique() > 1:
        diagnosis.warnings.append(
            "Results combine several campaigns. A shift in spend between them can change these metrics "
            "and is not separated here. Select one campaign to inspect it."
        )


def run_diagnosis(data, granularity, threshold=DEFAULT_MATERIALITY_THRESHOLD, prior_period=None,
                  current_period=None, campaign=None, min_purchases=MIN_PURCHASES_PER_PERIOD):
    """Diagnose a canonical dataset. Defaults to the latest two complete periods."""
    if campaign is not None and campaign not in set(data["campaign"]):
        raise ValueError(f"Campaign not found: {campaign}")

    if prior_period is None or current_period is None:
        prior_period, current_period = latest_two_complete(data, granularity)
    if prior_period.end >= current_period.start:
        raise ValueError("The prior period must end before the current period starts.")

    diagnosis = diagnose_totals(
        aggregate(data, prior_period, campaign),
        aggregate(data, current_period, campaign),
        threshold,
        min_purchases,
    )
    diagnosis.prior_label = prior_period.label
    diagnosis.current_label = current_period.label
    _add_data_warnings(diagnosis, data, prior_period, current_period, campaign)
    return diagnosis
