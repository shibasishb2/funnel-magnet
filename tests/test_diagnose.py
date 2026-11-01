import pandas as pd
import pytest

from funnel_magnet.diagnose import (
    DEFAULT_MATERIALITY_THRESHOLD,
    Status,
    assess,
    diagnose_totals,
    run_diagnosis,
)
from funnel_magnet.metrics import Totals
from funnel_magnet.periods import InsufficientPeriodsError, MONTHLY, WEEKLY, list_complete_periods

# ROAS 3.0, AOV 60, CPA 20, CPC 0.50, CVR 2.5%, CPM 10, CTR 2%.
PRIOR = Totals(
    spend=1000.0,
    impressions=100_000.0,
    link_clicks=2000.0,
    purchases=50.0,
    purchase_value=3000.0,
    landing_page_views=1600.0,
    add_to_carts=400.0,
    checkouts_initiated=100.0,
)


def period(base=PRIOR, **overrides):
    values = {**base.__dict__, **overrides}
    return Totals(**values)


def scaled_funnel(link_clicks, lpv=0.8, cart=0.25, checkout=0.25, purchase=0.5, aov=60.0):
    page_views = link_clicks * lpv
    carts = page_views * cart
    checkouts = carts * checkout
    purchases = checkouts * purchase
    return dict(
        link_clicks=link_clicks,
        landing_page_views=page_views,
        add_to_carts=carts,
        checkouts_initiated=checkouts,
        purchases=purchases,
        purchase_value=purchases * aov,
    )


def test_default_threshold_is_five_percent():
    assert DEFAULT_MATERIALITY_THRESHOLD == 0.05


@pytest.mark.parametrize(
    "current, expected",
    [
        (104.9, Status.STABLE),
        (105.0, Status.IMPROVED),
        (95.1, Status.STABLE),
        (95.0, Status.DETERIORATED),
    ],
)
def test_threshold_boundary(current, expected):
    assert assess("roas", 100.0, current, 0.05, True).status is expected


def test_polarity_makes_a_higher_cost_a_deterioration():
    assert assess("cpa", 20.0, 25.0, 0.05, False).status is Status.DETERIORATED
    assert assess("cpa", 25.0, 20.0, 0.05, False).status is Status.IMPROVED


def test_rise_from_zero_has_no_percentage():
    change = assess("roas", 0.0, 2.0, 0.05, True)

    assert change.pct_change is None
    assert change.status is Status.IMPROVED


def test_threshold_changes_the_result():
    current = period(spend=1080.0)  # CPM, CPC, CPA +8%, ROAS -7.4%

    assert diagnose_totals(PRIOR, current).changes["cpa"].status is Status.DETERIORATED
    assert diagnose_totals(PRIOR, current, threshold=0.10).paths == [("roas",)]


@pytest.mark.parametrize("threshold", [-0.01, 1.0, 5])
def test_invalid_thresholds_are_rejected(threshold):
    with pytest.raises(ValueError):
        diagnose_totals(PRIOR, PRIOR, threshold=threshold)


def test_no_change_stops_at_step_two():
    result = diagnose_totals(PRIOR, PRIOR)

    assert result.paths == [("roas",)]
    assert result.adverse_drivers == []
    assert result.data_needed == []


def test_cpm_path():
    assert diagnose_totals(PRIOR, period(spend=1200.0)).paths == [("roas", "cpa", "cpc", "cpm")]


def test_both_cpa_branches_run_when_cpc_and_cvr_worsen():
    current = period(
        PRIOR,
        spend=1000.0,
        impressions=100_000.0,
        **scaled_funnel(1600.0, checkout=0.15),
    )
    result = diagnose_totals(PRIOR, current)

    assert ("roas", "cpa", "cpc", "ctr") in result.paths
    assert ("roas", "cpa", "cvr", "add_to_carts_to_checkouts_initiated") in result.paths


def test_both_cpc_inputs_are_followed_when_both_worsen():
    current = period(spend=1200.0, impressions=100_000.0, link_clicks=1800.0)
    current = period(current, purchases=45.0, purchase_value=2700.0,
                     landing_page_views=1440.0, add_to_carts=360.0, checkouts_initiated=90.0)
    # CPM rises 20%, and CTR falls 10%.
    result = diagnose_totals(PRIOR, current)

    assert ("roas", "cpa", "cpc", "cpm") in result.paths
    assert ("roas", "cpa", "cpc", "ctr") in result.paths


