"""Turn engine results into display-ready values, with no Streamlit dependency."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from funnel_magnet.diagnose import Status
from funnel_magnet.metrics import OPTIONAL_STAGES

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"

KPI_ORDER = ("roas", "aov", "cpa", "cpc", "cvr", "cpm", "ctr")
CURRENCY_METRICS = ("aov", "cpa", "cpc", "cpm")
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "INR": "₹"}

METRIC_LABELS = {
    "roas": "ROAS",
    "aov": "AOV",
    "cpa": "CPA",
    "cpc": "CPC",
    "cvr": "CVR",
    "cpm": "CPM",
    "ctr": "CTR",
}
STAGE_LABELS = {
    "link_clicks": "Link clicks",
    "landing_page_views": "Landing page views",
    "add_to_carts": "Add to cart",
    "checkouts_initiated": "Checkout",
    "purchases": "Purchase",
}
STATUS_LABELS = {
    Status.IMPROVED: "Better",
    Status.DETERIORATED: "Worse",
    Status.STABLE: "Stable",
    Status.UNDEFINED: "Undefined",
}

UNDEFINED = "n/a"

DECISION_SUPPORT_NOTICE = (
    "Funnel Magnet provides decision support. Its results show what changed and suggest checks "
    "for a person to review. They are not automatically verified causal conclusions."
)

# First match wins, so the funnel group is checked before the generic ones.
WARNING_GROUPS = (
    ("Funnel data", ("funnel", "stage", "above 100%", *OPTIONAL_STAGES)),
    ("Low purchase volume", ("directional",)),
    ("Attribution", ("attribution", "late-attributed")),
    ("Undefined metrics", ("zero spend", "zero purchases", "zero link clicks", "zero impressions")),
)
OTHER_WARNINGS = "Other data checks"


@dataclass(frozen=True)
class Sample:
    name: str
    file: str
    description: str

    @property
    def path(self):
        return SAMPLES_DIR / self.file


@dataclass(frozen=True)
class Kpi:
    metric: str
    label: str
    prior: str
    current: str
    change: str
    status: Status


@dataclass
class TreeNode:
    metric: str
    label: str
    prior: str
    current: str
    change: str
    status: Status
    on_path: bool
    badge: str | None = None
    children: list["TreeNode"] = field(default_factory=list)


@dataclass(frozen=True)
class Findings:
    observations: list[str]
    drivers: list[str]
    evidence_needed: list[str]


@dataclass(frozen=True)
class WaterfallStep:
    label: str
    value: float
    measure: str  # "absolute", "relative" or "total"


def load_samples():
    entries = json.loads((SAMPLES_DIR / "expected_results.json").read_text(encoding="utf-8"))
    return [
        Sample(
            name=entry["file"].removesuffix(".csv")[3:].replace("_", " ").capitalize()
            .replace("Cpm", "CPM").replace("Ctr", "CTR").replace("Aov", "AOV"),
            file=entry["file"],
            description=entry["description"],
        )
        for entry in entries
    ]


def metric_label(metric):
    if metric in METRIC_LABELS:
        return METRIC_LABELS[metric]
    return humanize(metric)


def format_value(metric, value, currency=None):
    if value is None:
        return UNDEFINED
    if metric in ("cvr", "ctr"):
        return f"{value:.2%}"
    if "_to_" in metric:
        return f"{value:.1%}"
    if metric in CURRENCY_METRICS:
        symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} " if currency else "")
        return f"{symbol}{value:,.2f}"
    return f"{value:.2f}"


def format_change(pct_change):
    return UNDEFINED if pct_change is None else f"{pct_change:+.1%}"


def format_threshold(threshold):
    return f"{threshold * 100:g}%"


def humanize(text):
    """Replace engine column names in a message with display labels."""
    for stage, label in STAGE_LABELS.items():
        text = text.replace(f"{stage}_to_", f"{label} → ")
    for stage, label in STAGE_LABELS.items():
        if stage != "purchases":  # Also an everyday word in messages.
            text = text.replace(stage, label)
    return text.replace("→ purchases", "→ Purchase").replace("purchase_value", "Purchase value")


def kpi_cards(diagnosis, currency=None):
    cards = []
    for metric in KPI_ORDER:
        change = diagnosis.changes[metric]
        cards.append(
            Kpi(
                metric,
                metric_label(metric),
                format_value(metric, change.prior, currency),
                format_value(metric, change.current, currency),
                format_change(change.pct_change),
                change.status,
            )
        )
    return cards


def driver_badges(diagnosis):
    """Name the main mathematical drivers. Only engine drivers can get a badge."""
    badges = {}
    adverse = diagnosis.adverse_drivers
    favorable = diagnosis.favorable_drivers
    for rank, driver in enumerate(adverse):
        badges[driver.metric] = "Main driver" if rank == 0 else "Driver"
    if favorable:
        badges[favorable[0].metric] = "Offset" if adverse else "Main driver"
    return badges


def rate_metrics(diagnosis):
    return [metric for metric in diagnosis.changes if "_to_" in metric]


def build_tree(diagnosis, currency=None):
    """Lay out the decision tree, marking the path the engine actually took."""
    on_path = {node for path in diagnosis.paths for node in path}
    badges = driver_badges(diagnosis)

    def node(metric, children=()):
        change = diagnosis.changes[metric]
        return TreeNode(
            metric=metric,
            label=metric_label(metric),
            prior=format_value(metric, change.prior, currency),
            current=format_value(metric, change.current, currency),
            change=format_change(change.pct_change),
            status=change.status,
            on_path=metric in on_path,
            badge=badges.get(metric),
            children=list(children),
        )

    # The funnel has only one rate when no stages exist between clicks and purchases.
    rates = rate_metrics(diagnosis)
    stage_nodes = [node(rate) for rate in rates] if len(rates) > 1 else []

    return node(
        "roas",
        [
            node("aov"),
            node(
                "cpa",
                [
                    node("cpc", [node("cpm"), node("ctr")]),
                    node("cvr", stage_nodes),
                ],
            ),
        ],
    )


def _observation(change, currency):
    label = metric_label(change.metric)
    if change.status is Status.UNDEFINED:
        return f"{label} is undefined in at least one period."

    prior = format_value(change.metric, change.prior, currency)
    current = format_value(change.metric, change.current, currency)
    if change.pct_change is None:
        return f"{label} moved from {prior} to {current}."
    if change.pct_change == 0:
        return f"{label} was unchanged at {current}."
    verb = "rose" if change.pct_change > 0 else "fell"
    return f"{label} {verb} {abs(change.pct_change):.1%}, from {prior} to {current}."


def _changes_text(diagnosis, metrics):
    return ", ".join(
        f"{metric_label(metric)} ({format_change(diagnosis.changes[metric].pct_change)})"
        for metric in metrics
    )


def _largest_driver(role, driver):
    return (
        f"The largest {role} driver is {metric_label(driver.metric)}, which contributed "
        f"{driver.contribution:+.2f} to ROAS ({driver.weight:.0%} of all movement)."
    )


def mathematical_diagnosis(diagnosis, currency=None):
    """Describe the tree result in sentences, written entirely from engine values."""
    changes = diagnosis.changes
    if not diagnosis.assessable:
        return ["ROAS can't be compared because at least one period has no spend."]

    nodes = {node for path in diagnosis.paths for node in path}
    sentences = [_observation(changes["roas"], currency)]

    if "aov" in nodes:
        sentences.append(_observation(changes["aov"], currency))
    if "cpa" in nodes:
        sentences.append(
            f"The CPA result is mathematically explained by {_changes_text(diagnosis, ('cpc', 'cvr'))}."
        )
    if "cpc" in nodes:
        sentences.append(
            f"The CPC result is mathematically explained by {_changes_text(diagnosis, ('cpm', 'ctr'))}."
        )
    declined = [metric for metric in rate_metrics(diagnosis) if metric in nodes]
    if declined:
        sentences.append(
            f"The CVR result is mathematically explained by these funnel stages: "
            f"{_changes_text(diagnosis, declined)}."
        )

    adverse, favorable = diagnosis.adverse_drivers, diagnosis.favorable_drivers
    if diagnosis.drivers_undefined_reason:
        sentences.append(
            f"Contributions were not calculated. {humanize(diagnosis.drivers_undefined_reason)}"
        )
    elif adverse:
        sentences.append(_largest_driver("adverse", adverse[0]))
    if favorable and not diagnosis.drivers_undefined_reason:
        sentences.append(_largest_driver("favorable", favorable[0]))
    if nodes == {"roas"}:
        sentences.append(
            f"No branch needed investigation at the {format_threshold(diagnosis.threshold)} threshold."
        )

    sentences.append("This shows how ROAS changed arithmetically, not why.")
    return sentences


def _driver_sentence(driver):
    return (
        f"{metric_label(driver.metric)} contributed {driver.contribution:+.2f} to ROAS "
        f"({driver.weight:.0%} of all movement)."
    )


def build_findings(diagnosis, currency=None):
    """Sort results into observations and drivers. No cause is ever stated."""
    metrics = [*KPI_ORDER, *rate_metrics(diagnosis)]
    observations = [_observation(diagnosis.changes["roas"], currency)]
    observations += [
        _observation(diagnosis.changes[metric], currency)
        for metric in metrics[1:]
        if diagnosis.changes[metric].status is not Status.STABLE
    ]
    stable = [
        metric_label(metric)
        for metric in metrics[1:]
        if diagnosis.changes[metric].status is Status.STABLE
    ]
    if stable:
        observations.append(
            f"Changed by less than {format_threshold(diagnosis.threshold)}: {', '.join(stable)}."
        )

    if diagnosis.drivers_undefined_reason:
        drivers = [f"Contributions were not calculated. {humanize(diagnosis.drivers_undefined_reason)}"]
    else:
        material = [*diagnosis.adverse_drivers, *diagnosis.favorable_drivers]
        drivers = [_driver_sentence(driver) for driver in material] or [
            f"No lowest-level metric changed by {format_threshold(diagnosis.threshold)} or more."
        ]

    return Findings(observations, drivers, diagnosis.data_needed)


def waterfall_steps(diagnosis):
    """Return the waterfall as steps, or None when contributions are undefined.

    Metrics below the threshold are combined so the steps still sum to the change.
    """
    if not diagnosis.drivers:
        return None

    roas = diagnosis.changes["roas"]
    steps = [WaterfallStep(f"{diagnosis.prior_label or 'Prior'}", roas.prior, "absolute")]
    negligible = [d.contribution for d in diagnosis.drivers if d.role == "negligible"]
    for driver in diagnosis.drivers:
        if driver.role != "negligible":
            steps.append(WaterfallStep(metric_label(driver.metric), driver.contribution, "relative"))
    if negligible:
        steps.append(WaterfallStep("Other (below threshold)", sum(negligible), "relative"))
    steps.append(WaterfallStep(f"{diagnosis.current_label or 'Current'}", roas.current, "total"))
    return steps


def driver_table(diagnosis):
    return [
        {
            "Metric": metric_label(driver.metric),
            "Change": format_change(driver.pct_change),
            "ROAS contribution": f"{driver.contribution:+.3f}",
            "Share of movement": f"{driver.weight:.0%}",
            "Role": driver.role.capitalize(),
        }
        for driver in diagnosis.drivers
    ]


def group_warnings(messages):
    """Group data quality messages for display, keeping their order within a group."""
    groups = {}
    for message in messages:
        lowered = message.lower()
        group = next(
            (name for name, words in WARNING_GROUPS if any(word in lowered for word in words)),
            OTHER_WARNINGS,
        )
        groups.setdefault(group, []).append(humanize(message))
    return groups
