"""Metric definitions, all recalculated from totals.

Every click-based metric uses link clicks. Reported ratios are never averaged.
A metric with a zero or missing denominator is None, never 0.
"""

from dataclasses import dataclass

FUNNEL_STAGES = (
    "link_clicks",
    "landing_page_views",
    "add_to_carts",
    "checkouts_initiated",
    "purchases",
)

OPTIONAL_STAGES = FUNNEL_STAGES[1:-1]

HIGHER_IS_BETTER = {
    "roas": True,
    "aov": True,
    "cpa": False,
    "cpc": False,
    "cvr": True,
    "cpm": False,
    "ctr": True,
}


@dataclass(frozen=True)
class Totals:
    spend: float
    impressions: float
    link_clicks: float
    purchases: float
    purchase_value: float
    landing_page_views: float | None = None
    add_to_carts: float | None = None
    checkouts_initiated: float | None = None


def safe_ratio(numerator, denominator, scale=1.0):
    """Return numerator / denominator, or None when that is undefined."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return scale * numerator / denominator


def compute_metrics(totals):
    return {
        "roas": safe_ratio(totals.purchase_value, totals.spend),
        "aov": safe_ratio(totals.purchase_value, totals.purchases),
        "cpa": safe_ratio(totals.spend, totals.purchases),
        "cpc": safe_ratio(totals.spend, totals.link_clicks),
        "cvr": safe_ratio(totals.purchases, totals.link_clicks),
        "cpm": safe_ratio(totals.spend, totals.impressions, scale=1000),
        "ctr": safe_ratio(totals.link_clicks, totals.impressions),
    }


def available_funnel_stages(*periods):
    """Return the funnel stages that have data in every given period."""
    return tuple(
        stage
        for stage in FUNNEL_STAGES
        if all(getattr(totals, stage) is not None for totals in periods)
    )


def rate_name(from_stage, to_stage):
    return f"{from_stage}_to_{to_stage}"


def funnel_rates(totals, stages):
    """Return the step rate between each adjacent pair of stages.

    The rates multiply to CVR because each stage cancels with the next.
    """
    return {
        rate_name(from_stage, to_stage): safe_ratio(
            getattr(totals, to_stage), getattr(totals, from_stage)
        )
        for from_stage, to_stage in zip(stages, stages[1:])
    }
