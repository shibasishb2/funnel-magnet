"""Generate the synthetic Meta Ads sample datasets and their expected results.

All data is invented. Each scenario changes one or two inputs in two steps, on
1 August and 24 August 2026, so both July vs August and the final two weeks show
the problem. Expected results are declared by hand here, not taken from the engine.

Run: python data/samples/generate_samples.py
"""

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

START = date(2026, 6, 1)  # A Monday, so the first week and month are both complete.
END = date(2026, 8, 31)  # The month end, so August is complete. The last full week ends on the 30th.
STEP_DATES = (date(2026, 8, 1), date(2026, 8, 24))
SEED = 2026
NOISE = 0.015

BASE = {
    "cpm": 10.0,
    "ctr": 0.015,
    "click_to_lpv": 0.85,
    "lpv_to_cart": 0.15,
    "cart_to_checkout": 0.60,
    "checkout_to_purchase": 0.70,
    "aov": 60.0,
}

CAMPAIGNS = (
    ("Prospecting - Broad", 500.0, 1.0),
    ("Retargeting - Site Visitors", 300.0, 1.1),
    ("Seasonal - Summer Offer", 200.0, 0.9),
)

HEADERS = (
    "Day",
    "Campaign name",
    "Amount spent (EUR)",
    "Impressions",
    "Link clicks",
    "Landing page views",
    "Adds to cart",
    "Checkouts initiated",
    "Purchases",
    "Purchases conversion value",
    "Purchase ROAS (return on ad spend)",
    "Cost per purchase",
    "CPC (cost per link click)",
    "CPM (cost per 1,000 impressions)",
    "CTR (link click-through rate)",
    "Attribution setting",
)

CPM_PATH = ["roas", "cpa", "cpc", "cpm"]

SCENARIOS = (
    {
        "file": "01_cpm_inflation.csv",
        "description": "CPM rises while every other input holds.",
        "shifts": {"cpm": 1.18},
        "expected_paths": [CPM_PATH],
        "expected_roas_status": "deteriorated",
        "expected_top_adverse_driver": "cpm",
    },
    {
        "file": "02_ctr_decline.csv",
        "description": "CTR falls while every other input holds.",
        "shifts": {"ctr": 0.82},
        "expected_paths": [["roas", "cpa", "cpc", "ctr"]],
        "expected_roas_status": "deteriorated",
        "expected_top_adverse_driver": "ctr",
    },
    {
        "file": "03_landing_page_issue.csv",
        "description": "Fewer link clicks become landing page views.",
        "shifts": {"click_to_lpv": 0.82},
        "expected_paths": [["roas", "cpa", "cvr", "link_clicks_to_landing_page_views"]],
        "expected_roas_status": "deteriorated",
        "expected_top_adverse_driver": "link_clicks_to_landing_page_views",
    },
    {
        "file": "04_checkout_friction.csv",
        "description": "Fewer initiated checkouts become purchases.",
        "shifts": {"checkout_to_purchase": 0.82},
        "expected_paths": [["roas", "cpa", "cvr", "checkouts_initiated_to_purchases"]],
        "expected_roas_status": "deteriorated",
        "expected_top_adverse_driver": "checkouts_initiated_to_purchases",
    },
    {
        "file": "05_aov_decline.csv",
        "description": "AOV falls while CPA holds.",
        "shifts": {"aov": 0.85},
        "expected_paths": [["roas", "aov"]],
        "expected_roas_status": "deteriorated",
        "expected_top_adverse_driver": "aov",
    },
    {
        "file": "06_masked_problem.csv",
        "description": "Higher AOV offsets a higher CPM, so ROAS looks stable.",
        "shifts": {"aov": 1.20, "cpm": 1.18},
        "expected_paths": [CPM_PATH],
        "expected_roas_status": "stable",
        "expected_top_adverse_driver": "cpm",
    },
    {
        "file": "07_healthy_control.csv",
        "description": "No input changes.",
        "shifts": {},
        "expected_paths": [["roas"]],
        "expected_roas_status": "stable",
        "expected_top_adverse_driver": None,
    },
)


def _value(name, day, shifts, rng):
    steps = sum(day >= step for step in STEP_DATES)
    return BASE[name] * shifts.get(name, 1.0) ** steps * rng.gauss(1, NOISE)


def _row(day, campaign, daily_spend, ctr_factor, shifts, rng):
    spend = round(daily_spend * rng.gauss(1, NOISE), 2)
    impressions = round(spend / _value("cpm", day, shifts, rng) * 1000)
    clicks = round(impressions * _value("ctr", day, shifts, rng) * ctr_factor)
    page_views = round(clicks * _value("click_to_lpv", day, shifts, rng))
    carts = round(page_views * _value("lpv_to_cart", day, shifts, rng))
    checkouts = round(carts * _value("cart_to_checkout", day, shifts, rng))
    purchases = round(checkouts * _value("checkout_to_purchase", day, shifts, rng))
    revenue = round(purchases * _value("aov", day, shifts, rng), 2)

    return (
        day.isoformat(),
        campaign,
        f"{spend:.2f}",
        impressions,
        clicks,
        page_views,
        carts,
        checkouts,
        purchases,
        f"{revenue:.2f}",
        f"{revenue / spend:.2f}",
        f"{spend / purchases:.2f}" if purchases else "",
        f"{spend / clicks:.2f}",
        f"{spend / impressions * 1000:.2f}",
        f"{clicks / impressions * 100:.2f}",
        "7-day click or 1-day view",
    )


def generate(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    days = [START + timedelta(days=i) for i in range((END - START).days + 1)]

    for scenario in SCENARIOS:
        rng = random.Random(SEED)
        with open(output_dir / scenario["file"], "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(HEADERS)
            for day in days:
                for campaign, daily_spend, ctr_factor in CAMPAIGNS:
                    writer.writerow(_row(day, campaign, daily_spend, ctr_factor, scenario["shifts"], rng))

    expected = [
        {key: value for key, value in scenario.items() if key != "shifts"} for scenario in SCENARIOS
    ]
    (output_dir / "expected_results.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    generate(Path(__file__).parent)