def test_aov_and_cpa_branches_both_run():
    current = period(spend=1200.0, purchase_value=2700.0)
    result = diagnose_totals(PRIOR, current)

    assert ("roas", "aov") in result.paths
    assert any(path[:2] == ("roas", "cpa") for path in result.paths)


def test_better_roas_does_not_hide_a_worse_cpa():
    current = period(spend=1200.0, purchase_value=4800.0)
    result = diagnose_totals(PRIOR, current)

    assert result.changes["roas"].status is Status.IMPROVED
    assert result.paths[0][:2] == ("roas", "cpa")


def test_cpa_with_no_single_material_input_stops_at_cpa():
    # CPC +3.6% and CVR -3.3% add up to a 7.1% CPA rise.
    current = period(spend=1036.0, purchases=48.35, purchase_value=2901.0)
    result = diagnose_totals(PRIOR, current)

    assert result.changes["cpa"].status is Status.DETERIORATED
    assert result.paths == [("roas", "cpa")]


def test_cvr_decline_without_funnel_data_lists_the_data_needed():
    prior = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0)
    current = Totals(1000.0, 100_000.0, 2000.0, 40.0, 2400.0)
    result = diagnose_totals(prior, current)

    assert result.paths == [("roas", "cpa", "cvr")]
    assert any("Funnel-stage" in item for item in result.data_needed)
    assert any("funnel stages are unavailable" in item for item in result.warnings)


def test_drivers_sum_to_the_roas_change_and_are_ranked():
    current = period(spend=1200.0, purchase_value=2850.0)
    result = diagnose_totals(PRIOR, current)
    roas = result.changes["roas"]
    contributions = [abs(d.contribution) for d in result.drivers]

    assert sum(d.contribution for d in result.drivers) == pytest.approx(roas.current - roas.prior)
    assert contributions == sorted(contributions, reverse=True)
    assert result.adverse_drivers[0].metric == "cpm"


def test_every_decomposition_level_sums_to_its_parent():
    current = period(PRIOR, spend=1150.0, impressions=95_000.0, **scaled_funnel(1800.0, lpv=0.7, aov=57.0))
    result = diagnose_totals(PRIOR, current)

    for decomposition in result.decompositions.values():
        assert decomposition.defined
        change = decomposition.current - decomposition.prior
        assert sum(c.contribution for c in decomposition.contributions) == pytest.approx(change)


def test_zero_spend_in_a_period_is_not_assessable():
    current = period(spend=0.0, impressions=0.0, link_clicks=0.0, purchases=0.0, purchase_value=0.0,
                     landing_page_views=0.0, add_to_carts=0.0, checkouts_initiated=0.0)
    result = diagnose_totals(PRIOR, current)

    assert not result.assessable
    assert result.paths == []
    assert result.drivers == []
    assert result.drivers_undefined_reason
    assert any("zero spend" in item for item in result.warnings)


def test_zero_prior_spend_is_not_assessable():
    assert not diagnose_totals(Totals(0.0, 0.0, 0.0, 0.0, 0.0), PRIOR).assessable


def test_losing_all_purchases_still_investigates_without_contributions():
    current = period(purchases=0.0, purchase_value=0.0, checkouts_initiated=100.0)
    result = diagnose_totals(PRIOR, current)

    assert result.changes["cpa"].status is Status.UNDEFINED
    assert result.changes["roas"].status is Status.DETERIORATED
    assert ("roas", "cpa", "cvr", "checkouts_initiated_to_purchases") in result.paths
    assert not result.decompositions["roas"].defined
    assert result.drivers == []
    assert any("zero purchases" in item for item in result.warnings)


def test_cpc_split_survives_when_purchases_are_lost():
    current = period(spend=1200.0, purchases=0.0, purchase_value=0.0)
    result = diagnose_totals(PRIOR, current)

    assert result.decompositions["cpc"].defined
    assert ("roas", "cpa", "cpc", "cpm") in result.paths


def test_zero_purchases_in_both_periods_gives_no_contributions():
    prior = Totals(1000.0, 100_000.0, 2000.0, 0.0, 0.0)
    result = diagnose_totals(prior, prior)

    assert result.changes["aov"].status is Status.UNDEFINED
    assert result.drivers == []


def test_recovery_from_zero_purchases_has_no_contributions():
    prior = Totals(1000.0, 100_000.0, 2000.0, 0.0, 0.0)
    current = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0)
    result = diagnose_totals(prior, current)

    assert result.changes["roas"].status is Status.IMPROVED
    assert result.changes["roas"].pct_change is None
    assert not result.decompositions["roas"].defined
    assert result.paths == [("roas",)]


