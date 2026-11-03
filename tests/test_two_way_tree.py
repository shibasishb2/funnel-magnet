"""The decision tree follows material changes in both directions.

The totals are synthetic but copy a real September to October pattern: ROAS
improved while CPM rose sharply, CTR rose by more, and two funnel stages moved
in opposite directions.
"""

import pytest

from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.diagnose import Status, diagnose_totals
from funnel_magnet.metrics import Totals
from funnel_magnet.ui import view

SEPTEMBER = Totals(100_000.0, 324_180.0, 4439.0, 60.0, 315_960.0, 3733.0, 573.0, 113.0)
OCTOBER = Totals(100_000.0, 224_488.0, 4578.0, 66.0, 362_406.0, 3975.0, 708.0, 140.0)

CPM_PATH = ("roas", "cpa", "cpc", "cpm")
CTR_PATH = ("roas", "cpa", "cpc", "ctr")
CART_PATH = ("roas", "cpa", "cvr", "landing_page_views_to_add_to_carts")
CHECKOUT_PATH = ("roas", "cpa", "cvr", "checkouts_initiated_to_purchases")


@pytest.fixture
def improved():
    return diagnose_totals(SEPTEMBER, OCTOBER)


@pytest.fixture
def declined():
    return diagnose_totals(OCTOBER, SEPTEMBER)


def statuses(diagnosis):
    return {metric: change.status for metric, change in diagnosis.changes.items()}


def test_fixture_reproduces_the_pattern(improved):
    status = statuses(improved)

    assert status["roas"] is Status.IMPROVED
    assert status["cpm"] is Status.DETERIORATED and status["ctr"] is Status.IMPROVED
    assert status["cpc"] is Status.STABLE
    assert status["landing_page_views_to_add_to_carts"] is Status.IMPROVED
    assert status["checkouts_initiated_to_purchases"] is Status.DETERIORATED
    assert status["aov"] is Status.STABLE


def test_an_improvement_is_explained(improved):
    assert set(improved.paths) == {CPM_PATH, CTR_PATH, CART_PATH, CHECKOUT_PATH}


def test_a_stable_cpc_no_longer_hides_cpm_and_ctr(improved):
    assert improved.changes["cpc"].status is Status.STABLE
    assert CPM_PATH in improved.paths and CTR_PATH in improved.paths


def test_the_reverse_decline_follows_the_same_branches(declined):
    assert declined.changes["roas"].status is Status.DETERIORATED
    assert set(declined.paths) == {CPM_PATH, CTR_PATH, CART_PATH, CHECKOUT_PATH}


def test_contributions_are_unchanged_and_still_sum(improved):
    roas = improved.changes["roas"]

    assert sum(d.contribution for d in improved.drivers) == pytest.approx(roas.current - roas.prior)
    assert {d.metric: d.role for d in improved.drivers if d.role != "negligible"} == {
        "ctr": "favorable",
        "cpm": "adverse",
        "landing_page_views_to_add_to_carts": "favorable",
        "checkouts_initiated_to_purchases": "adverse",
    }


def test_drivers_are_labelled_by_their_effect_on_an_improvement(improved):
    labels = {d.metric: view.contribution_label(improved, d) for d in improved.drivers}

    assert labels["ctr"] == labels["landing_page_views_to_add_to_carts"] == "Positive contributor"
    assert labels["cpm"] == labels["checkouts_initiated_to_purchases"] == "Offsetting driver"
    assert labels["aov"] == "Negligible"


def test_drivers_are_labelled_by_their_effect_on_a_decline(declined):
    labels = {d.metric: view.contribution_label(declined, d) for d in declined.drivers}

    assert labels["ctr"] == labels["landing_page_views_to_add_to_carts"] == "Adverse contributor"
    assert labels["cpm"] == labels["checkouts_initiated_to_purchases"] == "Offsetting driver"


def test_main_driver_is_the_largest_contribution_with_roas(improved, declined):
    up, down = view.driver_badges(improved), view.driver_badges(declined)

    assert up["ctr"] == "Main driver" and up["cpm"] == "Offset"
    assert down["ctr"] == "Main driver" and down["cpm"] == "Offset"
    assert sum(badge == "Main driver" for badge in up.values()) == 1


def test_tree_outlines_every_followed_branch(improved):
    on_path = set()
    stack = [view.build_tree(improved)]
    while stack:
        node = stack.pop()
        on_path |= {node.metric} if node.on_path else set()
        stack += node.children

    assert on_path == {"roas", "cpa", "cpc", "cpm", "ctr", "cvr", *(p[-1] for p in (CART_PATH, CHECKOUT_PATH))}


def test_mathematical_diagnosis_explains_the_improvement_and_offsets(improved):
    text = " ".join(view.mathematical_diagnosis(improved, "INR"))

    assert text.startswith("ROAS rose")
    assert "CPC changed by less than 5%, with offsetting changes in its inputs: CPM (+44.4%), CTR (+48.9%)." in text
    assert "The CVR result is mathematically explained by these funnel stages:" in text
    assert "The largest positive contributor is CTR" in text
    assert "The largest offsetting driver is CPM" in text
    assert "No branch needed investigation" not in text


def test_findings_list_drivers_with_their_labels(improved):
    drivers = view.build_findings(improved, "INR").drivers

    assert drivers[0].startswith("Positive contributor: CTR contributed")
    assert any(line.startswith("Offsetting driver: CPM contributed") for line in drivers)


def test_evidence_needed_covers_improving_and_offsetting_metrics(improved):
    needed = " ".join(improved.data_needed)

    for metric in ("CPM", "CTR"):
        assert f"to explain the {metric} change" in needed
    assert "Landing page, pricing and stock change records" in needed
    assert "Checkout, payment and shipping records" in needed


def test_ai_evidence_matches_the_dashboard(improved):
    evidence = build_evidence(improved, "INR")
    paths = [fact for fact in evidence.facts if fact.kind == "diagnostic_path"]
    drivers = {fact.metric: fact for fact in evidence.facts if fact.kind == "mathematical_driver"}

    assert evidence.needs_hypotheses
    assert {tuple(fact.data["nodes"]) for fact in paths} == set(improved.paths)
    assert evidence.path_nodes == {node for path in improved.paths for node in path}
    assert drivers["ctr"].data["effect_on_roas"] == "positive contributor"
    assert "offsetting driver" in drivers["cpm"].statement
    assert evidence.by_id["X1"].statement == " ".join(view.mathematical_diagnosis(improved, "INR"))


def test_threshold_remains_configurable():
    assert diagnose_totals(SEPTEMBER, OCTOBER, threshold=0.50).paths == [("roas",)]


def test_material_parent_without_material_inputs_still_stops_there():
    # CPC +3.6% and CVR -3.3% add up to a 7.1% CPA rise, as before.
    prior = Totals(1000.0, 100_000.0, 2000.0, 50.0, 3000.0, 1600.0, 400.0, 100.0)
    current = Totals(1036.0, 100_000.0, 2000.0, 48.35, 2901.0, 1600.0, 400.0, 100.0)

    assert diagnose_totals(prior, current).paths == [("roas", "cpa")]


def test_unchanged_periods_still_stop_at_roas():
    result = diagnose_totals(SEPTEMBER, SEPTEMBER)

    assert result.paths == [("roas",)]
    assert view.driver_badges(result) == {}
