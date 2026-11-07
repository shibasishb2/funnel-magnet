"""Level 1, the executive diagnosis: plain English, deterministic, and faithful to Level 2."""

import re
from datetime import date
from html import escape, unescape

import pytest

from funnel_magnet import playbook
from funnel_magnet.ai import prioritise
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
from funnel_magnet.metrics import Totals
from funnel_magnet.periods import MONTHLY, WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import executive, render, report, view
from test_two_way_tree import OCTOBER, SEPTEMBER

SAMPLES = view.load_samples()
CAUSAL = re.compile(r"\b(caused|causes|due to|because of|proves?|the reason)\b", re.IGNORECASE)


def scenario(name, granularity=WEEKLY):
    if name == "Sep-Oct":
        diagnosis, currency, warnings = diagnose_totals(SEPTEMBER, OCTOBER), "EUR", ()
        diagnosis.prior_label, diagnosis.current_label = "2026-09", "2026-10"
    else:
        loaded = load_meta_csv(next(s for s in SAMPLES if s.name == name).path)
        diagnosis, currency = run_diagnosis(loaded.data, granularity), loaded.currency
        warnings = tuple(issue.message for issue in loaded.warnings)
    marketing = playbook.build(diagnosis, currency, several_campaigns=True, earlier_periods=True)
    return diagnosis, marketing, currency, warnings


ALL = [*(s.name for s in SAMPLES), "Sep-Oct"]


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize("name", [s.name for s in SAMPLES])
def test_every_scenario_answers_the_three_questions(name, granularity):
    diagnosis, marketing, currency, warnings = scenario(name, granularity)
    summary = executive.build(diagnosis, marketing, warnings, currency=currency)

    # What changed: the headline, ROAS and at most three other KPIs, and the path to ROAS.
    assert summary.headline.startswith("ROAS")
    assert summary.tiles[0].label == "ROAS" and len(summary.tiles) <= 1 + executive.EXTRA_TILES
    for chain in summary.mechanisms:
        assert " → " in chain and chain.endswith("points")
    # What needs attention: no materially worse final KPI is simplified away.
    assert [p.label for p in summary.problems] == [m.label for m in marketing.problems]
    assert all(p.why for p in summary.problems)
    # What to investigate: the same top three, each with a supporting result and an action.
    assert [c.title for c in summary.checks] == [i.title for i in marketing.top_investigations]
    actions = {a.cause: a.if_supported for a in marketing.actions}
    causes = {c.title: c for c in marketing.causes}
    for check, item in zip(summary.checks, marketing.top_investigations):
        if check.cause and check.supports_if == executive._neutral(check.cause):
            assert set(check.other_readings) == set(item.readings)
        else:
            assert {check.supports_if, *check.other_readings} == set(item.readings)
        if check.cause:
            assert check.cause in item.tests and not causes[check.cause].contradicted
            assert check.action == actions[check.cause]
    # Python's own sentences never claim a cause.
    assert not CAUSAL.search(summary.headline)
    assert not any(CAUSAL.search(chain) for chain in summary.mechanisms)


def test_falling_roas_names_the_biggest_drag():
    diagnosis, marketing, currency, _ = scenario("CPM inflation")
    summary = executive.build(diagnosis, marketing, currency=currency)

    assert summary.headline == "ROAS fell 14.3%. The biggest drag was cost per 1,000 impressions (CPM up 18.3%)."
    assert [t.label for t in summary.tiles] == ["ROAS", "CPM", "CPC", "CPA"]
    assert summary.mechanisms == ("CPM +18.3% → CPC +17.1% → CPA +16.8% → ROAS -0.64 points",)
    assert not summary.problems[0].hidden and summary.cancel == ""


