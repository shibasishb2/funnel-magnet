"""The marketing playbook and the four-part diagnosis it produces, with no AI."""

from pathlib import Path

import pytest

from funnel_magnet import playbook
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
from funnel_magnet.metrics import Totals
from funnel_magnet.periods import MONTHLY, WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import view
from test_two_way_tree import OCTOBER, SEPTEMBER

SAMPLES = view.load_samples()
PRIOR = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0, 1600.0, 400.0, 100.0)
LPV = "link_clicks_to_landing_page_views"
CART = "landing_page_views_to_add_to_carts"
CHECKOUT = "checkouts_initiated_to_purchases"


def build(diagnosis, **flags):
    return playbook.build(diagnosis, "EUR", **{"several_campaigns": True, "earlier_periods": True, **flags})


def sample(index, granularity=WEEKLY):
    return run_diagnosis(load_meta_csv(SAMPLES[index].path).data, granularity)


def ids(items):
    return [item.id for item in items]


# The playbook file


def test_playbook_loads_and_every_cause_is_hedged():
    book = playbook.load_playbook()

    assert len(book.causes) >= 20
    assert all(" may " in f" {cause['statement']} " for cause in book.causes)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ('statement = "People may have seen', 'statement = "People have seen', "hedged"),
        ('investigations = ["frequency_reach", "creative_age", "ad_level_ctr"]', 'investigations = ["made_up"]', "unknown investigation"),
        ('explains = ["aov"]\ninvestigations = ["order_data"]\nif_supported = "Promote', 'explains = ["revenue"]\ninvestigations = ["order_data"]\nif_supported = "Promote', "unknown metric"),
        ('source = "campaigns"', 'source = "somewhere"', "unknown source"),
        ('if_unclear = "Comparing campaigns', 'unclear = "Comparing campaigns', "needs look_at, readings and if_unclear"),
        ('a campaign mix shift may explain part', 'a campaign mix shift confirms part', "presents a result as proof"),
        ('in both periods, next to {kpis}', 'in both periods, next to {kpi}', "unknown placeholder"),
    ],
)
def test_a_broken_playbook_fails_loudly(tmp_path, old, new, message):
    text = playbook.PLAYBOOK_PATH.read_text("utf-8")
    assert old in text
    broken = tmp_path / "playbook.toml"
    broken.write_text(text.replace(old, new, 1), "utf-8")

    with pytest.raises(ValueError, match=message):
        playbook.load_playbook(broken)


# Problem KPIs, improvements and offsets


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize(
    "index, problems, beneficial",
    [
        (0, ["cpm"], []),
        (1, ["ctr"], []),
        (2, [LPV], []),
        (3, [CHECKOUT], []),
        (4, ["aov"], []),
        (5, ["cpm"], ["aov"]),
        (6, [], []),
    ],
    ids=[item.name for item in SAMPLES],
)
def test_problem_and_improved_kpis_per_scenario(index, problems, beneficial, granularity):
    result = build(sample(index, granularity))

    assert [move.metric for move in result.problems] == problems
    assert [move.metric for move in result.beneficial] == beneficial


def test_masked_problem_is_found_and_the_improvement_gets_no_causes():
    result = build(sample(5))

    assert result.beneficial[0].effect == "Positive contributor"
    assert not any("AOV" in cause.explains for cause in result.causes)
    assert all(cause.explains == ("CPM",) for cause in result.causes)


def test_september_october_pattern():
    result = build(diagnose_totals(SEPTEMBER, OCTOBER))

    assert [move.metric for move in result.problems] == ["cpm", CHECKOUT]
    assert [move.metric for move in result.beneficial] == ["ctr", CART]
    assert all(move.effect == "Offsetting driver" for move in result.problems)
    # Checkout is a late stage, so landing page experience isn't offered as the shared factor.
    assert [cause.id for cause in result.shared_causes] == ["delivery_shift", "campaign_mix"]
    assert all(cause.explains == ("CPM", "Checkout → Purchase") for cause in result.shared_causes)
    assert "landing_experience_quality" not in ids(result.causes)
    assert any("may be separate problems" in note for note in result.patterns)
    # CTR improved, so no CTR-only cause is suggested.
    assert "creative_fatigue" not in ids(result.causes)