def test_zero_link_clicks_is_handled():
    current = period(link_clicks=0.0, purchases=0.0, purchase_value=0.0, landing_page_views=0.0,
                     add_to_carts=0.0, checkouts_initiated=0.0)
    result = diagnose_totals(PRIOR, current)

    assert result.changes["cpc"].status is Status.UNDEFINED
    assert result.drivers == []
    assert any("zero link clicks" in item for item in result.warnings)


def test_zero_stage_keeps_the_funnel_split_undefined_but_uses_cvr_driver():
    current = period(add_to_carts=0.0)
    result = diagnose_totals(PRIOR, current)

    assert not result.decompositions["cvr"].defined
    assert result.drivers_undefined_reason is None
    assert "cvr" in [driver.metric for driver in result.drivers]


def test_rate_above_one_is_flagged():
    current = period(landing_page_views=2500.0)

    assert any("above 100%" in item for item in diagnose_totals(PRIOR, current).warnings)


def test_low_volume_is_flagged_and_configurable():
    small = Totals(100.0, 10_000.0, 200.0, 10.0, 600.0)

    assert any("directional" in item for item in diagnose_totals(small, small).warnings)
    assert not any("directional" in item for item in diagnose_totals(small, small, min_purchases=5).warnings)


def daily_data(start, end, **overrides):
    values = dict(
        campaign="A",
        spend=100.0,
        impressions=10_000.0,
        link_clicks=200.0,
        purchases=5.0,
        purchase_value=300.0,
    )
    return pd.DataFrame({"date": pd.date_range(start, end), **{**values, **overrides}})


def test_run_diagnosis_uses_latest_complete_periods():
    data = daily_data("2026-06-01", "2026-08-31")

    assert run_diagnosis(data, MONTHLY).current_label == "2026-08"
    assert run_diagnosis(data, WEEKLY).current_label == "2026-W35"


def test_run_diagnosis_accepts_chosen_periods():
    data = daily_data("2026-06-01", "2026-08-31")
    june, july, august = list_complete_periods(data, MONTHLY)

    result = run_diagnosis(data, MONTHLY, prior_period=june, current_period=august)

    assert (result.prior_label, result.current_label) == ("2026-06", "2026-08")


def test_periods_in_the_wrong_order_are_rejected():
    data = daily_data("2026-06-01", "2026-08-31")
    june, july, august = list_complete_periods(data, MONTHLY)

    with pytest.raises(ValueError, match="must end before"):
        run_diagnosis(data, MONTHLY, prior_period=august, current_period=june)


def test_too_few_periods_raises_a_clear_error():
    with pytest.raises(InsufficientPeriodsError):
        run_diagnosis(daily_data("2026-06-01", "2026-06-20"), MONTHLY)


def test_unknown_campaign_is_rejected():
    with pytest.raises(ValueError, match="Campaign not found"):
        run_diagnosis(daily_data("2026-06-01", "2026-08-31"), MONTHLY, campaign="Z")


def test_campaign_filter_changes_the_scope():
    worse = daily_data("2026-06-01", "2026-06-14", campaign="B")
    worse.loc[worse["date"] >= "2026-06-08", "spend"] = 150.0
    data = pd.concat([daily_data("2026-06-01", "2026-06-14"), worse], ignore_index=True)

    result = run_diagnosis(data, WEEKLY, campaign="B")

    assert result.changes["cpm"].pct_change == pytest.approx(0.5)


def test_missing_days_are_flagged():
    data = daily_data("2026-06-01", "2026-06-14")
    data = data[data["date"] != "2026-06-10"]

    assert any("6 of 7 days" in item for item in run_diagnosis(data, WEEKLY).warnings)


def test_changed_attribution_setting_is_flagged():
    data = daily_data("2026-06-01", "2026-06-14", attribution_setting="7-day click")
    data.loc[data["date"] >= "2026-06-08", "attribution_setting"] = "1-day click"

    assert any("Attribution settings differ" in item for item in run_diagnosis(data, WEEKLY).warnings)


def test_missing_attribution_setting_is_flagged():
    data = daily_data("2026-06-01", "2026-06-14")

    assert any("no attribution setting" in item for item in run_diagnosis(data, WEEKLY).warnings)


def test_recent_period_and_campaign_mix_warnings():
    data = pd.concat(
        [daily_data("2026-06-01", "2026-06-14"), daily_data("2026-06-01", "2026-06-14", campaign="B")]
    )
    warnings = run_diagnosis(data, WEEKLY).warnings

    assert any("late-attributed" in item for item in warnings)
    assert any("several campaigns" in item for item in warnings)