def test_stable_roas_with_an_offsetting_problem_is_flagged_as_hidden():
    diagnosis, marketing, currency, _ = scenario("Masked problem")
    summary = executive.build(diagnosis, marketing, currency=currency)

    assert summary.headline.startswith("ROAS held steady (+2.9%), but cost per 1,000 impressions (CPM up 18.3%) worsened.")
    assert "Gains in average order value are hiding it." in summary.headline
    assert [p.hidden for p in summary.problems] == [True]
    # The gain that hides the problem is shown with it.
    assert [t.label for t in summary.tiles][:3] == ["ROAS", "CPM", "AOV"]
    assert summary.mechanisms[1] == "AOV +20.1% → ROAS +0.92 points"


def test_rising_roas_with_hidden_problems_names_both():
    diagnosis, marketing, currency, _ = scenario("Sep-Oct")
    summary = executive.build(diagnosis, marketing, currency=currency)

    assert summary.headline.startswith("ROAS rose 14.7%, but cost per 1,000 impressions (CPM up 44.4%) and ")
    assert "purchases per checkout started (down 11.2%)" in summary.headline
    assert all(p.hidden for p in summary.problems) and len(summary.problems) == 2
    # The real diagnostic path, including the steps that moved the other way.
    assert summary.mechanisms == (
        "CPM +44.4% → CPC -3.0% → CPA -9.1% → ROAS -1.24 points",
        "Checkout → Purchase -11.2% → CVR +6.7% → CPA -9.1% → ROAS -0.40 points",
    )
    # The playbook's note on how the problems may relate is in the detailed analysis.
    assert all(pattern in render.possible_causes_html(marketing) for pattern in marketing.patterns)


def test_healthy_control_says_nothing_worsened():
    diagnosis, marketing, currency, _ = scenario("Healthy control")
    summary = executive.build(diagnosis, marketing, currency=currency)

    assert summary.headline == "ROAS held steady (+1.4%), and no KPI worsened by 5% or more."
    assert summary.problems == () and summary.checks == () and [t.label for t in summary.tiles] == ["ROAS"]


@pytest.mark.parametrize("name", ["Landing page issue", "Checkout friction"])
def test_funnel_problems_put_the_tracking_step_among_the_next_steps(name):
    summary = executive.build(*scenario(name)[:2])

    assert "Tracking diagnostics" in [c.title for c in summary.checks]
    assert "Tracking problem" in [s.cause for s in summary.next_steps]


@pytest.mark.parametrize("name", ALL)
def test_every_investigation_explains_what_to_look_at_and_what_results_could_mean(name):
    diagnosis, marketing, _, _ = scenario(name)
    labels = {move.label for move in marketing.problems}

    for item in marketing.investigations:
        texts = [item.look_at, *item.readings, item.if_unclear]
        assert all(texts) and len(item.readings) >= 1
        assert not any("{" in text for text in texts)
        # Results are possibilities: nothing is presented as proof.
        assert not any(re.search(r"\b(confirms?|proven|definitely|certainly)\b", text, re.I) for text in texts)
        assert not any(re.search(r"\bproves?\b", text) and "not prove" not in text for text in texts)
    # The generic checks name the KPIs that actually worsened.
    split = next((i for i in marketing.investigations if i.id == "campaign_split"), None)
    if split:
        assert all(label in split.readings[0] for label in labels)


def test_readings_are_specific_rather_than_repeated():
    readings = [r for item in playbook.load_playbook().investigations.values() for r in item["readings"]]

    assert len(readings) == len(set(readings))
    assert not any("makes that cause more likely" in reading for reading in readings)


def test_each_card_says_what_to_check_what_would_support_it_and_what_to_do():
    diagnosis, marketing, _, _ = scenario("CPM inflation")
    card = executive.build(diagnosis, marketing).checks[0]
    item = marketing.top_investigations[0]

    assert card.look_at == item.look_at and card.cause == "Account structure"
    # No campaign-comparison reading is about account structure, so the card says so neutrally.
    assert card.supports_if == "If this check's results point to account structure, that would support this possible cause."
    assert card.action.startswith("Consolidate fragmented ad sets")
    assert set(card.other_readings) == set(item.readings)
    # The other readings and the uncertainty note are folded away on the page.
    html = render.executive_html(executive.build(diagnosis, marketing))
    folded = html[html.index("<details"):html.index("</details>")]
    assert escape(card.if_unclear) in folded and all(escape(r) in folded for r in card.other_readings)


