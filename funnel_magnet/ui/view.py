"""Turn engine results into display-ready values, with no Streamlit dependency."""

import json
import re
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
    "cvr": "purchases per link click",
    "cpm": "cost per 1,000 impressions",
    "ctr": "link click-through rate",
    # Funnel stages are ratios of Meta event counts, not shares of unique people.
    "link_clicks_to_landing_page_views": "landing page views per link click",
    "landing_page_views_to_add_to_carts": "add-to-cart events per landing page view",
    "add_to_carts_to_checkouts_initiated": "checkouts started per add-to-cart event",
    "checkouts_initiated_to_purchases": "purchases per checkout started",
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
        "Fewer purchases are recorded for each link click, so more ad spend is needed for "
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
    # A funnel ratio counts Meta events, so a fall may be real or a tracking issue.
    "link_clicks_to_landing_page_views": (
        "Fewer landing page views are recorded for each paid link click. This may mean "
        "paid clicks that never reach the page, or a tracking issue, both of which should "
        "be investigated."
    ),
    "landing_page_views_to_add_to_carts": (
        "Fewer add-to-cart events are recorded for each landing page view. This may mean "
        "fewer people choosing to buy from the page, or a tracking issue, both of which "
        "should be investigated."
    ),
    "add_to_carts_to_checkouts_initiated": (
        "Fewer checkouts are started for each add-to-cart event. This may indicate potential "
        "sales lost between cart and checkout, or a tracking issue, both of which should be "
        "investigated."
    ),
    "checkouts_initiated_to_purchases": (
        "Fewer purchases are recorded for each checkout started. This may indicate lost "
        "sales during checkout or a tracking issue, both of which should be investigated."
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


# ---------------------------------------------------------------- KPI glossary
# Each entry matches metrics.py exactly: full name, what it means, and its formula.
# CVR is per link click, not per visitor, and CPM is per 1,000 impressions.
GLOSSARY = {
    "roas": ("Return on ad spend", "How much recorded sales value each unit of ad spend brought back.",
             "Purchase value ÷ ad spend"),
    "aov": ("Average order value", "The average value of each recorded purchase.", "Purchase value ÷ purchases"),
    "cpa": ("Cost per purchase", "How much ad spend it took to get one recorded purchase. Also called cost per acquisition.",
            "Ad spend ÷ purchases"),
    "cpc": ("Cost per link click", "How much each click on an ad's link cost.", "Ad spend ÷ link clicks"),
    "cvr": ("Conversion rate", "How many recorded purchases each link click produced.", "Purchases ÷ link clicks"),
    "cpm": ("Cost per 1,000 impressions", "What it cost to show the ads 1,000 times.", "Ad spend ÷ impressions × 1,000"),
    "ctr": ("Link click-through rate", "How often an ad being shown led to a click on its link.", "Link clicks ÷ impressions"),
    "link_clicks_to_landing_page_views": ("Landing page views per link click",
                                          "How many landing page loads were recorded for each link click.",
                                          "Landing page views ÷ link clicks"),
    "landing_page_views_to_add_to_carts": ("Add-to-carts per landing page view",
                                           "How many add-to-cart events were recorded for each landing page view.",
                                           "Adds to cart ÷ landing page views"),
    "add_to_carts_to_checkouts_initiated": ("Checkouts started per add to cart",
                                            "How many checkouts were started for each add-to-cart event.",
                                            "Checkouts initiated ÷ adds to cart"),
    "checkouts_initiated_to_purchases": ("Purchases per checkout started",
                                         "How many purchases were recorded for each checkout started.",
                                         "Purchases ÷ checkouts initiated"),
}
# How the KPIs fit together, exactly as the decision tree splits them.
KPI_RELATIONSHIPS = (
    "ROAS = AOV ÷ CPA",
    "CPA = CPC ÷ CVR",
    "CPC = CPM ÷ (1,000 × CTR)",
    "CVR = the funnel steps multiplied together, from link click to purchase",
)
# Meta counts events, so funnel steps are ratios of event counts, not of unique people.
GLOSSARY_NOTE = (
    "All figures come from the Meta Ads export. Purchases and purchase value are the ones Meta "
    "credited to the ads, and funnel steps compare counts of recorded events, not unique people."
)


def full_name(metric):
    """Like "Cost per 1,000 impressions (CPM)", or a funnel step's plain name."""
    if metric in METRIC_LABELS:
        return f"{GLOSSARY[metric][0]} ({METRIC_LABELS[metric]})"
    return GLOSSARY[metric][0] if metric in GLOSSARY else metric_label(metric)


def _amount(metric, value, currency):
    return format_value(metric, value, currency)


def kpi_example(change, currency=None):
    """One sentence with this comparison's own figures, such as
    "Showing the ads 1,000 times cost €13.95, up from €11.79." Empty when undefined."""
    metric = change.metric
    if change.current is None or change.prior is None:
        return ""
    now, before = _amount(metric, change.current, currency), _amount(metric, change.prior, currency)
    unit = format_value("aov", 1, currency).replace("1.00", "1")
    per_hundred = lambda value: f"{value * 100:.2f}".rstrip("0").rstrip(".")
    symbol = unit[:-1]
    money = lambda value: f"{symbol}{value:,.2f}"
    if unit == "1":
        # No currency in the file, so speak of units of spend.
        unit = "unit"
    templates = {
        "roas": f"Each {unit} of ad spend brought back {money(change.current)} in recorded sales, compared with {money(change.prior)} before.",
        "aov": f"The average recorded purchase was worth {now}, compared with {before} before.",
        "cpa": f"Each recorded purchase cost {now} in ad spend, compared with {before} before.",
        "cpc": f"Each link click cost {now}, compared with {before} before.",
        "cvr": f"{per_hundred(change.current)} purchases were recorded for every 100 link clicks, compared with {per_hundred(change.prior)} before.",
        "cpm": f"Showing the ads 1,000 times cost {now}, compared with {before} before.",
        "ctr": f"{per_hundred(change.current)} link clicks were recorded for every 100 impressions, compared with {per_hundred(change.prior)} before.",
        "link_clicks_to_landing_page_views": "{} landing page views were recorded for every 100 link clicks, compared with {} before.",
        "landing_page_views_to_add_to_carts": "{} add-to-cart events were recorded for every 100 landing page views, compared with {} before.",
        "add_to_carts_to_checkouts_initiated": "{} checkouts were started for every 100 add-to-cart events, compared with {} before.",
        "checkouts_initiated_to_purchases": "{} purchases were recorded for every 100 checkouts started, compared with {} before.",
    }
    template = templates.get(metric, "")
    if "_to_" in metric:
        return template.format(per_hundred(change.current), per_hundred(change.prior))
    return template


# ---------------------------------------------------------------- Plain-English data warnings
# Plain titles for the warning groups, in the same order and with the same meaning.
GROUP_TITLES = {
    "Funnel data": "Website funnel data",
    "Low purchase volume": "Few purchases",
    "Attribution": "How purchases are credited to ads (attribution)",
    "Undefined metrics": "Figures that can't be calculated",
    OTHER_WARNINGS: "Other data notes",
}
_UNDEFINED = {
    "spend": "has no ad spend, so return on ad spend and the costs per purchase and per click can't be calculated for it.",
    "purchases": ("recorded no purchases, so average order value and cost per purchase can't be calculated, "
                  "and their effect on return on ad spend isn't measured."),
    "link clicks": "recorded no link clicks, so cost per click and the conversion rate can't be calculated for it.",
    "impressions": "recorded no impressions, so cost per 1,000 impressions and click-through rate can't be calculated for it.",
}
PLAIN_WARNINGS = (
    (re.compile(r"^The (prior|current) period has zero (spend|purchases|link clicks|impressions)"),
     lambda m: f"The {m[1]} period {_UNDEFINED[m[2]]}"),
    (re.compile(r"^(.+) is above 100% in the (prior|current) period"),
     lambda m: (f"The {m[1]} step shows more than 100% in the {m[2]} period, which can't happen in reality and "
                "suggests a tracking or attribution problem. Treat this step with caution.")),
    (re.compile(r"^These funnel stages are unavailable, so neighbouring stages were combined: (.+)\.$"),
     lambda m: f"Some funnel steps aren't in the file ({m[1]}), so the steps either side of them are measured together."),
    (re.compile(r"^At least one period has fewer than (\d+) purchases"),
     lambda m: (f"At least one period has fewer than {m[1]} purchases. With so few sales, percentage changes can "
                "swing a lot, so treat the results as a rough indication only.")),
    (re.compile(r"^The (prior|current) period has data for (\d+) of (\d+) days"),
     lambda m: (f"The {m[1]} period only has data for {m[2]} of its {m[3]} days. The missing days are treated as "
                "days with no advertising, which can make that period look smaller than it was.")),
    (re.compile(r"^(.+) is missing in (\d+) row\(s\) of the (prior|current) period"),
     lambda m: (f"{m[1]} figures are missing in {m[2]} row(s) of the {m[3]} period, so that funnel step is left "
                "out of the comparison rather than counted as zero.")),
    (re.compile(r"^Attribution settings differ"),
     lambda m: ("The two periods use different attribution settings (the rules Meta uses to credit purchases to "
                "ads), so their purchase figures may not be directly comparable.")),
    (re.compile(r"^The file has no attribution setting"),
     lambda m: ("The export doesn't state its attribution setting (the rules Meta uses to credit purchases to ads), "
                "so it can't be confirmed that both periods were counted the same way.")),
    (re.compile(r"^The current period ended (\d+ days?) before this analysis, inside the (\d+)-day attribution window"),
     lambda m: (f"The current period ended {m[1]} before this analysis, inside the {m[2]}-day attribution window. "
                "Meta can still credit more purchases to it, so it may look weaker now than it will later.")),
    (re.compile(r"^The current period ended (\d+ days?) before this analysis, and the file doesn't state"),
     lambda m: (f"The current period ended {m[1]} before this analysis, and the export doesn't state its "
                "attribution window, so it can't be confirmed that every purchase Meta will credit to it is included.")),
    (re.compile(r"^Results combine several campaigns"),
     lambda m: ("These results add all campaigns together. If spend moved between campaigns, the totals can change "
                "even when no single campaign did. Pick one campaign in the sidebar to look at it on its own.")),
    (re.compile(r"^The file has no campaign column"),
     lambda m: ("The file has no campaign column, so the whole account is analysed as one total. "
                "Campaign-by-campaign results aren't available.")),
    (re.compile(r"^(.+) differs from the recalculated value in (\d+) row\(s\)"),
     lambda m: (f"Meta's own {m[1]} figure differs from Funnel Magnet's calculation in {m[2]} row(s), probably "
                "because it is defined differently. Funnel Magnet's calculation is used.")),
)


def plain_warning(message):
    """The same warning in plain English, keeping its substance. Unknown messages are unchanged."""
    text = humanize(message)
    for pattern, rewrite in PLAIN_WARNINGS:
        match = pattern.search(text)
        if match:
            return rewrite(match)
    return text


def plain_warning_groups(messages):
    """group_warnings with plain titles and plain messages, in the same order."""
    groups = {}
    for group, items in group_warnings(messages).items():
        groups[GROUP_TITLES.get(group, group)] = [plain_warning(item) for item in items]
    return groups
