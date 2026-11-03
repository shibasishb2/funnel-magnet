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

# Plain names and why a worsening matters, for marketers. The arithmetic behind each:
# ROAS = AOV / CPA, CPA = CPC / CVR, CPC = CPM / CTR, and CVR is the product of the
# funnel stages.
PLAIN_NAMES = {
    "roas": "return on ad spend",
    "aov": "average order value",
    "cpa": "cost per purchase",
    "cpc": "cost per click",
    "cvr": "conversion rate",
    "cpm": "cost per 1,000 impressions",
    "ctr": "click-through rate",
    "link_clicks_to_landing_page_views": "share of clicks that reach the landing page",
    "landing_page_views_to_add_to_carts": "share of visitors who add to cart",
    "add_to_carts_to_checkouts_initiated": "share of carts that reach checkout",
    "checkouts_initiated_to_purchases": "share of checkouts that end in a purchase",
}
WHY_IT_MATTERS = {
    "roas": (
        "Every unit of ad spend now brings back less revenue, so advertising is less profitable "
        "and harder to scale without losing money."
    ),
    "aov": (
        "Each order is worth less, so the same advertising cost per sale earns less revenue "
        "and less margin."
    ),
    "cpa": (
        "Each sale costs more to win, which eats into the margin on every order unless "
        "orders become more valuable."
    ),
    "cpc": (
        "Each ad click costs more, so the same budget brings fewer potential customers to "
        "the site and each sale costs more."
    ),
    "cvr": (
        "A smaller share of ad clicks turn into purchases, so more ad spend is needed for "
        "every sale."
    ),
    "cpm": (
        "Reaching people costs more, so every click and every sale costs more unless people "
        "click and buy more often."
    ),
    "ctr": (
        "Fewer people who see the ads click, so more paid impressions are needed for each "
        "visit, which raises the cost of every click and sale."
    ),
    "link_clicks_to_landing_page_views": (
        "More paid clicks never reach the landing page, so budget is spent on visits that "
        "never happen."
    ),
    "landing_page_views_to_add_to_carts": (
        "Fewer visitors add products to the cart, so more paid traffic is needed for each "
        "sale."
    ),
    "add_to_carts_to_checkouts_initiated": (
        "Fewer shoppers move from the cart to checkout, so sales slip away after customers "
        "have shown they want to buy."
    ),
    "checkouts_initiated_to_purchases": (
        "Fewer shoppers who start checkout finish paying, so sales are lost at the last "
        "step, after the advertising cost has already been paid."
    ),
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


POSITIVE = "Positive contributor"
ADVERSE = "Adverse contributor"
OFFSETTING = "Offsetting driver"
NEGLIGIBLE = "Negligible"


def roas_rose(diagnosis):
    roas = diagnosis.changes["roas"]
    return roas.prior is None or roas.current is None or roas.current >= roas.prior


def contribution_label(diagnosis, driver):
    """Name a driver by its effect: moving with the ROAS change, or offsetting it."""
    if driver.role == "negligible":
        return NEGLIGIBLE
    if (driver.role == "favorable") != roas_rose(diagnosis):
        return OFFSETTING
    return POSITIVE if driver.role == "favorable" else ADVERSE


def driver_groups(diagnosis):
    """Return (material drivers moving with ROAS, those offsetting it), largest first."""
    material = [d for d in diagnosis.drivers if d.role != "negligible"]
    offsetting = [d for d in material if contribution_label(diagnosis, d) == OFFSETTING]
    return [d for d in material if d not in offsetting], offsetting


def driver_badges(diagnosis):
    """Badge the drivers. The main driver is the largest one moving with ROAS."""
    with_roas, offsetting = driver_groups(diagnosis)
    badges = {d.metric: "Main driver" if rank == 0 else "Driver" for rank, d in enumerate(with_roas)}
    badges.update({d.metric: "Offset" for d in offsetting})
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


def _explained(diagnosis, metric, inputs, lead=""):
    changes = f"{lead}{_changes_text(diagnosis, inputs)}"
    if diagnosis.changes[metric].status is Status.STABLE:
        return (
            f"{metric_label(metric)} changed by less than {format_threshold(diagnosis.threshold)}, "
            f"with offsetting changes in {lead or 'its inputs: '}{_changes_text(diagnosis, inputs)}."
        )
    return f"The {metric_label(metric)} result is mathematically explained by {changes}."


def _changes_text(diagnosis, metrics):
    return ", ".join(
        f"{metric_label(metric)} ({format_change(diagnosis.changes[metric].pct_change)})"
        for metric in metrics
    )


def _largest_driver(label, driver):
    return (
        f"The largest {label.lower()} is {metric_label(driver.metric)}, which contributed "
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
        sentences.append(_explained(diagnosis, "cpa", ("cpc", "cvr")))
    if "cpc" in nodes:
        sentences.append(_explained(diagnosis, "cpc", ("cpm", "ctr")))
    stages = [metric for metric in rate_metrics(diagnosis) if metric in nodes]
    if stages:
        sentences.append(_explained(diagnosis, "cvr", stages, "these funnel stages: "))

    with_roas, offsetting = driver_groups(diagnosis)
    if diagnosis.drivers_undefined_reason:
        sentences.append(
            f"Contributions were not calculated. {humanize(diagnosis.drivers_undefined_reason)}"
        )
    else:
        if with_roas:
            sentences.append(_largest_driver(contribution_label(diagnosis, with_roas[0]), with_roas[0]))
        if offsetting:
            sentences.append(_largest_driver(OFFSETTING, offsetting[0]))
    if nodes == {"roas"}:
        sentences.append(
            f"No branch needed investigation at the {format_threshold(diagnosis.threshold)} threshold."
        )

    sentences.append("This shows how ROAS changed arithmetically, not why.")
    return sentences


def _driver_sentence(diagnosis, driver):
    return (
        f"{contribution_label(diagnosis, driver)}: {metric_label(driver.metric)} contributed "
        f"{driver.contribution:+.2f} to ROAS "
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
        with_roas, offsetting = driver_groups(diagnosis)
        drivers = [_driver_sentence(diagnosis, driver) for driver in [*with_roas, *offsetting]] or [
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
            "Effect on ROAS": contribution_label(diagnosis, driver),
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