def test_a_weakening_reading_is_never_shown_as_support():
    diagnosis, marketing = uploaded_pattern()
    for card in executive.build(diagnosis, marketing).checks:
        assert not executive.WEAKENING.search(card.supports_if)


def test_data_notes_are_short_and_complete():
    diagnosis, marketing, _, warnings = scenario("CPM inflation")
    notes = executive.build(diagnosis, marketing, warnings).data_notes

    assert [note.split(":")[0] for note in notes] == list(view.group_warnings([*warnings, *diagnosis.warnings]))


def test_every_kpi_has_a_plain_name_and_why_it_matters():
    for metric in (*playbook.KNOWN_METRICS, "roas"):
        assert view.PLAIN_NAMES[metric] and view.WHY_IT_MATTERS[metric].endswith(".")


def test_ai_prioritisation_reorders_level_one_but_not_the_headline():
    diagnosis, marketing, currency, _ = scenario("CPM inflation")
    reply = {
        "cause_priorities": [{"cause_id": "budget_bid_changes", "rationale": "A budget or bid change could push delivery into pricier impressions."}],
        "check_priorities": [{"check_id": "account_change_log", "rationale": "It could show whether a budget or bid edit came before the CPM rise."}],
        "relationship": "",
        "action_guidance": [],
    }
    prioritised = prioritise.merge(marketing, reply, build_evidence(diagnosis, currency), "Groq", "openai/gpt-oss-120b")
    before, after = executive.build(diagnosis, marketing), executive.build(diagnosis, prioritised)

    assert after.headline == before.headline and after.mechanisms == before.mechanisms
    assert after.problems == before.problems
    assert after.checks[0].title == "Account change history"
    assert after.checks[0].cause == "Budget scaling or bid strategy"
    # The AI's ranking commentary stays in the detailed analysis, not the summary.
    assert "It could show whether" not in render.executive_html(after)
    assert "It could show whether" in render.investigations_html(prioritised)


@pytest.mark.parametrize("name", ALL)
def test_page_and_report_show_the_same_executive_diagnosis(name):
    diagnosis, marketing, currency, warnings = scenario(name)
    summary = executive.build(diagnosis, marketing, warnings, currency=currency)
    page = render.executive_html(summary)
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, currency), "File", "All campaigns", "a", "b", "c",
        date(2026, 11, 3), currency, warnings, marketing=marketing,
    ))
    executive_part = html[html.index('<section class="executive">'):html.index('<h2 class="part">Detailed analysis</h2>')]

    assert html.index('<section class="executive">') < html.index("1. A. Confirmed findings")
    pieces = [summary.headline, *summary.mechanisms, *summary.data_notes, summary.cancel]
    pieces += [p.why for p in summary.problems]
    pieces += [part for c in summary.checks for part in (c.title, c.look_at, c.supports_if, c.cause, c.action)]
    pieces += [c.title for _, checks in summary.stage_checks for c in checks]
    for piece in filter(None, pieces):
        assert escape(piece) in page and escape(piece) in executive_part, piece
    assert page.count("Hidden by overall ROAS") == executive_part.count("Hidden by overall ROAS")
    # Every check's full interpretation is in section C on the page and section 3 in the report.
    section_c = render.investigations_html(marketing)
    for item in marketing.other_investigations:
        for piece in (item.look_at, *item.readings, item.if_unclear):
            assert escape(piece) in section_c and escape(piece) in html
    for item in marketing.top_investigations:
        for piece in (item.look_at, *item.readings, item.if_unclear):
            assert escape(piece) in page and escape(piece) in html


@pytest.mark.parametrize("name", [s.name for s in SAMPLES])
def test_the_summary_stays_short(name):
    diagnosis, marketing, currency, warnings = scenario(name)
    html = render.executive_html(executive.build(diagnosis, marketing, warnings, name, True, currency), "a", "b")
    visible = re.sub(r"<details.*?</details>", " ", html, flags=re.S)
    words = len(unescape(re.sub(r"<[^>]+>", " ", visible)).split())

    assert words <= 400, words


