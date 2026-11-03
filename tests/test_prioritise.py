"""AI prioritisation of the playbook diagnosis. Every reply is mocked, so nothing is sent."""

import json

import pytest

from funnel_magnet import playbook
from funnel_magnet.ai import prioritise, service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import AIRequestError, AIResponseError, ProviderResponse
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
from funnel_magnet.periods import WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import render, report, view
from test_two_way_tree import OCTOBER, SEPTEMBER

SAMPLES = view.load_samples()


class FakeProvider:
    name, model = "Fake", "fake-model"

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate(self, system_prompt, user_prompt, schema):
        self.calls.append((system_prompt, user_prompt, schema))
        if self.error:
            raise self.error
        return ProviderResponse(self.text, 3000, 600)


def case(index=0):
    loaded = load_meta_csv(SAMPLES[index].path)
    diagnosis = run_diagnosis(loaded.data, WEEKLY)
    return playbook.build(diagnosis, loaded.currency, several_campaigns=True, earlier_periods=True), build_evidence(diagnosis, loaded.currency)


def sep_oct():
    diagnosis = diagnose_totals(SEPTEMBER, OCTOBER)
    return playbook.build(diagnosis, "EUR", several_campaigns=True, earlier_periods=True), build_evidence(diagnosis, "EUR")


def reply(**overrides):
    body = {
        "cause_priorities": [
            {"cause_id": "budget_bid_changes", "rationale": "A budget or bid change could explain a sudden rise."},
            {"cause_id": "auction_competition", "rationale": "Competition may also have raised costs."},
        ],
        "check_priorities": [
            {"check_id": "account_change_log", "rationale": "It could quickly rule self-made changes in or out."},
            {"check_id": "campaign_split", "rationale": "It shows whether the rise is account-wide."},
        ],
        "relationship": "",
        "action_guidance": [
            {"cause_id": "budget_bid_changes", "guidance": "If the change history shows a budget jump, scale more gradually."}
        ],
        "extra_checks": [],
    }
    body.update(overrides)
    return json.dumps(body)


def run(text, marketing=None, evidence=None):
    marketing, evidence = (marketing, evidence) if marketing else case()
    return prioritise.prioritise(marketing, evidence, FakeProvider(text))


# Request


def test_request_sends_only_playbook_items_and_restricts_ids():
    marketing, evidence = case()
    provider = FakeProvider(reply())
    prioritise.prioritise(marketing, evidence, provider)
    system, user, schema = provider.calls[0]
    payload = json.loads(user.split("\n\n", 1)[1])

    assert [c["cause_id"] for c in payload["causes"]] == [c.id for c in marketing.causes]
    assert schema["properties"]["cause_priorities"]["items"]["properties"]["cause_id"]["enum"] == [c.id for c in marketing.causes]
    assert schema["properties"]["check_priorities"]["items"]["properties"]["check_id"]["enum"] == [i.id for i in marketing.investigations]
    assert "Campaign" not in json.dumps(payload["problems"])  # no campaign names, only KPIs
    assert "never say they do" in system


def test_nothing_to_prioritise_sends_nothing():
    marketing, evidence = case(6)
    provider = FakeProvider(reply())

    with pytest.raises(AIRequestError, match="no possible causes"):
        prioritise.prioritise(marketing, evidence, provider)
    assert provider.calls == []


# Merging


def test_ai_order_and_notes_are_applied_without_changing_the_findings():
    marketing, evidence = case()
    result = run(reply(), marketing, evidence)

    assert [c.id for c in result.causes][:2] == ["budget_bid_changes", "auction_competition"]
    assert result.causes[0].ai_note.startswith("A budget or bid change could")
    assert [i.id for i in result.top_investigations][:2] == ["account_change_log", "campaign_split"]
    assert (result.problems, result.beneficial, result.summary) == (marketing.problems, marketing.beneficial, marketing.summary)
    assert {c.id for c in result.causes} == {c.id for c in marketing.causes}
    assert result.ai.provider == "Fake" and result.ai.input_tokens == 3000


