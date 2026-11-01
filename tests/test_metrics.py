import pytest

from funnel_magnet.metrics import (
    Totals,
    available_funnel_stages,
    compute_metrics,
    funnel_rates,
    safe_ratio,
)


def totals(**overrides):
    values = dict(
        spend=1000.0,
        impressions=100_000.0,
        link_clicks=2000.0,
        purchases=50.0,
        purchase_value=3000.0,
    )
    return Totals(**{**values, **overrides})


def test_metrics_match_hand_calculations():
    metrics = compute_metrics(totals())

    assert metrics["roas"] == pytest.approx(3.0)
    assert metrics["aov"] == pytest.approx(60.0)
    assert metrics["cpa"] == pytest.approx(20.0)
    assert metrics["cpc"] == pytest.approx(0.5)
    assert metrics["cvr"] == pytest.approx(0.025)
    assert metrics["cpm"] == pytest.approx(10.0)
    assert metrics["ctr"] == pytest.approx(0.02)


def test_metric_identities_hold():
    metrics = compute_metrics(totals())

    assert metrics["roas"] == pytest.approx(metrics["aov"] / metrics["cpa"])
    assert metrics["cpa"] == pytest.approx(metrics["cpc"] / metrics["cvr"])
    assert metrics["cpc"] == pytest.approx(metrics["cpm"] / (metrics["ctr"] * 1000))


@pytest.mark.parametrize(
    "overrides, undefined, defined",
    [
        ({"spend": 0.0}, ["roas"], ["aov", "cvr", "ctr"]),
        ({"purchases": 0.0, "purchase_value": 0.0}, ["aov", "cpa"], ["roas", "cvr", "cpc"]),
        ({"link_clicks": 0.0}, ["cpc", "cvr"], ["roas", "cpa", "cpm"]),
        ({"impressions": 0.0}, ["cpm", "ctr"], ["roas", "cpc"]),
    ],
)
def test_zero_denominators_are_undefined_and_not_zero(overrides, undefined, defined):
    metrics = compute_metrics(totals(**overrides))

    assert all(metrics[name] is None for name in undefined)
    assert all(metrics[name] is not None for name in defined)


def test_zero_numerator_is_a_real_zero():
    assert compute_metrics(totals(purchases=0.0, purchase_value=0.0))["cvr"] == 0.0
    assert safe_ratio(None, 5) is None


def test_funnel_rates_multiply_to_cvr():
    data = totals(landing_page_views=1700.0, add_to_carts=255.0, checkouts_initiated=100.0)
    rates = funnel_rates(data, available_funnel_stages(data))

    product = 1.0
    for rate in rates.values():
        product *= rate
    assert len(rates) == 4
    assert product == pytest.approx(compute_metrics(data)["cvr"])


def test_missing_stages_combine_neighbouring_stages():
    data = totals(add_to_carts=255.0)
    rates = funnel_rates(data, available_funnel_stages(data))

    assert list(rates) == ["link_clicks_to_add_to_carts", "add_to_carts_to_purchases"]


def test_a_stage_must_exist_in_both_periods():
    with_stage = totals(landing_page_views=1700.0)

    assert available_funnel_stages(with_stage, totals()) == ("link_clicks", "purchases")