def test_card_wording_reads_as_plain_english():
    card = executive.build(*scenario("CTR decline")[:2]).checks[0]

    assert (card.title, card.cause) == ("CTR for each ad", "Creative fatigue")
    assert card.supports_if == "If a few long-running ads lost most of their CTR, creative fatigue is a likely candidate."
    assert card.action == "Refresh or rotate the most-seen creatives, and consider a frequency cap."


@pytest.mark.parametrize("synthetic", [True, False])
def test_synthetic_and_uploaded_results_are_labelled_on_the_page_and_in_the_report(synthetic):
    diagnosis, marketing, currency, warnings = scenario("CPM inflation")
    name = "CPM inflation" if synthetic else "my_export.csv"
    summary = executive.build(diagnosis, marketing, warnings, name, synthetic)
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, currency), name, "All campaigns", "a", "b", "c",
        date(2026, 11, 3), currency, warnings, marketing=marketing, synthetic=synthetic,
    ))

    if synthetic:
        assert summary.source == "Synthetic sample data (\u201cCPM inflation\u201d), not real campaign results."
    else:
        assert summary.source == "Your uploaded data: my_export.csv."
    assert escape(summary.source) in render.executive_html(summary) and escape(summary.source) in html
    other = "Your uploaded data" if synthetic else "Synthetic sample data"
    assert other not in render.executive_html(summary) and other not in html


# Presentation pass: event-based names, offsetting, merged steps and direct checkout checks.

def test_funnel_stages_are_described_as_event_ratios_not_people():
    for metric in playbook.FUNNEL_STAGES:
        name, why = view.PLAIN_NAMES[metric], view.WHY_IT_MATTERS[metric]
        assert " per " in name and not re.search(r"visitor|shopper|share of", name + why)
    assert view.PLAIN_NAMES["cvr"] == "purchases per link click"


def test_offsetting_movements_are_explained_in_plain_english():
    for name, net in (("Masked problem", "+0.14"), ("Sep-Oct", "+0.46")):
        summary = executive.build(*scenario(name)[:2])
        assert "largely cancel out" in summary.cancel and summary.cancel.endswith(f"net change of only {net}.")
    assert executive.build(*scenario("CPM inflation")[:2]).cancel == ""
    # What "% of all movement" means is explained where the shares are shown.
    diagnosis, marketing, currency, warnings = scenario("Masked problem")
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, currency), "File", "All campaigns", "a", "b", "c",
        date(2026, 11, 3), currency, warnings, marketing=marketing,
    ))
    assert escape(executive.SHARE_NOTE) in html


def test_september_october_considers_checkout_checks_after_tracking():
    summary = executive.build(*scenario("Sep-Oct")[:2])

    assert "Tracking diagnostics" in [c.title for c in summary.checks]
    # The shared-cause checks keep their places in the top three.
    assert [c.title for c in summary.checks][:2] == ["Compare each campaign separately", "Results by audience"]
    label, checks = summary.stage_checks[0]
    assert label == "Checkout → Purchase"
    assert [c.title for c in checks] == ["Website change log", "Payment logs", "Shipping costs shown", "Checkout step completion"]


def test_stage_checks_leave_out_what_the_top_three_already_cover():
    summary = executive.build(*scenario("Checkout friction")[:2])
    titles = [c.title for _, checks in summary.stage_checks for c in checks]

    assert "Website change log" in [c.title for c in summary.checks] and "Website change log" not in titles
    assert titles == ["Payment logs", "Shipping costs shown", "Checkout step completion"]