def test_unknown_ids_are_ignored_and_reported():
    result = run(reply(cause_priorities=[{"cause_id": "made_up", "rationale": "It may matter."},
                                         {"cause_id": "seasonality", "rationale": "Seasonal demand may matter."}]))

    assert result.causes[0].id == "seasonality"
    assert any("made_up" in issue for issue in result.ai.withheld)


def test_reply_with_nothing_valid_is_rejected():
    with pytest.raises(AIResponseError, match="didn't prioritise any"):
        run(reply(cause_priorities=[{"cause_id": "x", "rationale": "y"}], check_priorities=[]))


@pytest.mark.parametrize(
    "rationale",
    [
        "Auction competition caused the rise in CPM.",
        "Competition drove the increase.",
        "This is definitely the main driver.",
        "CPM rose 37% last week.",
        "Frequency increased sharply.",
    ],
)
def test_unsupported_claims_in_explanations_are_withheld(rationale):
    result = run(reply(cause_priorities=[{"cause_id": "auction_competition", "rationale": rationale}]))
    cause = next(c for c in result.causes if c.id == "auction_competition")

    assert cause.ai_note == ""
    assert result.ai.withheld


def test_numbers_from_the_evidence_are_allowed():
    result = run(reply(cause_priorities=[{"cause_id": "auction_competition", "rationale": "CPM rose 18.3%, so competition may matter."}]))

    assert result.causes[0].ai_note.startswith("CPM rose 18.3%")


@pytest.mark.parametrize(
    "relationship, shown",
    [
        ("Both problems were caused by the same audience shift.", False),
        ("CPM and checkout share a single cause.", False),
        ("A single audience shift could possibly explain both problems, which the audience breakdown would test.", True),
    ],
)
def test_shared_causes_must_stay_possibilities(relationship, shown):
    marketing, evidence = sep_oct()
    text = reply(cause_priorities=[{"cause_id": "delivery_shift", "rationale": "A targeting shift could explain both."}],
                 check_priorities=[], action_guidance=[], relationship=relationship)
    result = prioritise.prioritise(marketing, evidence, FakeProvider(text))

    assert (result.ai.relationship == relationship) is shown


def test_relationship_is_ignored_with_one_problem():
    result = run(reply(relationship="These could be related."))

    assert result.ai.relationship == ""


@pytest.mark.parametrize(
    "guidance, shown",
    [
        ("If the change history shows a budget jump, scale more gradually.", True),
        ("Scale budgets more gradually.", False),
        ("Reduce bids now, because competition raised CPM.", False),
    ],
)
def test_action_guidance_must_be_conditional(guidance, shown):
    result = run(reply(action_guidance=[{"cause_id": "budget_bid_changes", "guidance": guidance}]))
    action = next(a for a in result.actions if a.cause == "Budget scaling or bid strategy")

    assert (action.ai_note == guidance) is shown


def test_measurement_check_stays_in_the_top_three_for_funnel_problems():
    marketing, evidence = case(3)  # Checkout friction
    others = [i.id for i in marketing.investigations if i.id != "tracking_diagnostics"][:3]
    text = reply(cause_priorities=[{"cause_id": "payment_errors", "rationale": "Payment errors may matter most."}],
                 check_priorities=[{"check_id": c, "rationale": "It could help."} for c in others], action_guidance=[])
    result = prioritise.prioritise(marketing, evidence, FakeProvider(text))

    assert "tracking_diagnostics" in [i.id for i in result.top_investigations]


# Failures leave the deterministic diagnosis intact


@pytest.mark.parametrize(
    "error",
    [AIRequestError("The AI usage limit has been reached."), AIRequestError("The AI service did not respond in time.")],
)
def test_provider_failures_raise_and_change_nothing(error):
    marketing, evidence = case()

    with pytest.raises(AIRequestError):
        prioritise.prioritise(marketing, evidence, FakeProvider(error=error))
    assert marketing.ai is None