def test_healthy_control_suggests_nothing():
    result = build(sample(6))

    assert (result.causes, result.investigations, result.actions, result.patterns) == ((), (), (), ())


# Playbook rules


def test_cpm_alone_puts_cpm_specific_causes_first():
    result = build(sample(0))

    assert ids(result.causes)[0] == "auction_competition"
    assert {"seasonality", "audience_saturation", "creative_relevance", "account_structure"} <= set(ids(result.causes))


def test_cpm_and_ctr_together_are_investigated_jointly():
    current = Totals(1200.0, 100_000.0, 1800.0, 45.0, 2700.0, 1440.0, 360.0, 90.0)
    result = build(diagnose_totals(PRIOR, current))

    assert {move.metric for move in result.problems} == {"cpm", "ctr"}
    assert any("together" in note for note in result.patterns)
    # Causes that could explain both problems come first.
    assert set(ids(result.causes)[:2]) == {"audience_saturation", "creative_relevance"}
    assert result.causes[0].explains == ("CTR", "CPM") or result.causes[0].explains == ("CPM", "CTR")


def test_cpc_gets_its_components_checked_not_its_own_causes():
    current = Totals(1040.0, 100_000.0, 1940.0, 48.5, 2910.0, 1552.0, 388.0, 97.0)
    diagnosis = diagnose_totals(PRIOR, current)
    result = build(diagnosis)

    assert [move.metric for move in result.problems] == ["cpc"]
    assert result.causes == ()
    assert any("CPC worsened while CPM and CTR" in note for note in result.component_notes)
    assert "earlier_periods" in ids(result.investigations)


def test_cpa_gets_its_components_checked_not_its_own_causes():
    current = Totals(1036.0, 100_000.0, 2000.0, 48.35, 2901.0, 1600.0, 400.0, 100.0)
    result = build(diagnose_totals(PRIOR, current))

    assert [move.metric for move in result.problems] == ["cpa"]
    assert result.causes == ()
    assert any("CPA worsened while CPC and CVR" in note for note in result.component_notes)


def test_cvr_without_funnel_stages_asks_for_them():
    prior = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0)
    current = Totals(1000.0, 100_000.0, 2000.0, 40.0, 2400.0)
    result = build(diagnose_totals(prior, current))

    assert [move.metric for move in result.problems] == ["cvr"]
    assert "funnel_export" in ids(result.investigations)


@pytest.mark.parametrize(
    "index, expected, unexpected",
    [
        (2, {"landing_page_performance", "tracking_issue", "ad_page_mismatch"}, {"payment_errors", "price_offer"}),
        (3, {"payment_errors", "payment_options", "checkout_usability", "shipping_costs", "tracking_issue"}, {"landing_page_performance"}),
        (4, {"product_mix", "basket_size", "discounts", "pricing_change", "promotion_change"}, {"auction_competition"}),
    ],
)
def test_each_problem_gets_its_own_stage_or_kpi_causes(index, expected, unexpected):
    causes = set(ids(build(sample(index)).causes))

    assert expected <= causes and not unexpected & causes


def test_cpm_with_a_funnel_stage_adds_the_shared_cause_only_then():
    assert "landing_experience_quality" not in ids(build(sample(0)).causes)
    assert "landing_experience_quality" not in ids(build(sample(3)).causes)


# What to investigate and what to do


def test_cpm_alone_top_three_starts_with_the_upload():
    result = build(sample(0))

    assert ids(result.top_investigations) == ["campaign_split", "earlier_periods", "account_change_log"]


def test_unavailable_upload_checks_become_outside_steps():
    result = build(sample(0), several_campaigns=False, earlier_periods=False)
    checks = {item.id: item for item in result.investigations}

    assert not checks["campaign_split"].available
    assert "Export the report broken down by campaign" in checks["campaign_split"].detail
    assert "Export a longer date range" in checks["earlier_periods"].detail


def test_every_check_says_what_it_tests():
    for index in range(6):
        for item in build(sample(index)).investigations:
            assert item.tests and item.detail