def test_near_identical_next_steps_are_merged_but_kept_in_detail():
    diagnosis, marketing, currency, _ = scenario("Checkout friction")
    reply = {
        "cause_priorities": [{"cause_id": "payment_errors", "rationale": ""}, {"cause_id": "payment_options", "rationale": ""}],
        "check_priorities": [{"check_id": "payment_logs", "rationale": ""}],
        "relationship": "",
        "action_guidance": [],
    }
    prioritised = prioritise.merge(marketing, reply, build_evidence(diagnosis, currency), "Groq", "openai/gpt-oss-120b")
    steps = executive.build(diagnosis, prioritised).next_steps

    payment = next(s for s in steps if s.cause == "Payment errors")
    assert payment.related == ("Payment options",)
    assert "Payment options" not in [s.cause for s in steps]
    card = next(c for c in executive.build(diagnosis, prioritised).checks if c.cause == "Payment errors")
    assert card.related == ("Payment options",)
    assert "(related: Payment options)" in render.executive_html(executive.build(diagnosis, prioritised))
    # The detailed analysis still lists both causes and both actions.
    assert {"Payment errors", "Payment options"} <= {a.cause for a in prioritised.actions}


def test_funnel_explanations_keep_tracking_open_as_a_possibility():
    for metric in playbook.FUNNEL_STAGES:
        why = view.WHY_IT_MATTERS[metric]
        assert "may" in why and "tracking issue" in why and "should be investigated" in why
    assert view.WHY_IT_MATTERS["checkouts_initiated_to_purchases"] == (
        "Fewer purchases are recorded for each checkout started. This may indicate lost sales "
        "during checkout or a tracking issue, both of which should be investigated."
    )


# Cross-KPI consistency: the September-October uploaded pattern, from an account-level file.
# CPM +44.4%, CTR +48.9%, Checkout -> Purchase -12.2%, ROAS +13.3%.

UPLOADED_OCTOBER = Totals(100_000.0, 224_488.0, 4578.0, 60 / 113 * 0.878 * 140, 3.1596 * 1.133 * 100_000, 3975.0, 708.0, 140.0)


def uploaded_pattern():
    diagnosis = diagnose_totals(SEPTEMBER, UPLOADED_OCTOBER)
    diagnosis.prior_label, diagnosis.current_label = "2026-09", "2026-10"
    return diagnosis, playbook.build(diagnosis, "INR", several_campaigns=False, earlier_periods=True)


def test_the_uploaded_pattern_matches_the_reported_figures():
    diagnosis, _ = uploaded_pattern()
    changes = {k: round(diagnosis.changes[k].pct_change * 100, 1) for k in ("cpm", "ctr", "checkouts_initiated_to_purchases", "roas")}

    assert changes == {"cpm": 44.4, "ctr": 48.9, "checkouts_initiated_to_purchases": -12.2, "roas": 13.3}


def test_saturation_is_not_promoted_when_ctr_rose_but_stays_in_detail():
    diagnosis, marketing = uploaded_pattern()
    summary = executive.build(diagnosis, marketing)
    saturation = next(c for c in marketing.causes if c.id == "audience_saturation")

    assert "Audience saturation" not in [s.cause for s in summary.next_steps]
    assert not any("lower CTR" in s.statement for s in summary.next_steps)
    assert not any(c.cause == "Audience saturation" for c in summary.checks)
    # Still a possibility in the detailed analysis, with the evidence stated plainly.
    assert saturation.contradicted
    assert dict((label, kind) for label, kind, _ in saturation.signs) == {"CTR": "contradicts", "CPM": "supports"}
    assert "without ruling the cause out" in render.possible_causes_html(marketing)


def test_every_promoted_step_stays_tied_to_the_top_three():
    diagnosis, marketing = uploaded_pattern()
    summary = executive.build(diagnosis, marketing)
    top = {c.title for c in summary.checks}

    assert [s.cause for s in summary.next_steps] == ["Targeting, placement or audience shift", "Traffic quality", "Tracking problem"]
    # Each card carries the cause chosen for it, so no check is left without an action.
    assert [c.cause for c in summary.checks] == ["Targeting, placement or audience shift", "Traffic quality", "Tracking problem"]
    for step in summary.next_steps:
        assert {title for _, title in step.checks} <= top and step.checks