def test_refused_reply_is_still_validated():
    marketing, evidence = case()
    partial = reply(cause_priorities=[{"cause_id": "not_allowed", "rationale": "x"},
                                      {"cause_id": "seasonality", "rationale": "Seasonal demand may matter."}])
    provider = FakeProvider(error=AIResponseError("format", partial_text=partial))

    result = prioritise.prioritise(marketing, evidence, provider)

    assert result.causes[0].id == "seasonality"
    assert (result.ai.input_tokens, result.ai.output_tokens) == (None, None)


@pytest.mark.parametrize("text", ["not json", "[]", '"text"'])
def test_unreadable_replies_are_format_failures(text):
    with pytest.raises(AIResponseError, match="required format"):
        run(text)


# Page and report


def test_page_and_report_show_the_same_ai_content():
    from datetime import date

    marketing, evidence = case()
    diagnosis = run_diagnosis(load_meta_csv(SAMPLES[0].path).data, WEEKLY)
    result = run(reply(), marketing, evidence)
    page = (render.ai_status_html(result.ai) + render.possible_causes_html(result)
            + render.investigations_html(result) + render.actions_html(result))
    html = report.build_report(report.ReportInput(diagnosis, evidence, "S", "All campaigns", "a", "b", "c",
                                                  date(2026, 11, 3), "EUR", marketing=result))

    for text in ("A budget or bid change could explain a sudden rise.", "It could quickly rule self-made changes in or out.",
                 "If the change history shows a budget jump, scale more gradually.", "Fake fake-model"):
        assert text in page and text in html
    assert "5. AI prioritisation" in html and "Prioritised by Fake fake-model" in html


# Shared usage limit


def test_usage_limiter_enforces_minute_and_day_limits():
    now = [1000.0]
    limiter = service.UsageLimiter(per_minute=1, per_day=2, clock=lambda: now[0])

    assert limiter.acquire() is None
    assert "try again in about" in limiter.acquire()
    now[0] += 61
    assert limiter.acquire() is None
    now[0] += 61
    assert "for today has been used" in limiter.acquire()
    now[0] += 86_400
    assert limiter.acquire() is None


# Live-test corrections: mathematical consistency, playbook-only checks, readable text


def sep_oct_reply(**overrides):
    body = {
        "cause_priorities": [{"cause_id": "delivery_shift", "rationale": "A targeting shift could possibly explain both problems."}],
        "check_priorities": [{"check_id": "campaign_split", "rationale": "It shows whether one campaign drives both changes."}],
        "relationship": "",
        "action_guidance": [],
    }
    body.update(overrides)
    return json.dumps(body)


def test_request_states_roas_direction_and_each_kpis_role():
    marketing, evidence = sep_oct()
    provider = FakeProvider(sep_oct_reply())
    prioritise.prioritise(marketing, evidence, provider)
    payload = json.loads(provider.calls[0][1].split("\n\n", 1)[1])

    assert payload["roas"]["direction"] == "rose"
    assert payload["roas"]["summary"].startswith("ROAS rose")
    assert [p["role"] for p in payload["problems"]] == ["Offsetting driver", "Offsetting driver"]
    assert [i["role"] for i in payload["improvements"]] == ["Positive contributor", "Positive contributor"]


@pytest.mark.parametrize(
    "text",
    [
        "The CPM increase accounts for the majority of the ROAS decline.",
        "CPM may have caused the largest ROAS drop.",
        "ROAS fell, so auction factors may matter most.",
        "CTR fell, so creative factors may matter.",
        "Checkout may matter because CPM dropped sharply.",
    ],
)
def test_explanations_contradicting_the_calculation_are_withheld(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == ""
    assert any("contradicts the calculation" in issue for issue in result.ai.withheld)


@pytest.mark.parametrize(
    "text",
    [
        "ROAS rose even though CPM increased, so the CPM rise was offset.",
        "Higher CPM partly offset the ROAS gain.",
        "Test a lower bid to see if CPM falls.",
        "ROAS did not fall, but CPM may still deserve a check.",
    ],
)
def test_explanations_matching_the_calculation_are_kept(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == text


def test_reversed_roas_in_the_relationship_is_withheld():
    marketing, evidence = sep_oct()
    text = "The ROAS decline may be explained by a targeting shift that affected both problems."
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(relationship=text)))

    assert result.ai.relationship == ""