def test_every_action_belongs_to_a_cause_and_has_both_branches():
    result = build(sample(3))

    assert [action.cause for action in result.actions] == [cause.title for cause in result.causes]
    assert all(action.if_supported and action.if_not_supported for action in result.actions)


def test_rendered_actions_are_always_conditional():
    from funnel_magnet.ui import render

    html = render.actions_html(build(sample(3)))

    assert html.count("If the evidence supports this cause:") == len(build(sample(3)).actions)
    assert "Only after the checks above" in html


def test_not_assessable_comparison_gives_empty_sections():
    empty = Totals(0.0, 0.0, 0.0, 0.0, 0.0)
    result = build(diagnose_totals(PRIOR, empty))

    assert result.problems == () and result.causes == ()


def test_losing_all_purchases_still_builds():
    current = Totals(1000.0, 100_000.0, 2000.0, 0.0, 0.0, 1600.0, 400.0, 100.0)
    result = build(diagnose_totals(PRIOR, current))

    assert CHECKOUT in [move.metric for move in result.problems]


def test_account_level_export_offers_no_campaign_split_in_the_app():
    fixture = Path(__file__).parent / "fixtures" / "meta_account_level_inr.csv"
    loaded = load_meta_csv(fixture)
    result = playbook.build(run_diagnosis(loaded.data, MONTHLY), loaded.currency, several_campaigns=False, earlier_periods=True)

    checks = {item.id: item for item in result.investigations}
    if "campaign_split" in checks:
        assert not checks["campaign_split"].available
    assert all("€" not in move.change for move in result.problems + result.beneficial)


# Approved corrections


CPM_EARLY = Totals(1200.0, 100_000.0, 2000.0, 40.0, 2400.0, 1280.0, 320.0, 80.0)


def test_cpm_with_an_early_stage_offers_landing_experience_as_shared():
    result = build(diagnose_totals(PRIOR, CPM_EARLY))

    assert {move.metric for move in result.problems} == {"cpm", LPV}
    shared = ids(result.shared_causes)
    assert {"landing_experience_quality", "delivery_shift", "campaign_mix"} <= set(shared)
    assert any("early stage" in note for note in result.patterns)
    assert any("may be separate problems" in note for note in result.patterns)


def test_shared_and_separate_causes_never_overlap():
    result = build(diagnose_totals(SEPTEMBER, OCTOBER))

    assert set(ids(result.shared_causes)).isdisjoint(ids(result.separate_causes))
    assert len(result.shared_causes) + len(result.separate_causes) == len(result.causes)


def test_campaign_mix_needs_several_campaigns():
    assert "campaign_mix" in ids(build(sample(0)).causes)
    assert "campaign_mix" not in ids(build(sample(0), several_campaigns=False).causes)


def test_campaign_mix_is_not_offered_for_cpc_or_cpa():
    current = Totals(1040.0, 100_000.0, 1940.0, 48.5, 2910.0, 1552.0, 388.0, 97.0)

    assert build(diagnose_totals(PRIOR, current)).causes == ()


def test_ctr_includes_placement_changes_and_never_tightens_targeting_by_default():
    result = build(sample(1))
    book = {cause["id"]: cause for cause in playbook.load_playbook().causes}

    assert "placement_audience_changes" in ids(result.causes)
    mismatch = book["audience_mismatch"]
    assert "Test" in mismatch["if_supported"] and "segments the breakdown shows" in mismatch["if_supported"]
    assert "Narrow targeting" not in " ".join(c["if_supported"] for c in book.values())


def test_cpm_alone_offers_budget_scaling_but_no_ctr_check():
    result = build(sample(0))

    assert "budget_bid_changes" in ids(result.causes)
    assert "ad_level_ctr" not in ids(result.investigations)


def test_earlier_periods_no_longer_claims_to_test_competition():
    checks = {item.id: item for item in build(sample(0)).investigations}

    assert "Auction competition" not in checks["earlier_periods"].tests
    assert "Seasonality" in checks["earlier_periods"].tests


@pytest.mark.parametrize("index", [2, 3])
def test_funnel_problems_include_traffic_quality(index):
    assert "traffic_quality" in ids(build(sample(index)).causes)