def test_saturation_stays_a_next_step_when_ctr_really_fell():
    diagnosis, marketing, _, _ = scenario("CTR decline")
    saturation = next(c for c in marketing.causes if c.id == "audience_saturation")

    assert not saturation.contradicted
    assert dict((label, kind) for label, kind, _ in saturation.signs)["CTR"] == "supports"
    assert "Audience saturation" in [s.cause for s in executive.build(diagnosis, marketing).next_steps]


@pytest.mark.parametrize(
    "name, steps",
    [
        ("CPM inflation", ["Account structure", "Seasonality", "Budget scaling or bid strategy"]),
        ("Masked problem", ["Account structure", "Seasonality", "Budget scaling or bid strategy"]),
        ("Landing page issue", ["Tracking problem", "Campaign mix shift", "Landing page speed or errors"]),
        ("Checkout friction", ["Tracking problem", "Payment options", "Campaign mix shift"]),
        ("Sep-Oct", ["Campaign mix shift", "Targeting, placement or audience shift", "Tracking problem"]),
    ],
)
def test_other_scenarios_keep_their_next_steps(name, steps):
    assert [s.cause for s in executive.build(*scenario(name)[:2]).next_steps] == steps


def test_ai_ordering_cannot_promote_a_contradicted_cause():
    diagnosis, marketing = uploaded_pattern()
    reply = {
        "cause_priorities": [{"cause_id": "audience_saturation", "rationale": ""}],
        "check_priorities": [{"check_id": "audience_breakdown", "rationale": ""}, {"check_id": "frequency_reach", "rationale": ""}],
        "relationship": "",
        "action_guidance": [],
    }
    prioritised = prioritise.merge(marketing, reply, build_evidence(diagnosis, "INR"), "Groq", "openai/gpt-oss-120b")

    assert prioritised.causes[0].id == "audience_saturation"
    assert "Audience saturation" not in [s.cause for s in executive.build(diagnosis, prioritised).next_steps]


def test_page_and_report_show_the_same_evidence_note():
    diagnosis, marketing = uploaded_pattern()
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, "INR"), "File", "All campaigns", "a", "b", "c", date(2026, 11, 4), "INR", marketing=marketing,
    ))
    note = next(text for _, kind, text in next(c for c in marketing.causes if c.id == "audience_saturation").signs if kind == "contradicts")

    assert escape(note) in render.possible_causes_html(marketing) and escape(note) in html



def test_a_card_never_borrows_another_causes_evidence():
    """Every supporting reading names its own cause; otherwise the card uses a neutral line."""
    cases = [scenario(name)[:2] for name in ALL] + [uploaded_pattern()]
    cases += [(d, playbook.build(d, "EUR", several_campaigns=False, earlier_periods=True)) for d, _ in cases[:7]]
    for diagnosis, marketing in cases:
        for card in executive.build(diagnosis, marketing).checks:
            if not card.cause:
                continue
            neutral = card.supports_if == executive._neutral(card.cause)
            assert neutral or executive._about(card.supports_if, card.cause), (card.title, card.cause, card.supports_if)
            assert not executive.WEAKENING.search(card.supports_if)


@pytest.mark.parametrize(
    "name, title, cause, reading",
    [
        ("CPM inflation", "Compare earlier periods", "Seasonality",
         "A similar change at the same point in earlier cycles may suggest seasonality, but it does not prove it."),
        ("CTR decline", "Frequency and reach over time", "Audience saturation",
         "If frequency rose while reach flattened or fell, the same people are seeing the ads more often, "
         "which supports creative fatigue or audience saturation."),
    ],
)
def test_a_reading_about_the_cause_is_chosen_over_a_neighbouring_one(name, title, cause, reading):
    card = next(c for c in executive.build(*scenario(name)[:2]).checks if c.title == title)

    assert (card.cause, card.supports_if) == (cause, reading)


def test_a_reading_naming_a_different_cause_is_not_used():
    diagnosis, marketing = uploaded_pattern()
    card = next(c for c in executive.build(diagnosis, marketing).checks if c.title == "Results by placement")

    assert card.cause == "Traffic quality"
    assert card.supports_if == "If this check's results point to traffic quality, that would support this possible cause."