def test_invented_kpi_movement_is_withheld():
    result = run(reply(cause_priorities=[{"cause_id": "audience_saturation", "rationale": "Frequency increased, so saturation may matter."}]))

    assert next(c for c in result.causes if c.id == "audience_saturation").ai_note == ""


def test_ai_cannot_add_an_unrelated_investigation():
    marketing, evidence = case()
    text = reply(extra_checks=[{"title": "Landing page load speed", "detail": "Measure load times.",
                                "rationale": "It may matter.", "cause_id": "auction_competition"}])
    result = prioritise.prioritise(marketing, evidence, FakeProvider(text))

    assert "extra_checks" not in prioritise.response_schema(marketing)["properties"]
    assert {i.id for i in result.investigations} == {i.id for i in marketing.investigations}
    assert "Landing page load speed" not in [i.title for i in result.investigations]


def test_internal_names_are_replaced_with_readable_titles():
    text = reply(action_guidance=[{"cause_id": "budget_bid_changes",
                                   "guidance": "If account_change_log and campaign_split show a budget jump on cpm, scale more gradually."}])
    result = run(text)
    guidance = next(a.ai_note for a in result.actions if a.cause == "Budget scaling or bid strategy")

    assert guidance == "If Account change history and Compare each campaign separately show a budget jump on CPM, scale more gradually."
    assert "_" not in guidance


def test_a_general_statement_about_a_common_reason_is_kept():
    marketing, evidence = case(3)  # Checkout friction
    text = "Higher or unexpected shipping fees are a common reason for last-minute drop-off."
    result = prioritise.prioritise(marketing, evidence, FakeProvider(reply(
        cause_priorities=[{"cause_id": "shipping_costs", "rationale": text}], check_priorities=[], action_guidance=[])))

    assert result.causes[0].ai_note == text


@pytest.mark.parametrize(
    "text",
    ["CPM and checkout share a common cause.", "Both problems were caused by the same targeting change."],
)
def test_claims_that_problems_share_a_cause_are_still_withheld(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(relationship=text)))

    assert result.ai.relationship == ""


def test_withheld_text_and_reason_are_logged_for_review(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="funnel_magnet.ai.review"):
        run(reply(cause_priorities=[{"cause_id": "auction_competition", "rationale": "Competition drove the increase."}]))

    assert any("states a cause as fact" in r.getMessage() and "Competition drove the increase." in r.getMessage()
               for r in caplog.records)


# Direction checks with several KPIs in one sentence. Each direction word belongs to
# the KPI next to it, and possibilities aren't claims.

CPM_UP_CTR_DOWN = {"ROAS": -1, "CPM": 1, "CTR": -1}


@pytest.mark.parametrize(
    "text",
    [
        "Higher CPM and lower CTR may point to audience saturation.",
        "CPM rose while checkout conversion declined.",
        "A saturated audience can raise CPM and lower CTR.",
        "If the placement breakdown shows placements with higher CPM and lower CTR, move budget.",
        "Shows ad quality scores that can explain higher CPM if ads are rated lower.",
        "Breaks CPM down by placement.",
        "CTR could rise if the creative is refreshed.",
        "CPM did not fall, and the ROAS decline may need a check.",
    ],
)
def test_several_kpis_in_one_sentence_are_read_separately(text):
    assert prioritise._direction_issues("Text", text, CPM_UP_CTR_DOWN) == []