def test_website_change_log_tests_every_checkout_cause_it_can_reveal():
    checks = {item.id: item for item in build(sample(3)).investigations}

    assert {"Payment options", "Checkout form usability", "Shipping costs"} <= set(checks["site_change_log"].tests)


def test_frequency_check_says_the_app_does_not_analyse_those_columns():
    checks = {item.id: item for item in build(sample(1)).investigations}

    assert not checks["frequency_reach"].available
    assert "doesn't analyse them yet" in checks["frequency_reach"].detail


# Top three investigations


@pytest.mark.parametrize(
    "case",
    [lambda: sample(2), lambda: sample(3), lambda: diagnose_totals(SEPTEMBER, OCTOBER), lambda: diagnose_totals(PRIOR, CPM_EARLY)],
    ids=["landing", "checkout", "cpm_checkout", "cpm_landing"],
)
def test_funnel_problems_put_a_measurement_check_in_the_top_three(case):
    top = build(case()).top_investigations

    assert "tracking_diagnostics" in ids(top)
    assert "counting fault" in next(item.reason for item in top if item.id == "tracking_diagnostics")


def test_non_funnel_problems_do_not_force_a_tracking_check():
    assert "tracking_diagnostics" not in ids(build(sample(0)).top_investigations)


def test_ctr_top_three_favours_checks_that_separate_the_causes():
    top = ids(build(sample(1)).top_investigations)

    assert {"ad_level_ctr", "campaign_split", "frequency_reach"} == set(top)


def test_cpm_and_ctr_top_three_tests_shared_explanations():
    current = Totals(1200.0, 100_000.0, 1800.0, 45.0, 2700.0, 1440.0, 360.0, 90.0)
    top = build(diagnose_totals(PRIOR, current)).top_investigations

    assert all("shared by more than one problem" in item.reason for item in top)


def test_every_top_check_explains_its_priority():
    for index in range(6):
        result = build(sample(index))
        assert len(result.top_investigations) == min(3, len(result.investigations))
        assert all(item.reason for item in result.top_investigations)


def test_reasons_say_which_problems_a_check_addresses():
    checks = build(diagnose_totals(SEPTEMBER, OCTOBER)).investigations
    top = checks[:3]

    assert top[0].reason.startswith("Addresses both problems found: CPM and Checkout → Purchase.")
    assert "ROAS effect" not in " ".join(c.reason for c in checks)
    one = next(c for c in checks if c.id == "payment_logs")
    assert "Addresses Checkout → Purchase, 1 of the 2 problems found." in one.reason
    cpm = next(c for c in checks if c.id == "auction_benchmarks")
    assert "Addresses CPM, 1 of the 2 problems found, including the one with the largest effect on ROAS." in cpm.reason
    assert "Uses data already in your upload" in top[0].reason


def test_top_three_and_full_lists_show_on_the_page_and_in_the_report():
    from datetime import date

    from funnel_magnet.ai.evidence import build_evidence
    from funnel_magnet.ui import render, report

    diagnosis = diagnose_totals(SEPTEMBER, OCTOBER)
    diagnosis.prior_label, diagnosis.current_label = "2026-09", "2026-10"
    marketing = build(diagnosis)
    page = render.investigations_html(marketing) + render.possible_causes_html(marketing) + render.actions_html(marketing)
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, "EUR"), "File", "All campaigns", "a", "b", "c", date(2026, 11, 3), "EUR", marketing=marketing,
    ))

    # The report repeats the top three in its executive diagnosis.
    assert page.count("Priority ") == 3 and html.count("Priority ") == 6
    assert "<details" in page and "<details" not in html
    for item in marketing.investigations:
        assert item.title in page and item.title in html
    for top in marketing.top_investigations:
        from html import escape
        assert escape(top.reason) in page and escape(top.reason) in html
    for cause in marketing.causes:
        assert cause.title in page and cause.title in html


@pytest.mark.parametrize("stage", ["link_clicks_to_landing_page_views", "landing_page_views_to_add_to_carts",
                                   "add_to_carts_to_checkouts_initiated", "checkouts_initiated_to_purchases"])
def test_tracking_can_explain_every_funnel_stage(stage):
    tracking = next(c for c in playbook.load_playbook().causes if c["id"] == "tracking_issue")

    assert stage in tracking["explains"]