@pytest.mark.parametrize(
    "text, kpis",
    [
        ("Lower CPM and lower CTR may point to saturation.", {"CPM"}),
        ("Higher CPM and higher CTR suggest a placement change.", {"CTR"}),
        ("CPM and CTR both fell.", {"CPM"}),
        ("CPM increased, but CTR improved.", {"CTR"}),
        ("ROAS improved despite higher CPM.", {"ROAS"}),
        ("CPM fell while CTR rose.", {"CPM", "CTR"}),
        ("CTR fell, so ROAS rose.", {"ROAS"}),
        ("Saturation could matter because of higher CTR.", {"CTR"}),
        ("This may be due to higher CTR.", {"CTR"}),
        ("Seasonality is not the issue; the ROAS gain is.", {"ROAS"}),
        ("CPM was down 18.3% this week.", {"CPM"}),
        ("A better CPM may reflect cheaper placements.", {"CPM"}),
    ],
)
def test_genuine_contradictions_are_still_found(text, kpis):
    issues = prioritise._direction_issues("Text", text, CPM_UP_CTR_DOWN)

    assert {issue.split(": ")[1].split()[0] for issue in issues} == kpis


@pytest.mark.parametrize(
    "text",
    [
        "CTR could fall if audience saturation increases.",
        "Expect CTR to fall once frequency climbs.",
        "Test a creative refresh that could potentially lower CPM.",
    ],
)
def test_possible_movements_are_not_claims(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == text


@pytest.mark.parametrize(
    "text",
    [
        "Higher CPM and lower CTR may point to a delivery shift.",
        "CTR fell, so the delivery shift may matter.",
        "A delivery shift may matter because of lower CTR.",
        "Likely to raise CPM alone, which accounts for the largest ROAS drop.",
    ],
)
def test_observed_movements_against_the_calculation_are_withheld(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == ""
    assert any("contradicts the calculation" in issue for issue in result.ai.withheld)


# Checks can support or weaken a possible cause, never prove it.

@pytest.mark.parametrize(
    "text",
    [
        "The change log shows any budget edits, directly confirming or ruling out those possible causes.",
        "Placement data shows if delivery moved, confirming placement-change hypotheses.",
        "This check will confirm the auction competition cause.",
        "It pinpoints the exact cause of the CPM rise.",
    ],
)
def test_checks_claiming_to_prove_a_cause_are_withheld(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        check_priorities=[{"check_id": "account_change_log", "rationale": text}])))

    assert next(i for i in result.investigations if i.id == "account_change_log").ai_note == ""
    assert any("prove a cause" in issue for issue in result.ai.withheld)


@pytest.mark.parametrize(
    "text",
    [
        "The promotion calendar helps confirm or rule out discount changes as a possible cause.",
        "Checking reach and frequency helps confirm whether the same people see the ads.",
        "It confirms whether any targeting changes occurred.",
        "It can support or weaken the account structure hypothesis.",
    ],
)
def test_checks_that_support_or_weaken_a_cause_are_kept(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        check_priorities=[{"check_id": "account_change_log", "rationale": text}])))

    assert next(i for i in result.investigations if i.id == "account_change_log").ai_note == text


# September to October: ROAS, CTR and CVR improved while CPM and checkout conversion
# worsened. A possible movement can't explain the problem if the data contradicts it.

def test_sep_oct_directions_and_improvements_are_known():
    signs = prioritise._directions(sep_oct()[1])

    assert {k: signs[k] for k in ("ROAS", "CTR", "CPM", "CVR")} == {"ROAS": 1, "CTR": 1, "CPM": 1, "CVR": 1}
    assert {"ROAS", "CTR", "CVR"} <= signs.improved
    assert "CPM" not in signs.improved


@pytest.mark.parametrize(
    "text",
    [
        "CTR may have declined due to audience saturation.",
        "If the core audience is saturated, CTR may fall and CPM rise.",
        "Audience saturation can lower CTR and raise CPM.",
        "CTR may fall further as fatigue sets in.",
        "ROAS might have dropped because of the CPM rise.",
    ],
)
def test_sep_oct_possible_movements_against_the_data_are_withheld(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "audience_saturation", "rationale": text}])))

    assert next(c for c in result.causes if c.id == "audience_saturation").ai_note == ""
    assert any("contradicts the calculation" in issue for issue in result.ai.withheld)


@pytest.mark.parametrize(
    "text",
    [
        "A delivery shift could raise CPM and bring visitors less likely to complete checkout.",
        "Moving spend toward weaker campaigns may lift CPM while lowering the Checkout → Purchase rate.",
        "CPM may have risen because of auction competition, while ROAS still rose.",
        "Higher CPM partly offset the ROAS gain, and CTR rose.",
        "CTR could fall if audience saturation increases, so watch frequency.",
        "If CPM keeps rising, ROAS could fall in future periods.",
        "The rise in CPM could reduce ROAS, which is why it offsets part of the gain.",
        "A lower checkout rate could pull CVR down even though CVR rose overall.",
        "A targeting shift could explain both the CPM rise and the checkout drop, or they may be separate.",
    ],
)
def test_sep_oct_valid_explanations_are_kept(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == text
    assert result.ai.withheld == ()


def test_sep_oct_relationship_and_guidance_follow_the_same_rule():
    marketing, evidence = sep_oct()
    kept_guidance = "If the frequency check shows rising frequency, refresh creative before CTR starts to fall."
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        relationship="CTR may have fallen with the CPM rise, so both could share a cause.",
        action_guidance=[
            {"cause_id": "audience_saturation", "guidance": kept_guidance},
            {"cause_id": "delivery_shift", "guidance": "If the breakdown shows a shift, note that CTR may have declined as well."},
        ])))

    assert result.ai.relationship == ""
    guidance = {a.cause: a.ai_note for a in result.actions if a.ai_note}
    assert list(guidance.values()) == [kept_guidance]



# Presentation pass: overclaims, unsupported scope, likelihood ratings and repetition.

@pytest.mark.parametrize(
    "text, reason",
    [
        ("Comparing the same weeks last year isolates seasonal effects.", "isolate or pin down"),
        ("Results by audience help pinpoint saturation.", "isolate or pin down"),
        ("The upload already contains ad set results to test this.", "ad sets, ads, placements or audiences"),
        ("Placement data is available in this app, so check it first.", "ad sets, ads, placements or audiences"),
        ("Auction competition is the most likely factor here.", "how likely"),
        ("This makes it a strong candidate for the CPM rise.", "how likely"),
        ("Seasonality is probably behind part of the rise.", "how likely"),
    ],
)
def test_misleading_ai_claims_are_withheld(text, reason):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == ""
    assert any(reason in issue for issue in result.ai.withheld)


@pytest.mark.parametrize(
    "text",
    [
        "It shows whether the CPM rise is isolated to one campaign, which is worth knowing before changing budgets.",
        "Frequency data is not yet in the upload, so it needs an export from Ads Manager.",
        "CPM is the main driver of the decline in the calculation, so a delivery shift is worth checking first.",
        "If spend moved to weaker campaigns, a mix shift would become more likely.",
    ],
)
def test_sound_ai_explanations_are_still_kept(text):
    marketing, evidence = sep_oct()
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        cause_priorities=[{"cause_id": "delivery_shift", "rationale": text}])))

    assert result.causes[0].ai_note == text


def test_ai_notes_that_only_repeat_the_playbook_are_left_out_and_counted():
    marketing, evidence = sep_oct()
    payment = next(i for i in marketing.investigations if i.id == "payment_logs")
    result = prioritise.prioritise(marketing, evidence, FakeProvider(sep_oct_reply(
        check_priorities=[{"check_id": "payment_logs", "rationale": payment.detail}])))

    assert next(i for i in result.investigations if i.id == "payment_logs").ai_note == ""
    assert result.ai.repeated == 1 and result.ai.withheld == ()


def test_the_prompt_sets_the_new_limits():
    for rule in ("isolates or pins one down", "ad sets, individual ads, placements or audiences", "Never rate how likely"):
        assert rule in prioritise.SYSTEM_PROMPT
