"""Level 1, the executive diagnosis: plain English, deterministic, and faithful to Level 2."""

import re
from datetime import date
from html import escape

import pytest

from funnel_magnet import playbook
from funnel_magnet.ai import prioritise
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
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
def test_every_scenario_keeps_every_problem_and_the_same_top_three(name, granularity):
    diagnosis, marketing, _, warnings = scenario(name, granularity)
    summary = executive.build(diagnosis, marketing, warnings)

    assert summary.headline.startswith("ROAS")
    # No materially worse final KPI is simplified away, and each says why it matters.
    assert [p.label for p in summary.problems] == [m.label for m in marketing.problems]
    assert all(p.why for p in summary.problems)
    for move in marketing.problems:
        assert move.label in " ".join(summary.narrative) or view.PLAIN_NAMES[move.metric] in summary.headline
    assert [c.title for c in summary.checks] == [i.title for i in marketing.top_investigations]
    # Next steps are conditional, come from the playbook, and are tested by a top-three check.
    actions = {a.cause: a.if_supported for a in marketing.actions}
    for step in summary.next_steps:
        assert step.action == actions[step.cause]
        assert f"If the evidence supports it, {step.action[0].lower()}{step.action[1:]}" in step.text
        assert step.text.startswith(next(c.statement for c in marketing.causes if c.title == step.cause))
        assert {title for _, title in step.checks} <= {c.title for c in summary.checks}
        assert "supports " + step.cause not in step.text
    assert len(summary.next_steps) <= executive.NEXT_STEPS
    # Python's own sentences never claim a cause.
    assert not CAUSAL.search(summary.headline)
    assert not any(CAUSAL.search(sentence) for sentence in summary.narrative if sentence not in marketing.patterns)


def test_falling_roas_names_the_biggest_drag():
    summary = executive.build(*scenario("CPM inflation")[:2])

    assert summary.headline == "ROAS fell 14.3%. The biggest drag was cost per 1,000 impressions (CPM up 18.3%)."
    assert summary.narrative[0].startswith("Cost per 1,000 impressions (CPM) rose 18.3%, feeding into cost per click (CPC up 17.1%)")
    assert not summary.problems[0].hidden


def test_stable_roas_with_an_offsetting_problem_is_flagged_as_hidden():
    summary = executive.build(*scenario("Masked problem")[:2])

    assert summary.headline.startswith("ROAS held steady (+2.9%), but cost per 1,000 impressions (CPM up 18.3%) worsened.")
    assert "Gains in average order value are hiding it." in summary.headline
    assert [p.hidden for p in summary.problems] == [True]
    assert any("which can hide the problem" in sentence for sentence in summary.narrative)


def test_rising_roas_with_hidden_problems_names_both_and_the_link_between_them():
    diagnosis, marketing, _, _ = scenario("Sep-Oct")
    summary = executive.build(diagnosis, marketing)

    assert summary.headline.startswith("ROAS rose 14.7%, but cost per 1,000 impressions (CPM up 44.4%) and ")
    assert "share of checkouts that end in a purchase (down 11.2%)" in summary.headline
    assert all(p.hidden for p in summary.problems) and len(summary.problems) == 2
    # CPC and CPA improved, so the CPM sentence must not say CPM pushed them up.
    assert summary.narrative[0] == "Cost per 1,000 impressions (CPM) rose 44.4%, and took 1.24 off ROAS."
    assert all(pattern in summary.narrative for pattern in marketing.patterns)
    assert "Overall, ROAS still rose, from 3.16 to 3.62, which can hide these problems." in summary.narrative


def test_healthy_control_says_nothing_worsened():
    summary = executive.build(*scenario("Healthy control")[:2])

    assert summary.headline == "ROAS held steady (+1.4%), and no KPI worsened by 5% or more."
    assert summary.problems == () and summary.next_steps == ()


@pytest.mark.parametrize("name", ["Landing page issue", "Checkout friction"])
def test_funnel_problems_put_the_tracking_step_among_the_next_steps(name):
    summary = executive.build(*scenario(name)[:2])

    assert "Tracking diagnostics" in [c.title for c in summary.checks]
    assert "Tracking problem" in [s.cause for s in summary.next_steps]


def test_could_mean_comes_from_the_playbook_links():
    diagnosis, marketing, _, _ = scenario("CTR decline")
    check = executive.build(diagnosis, marketing).checks[0]
    tests = next(i for i in marketing.top_investigations if i.title == check.title).tests

    for cause in tests:
        assert cause in check.could_mean
    assert "more likely" in check.could_mean and "less likely" in check.could_mean


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

    assert after.headline == before.headline and after.narrative == before.narrative
    assert after.problems == before.problems
    assert after.checks[0].title == "Account change history"
    assert after.checks[0].ai_note.startswith("It could show whether")
    assert after.next_steps[0].cause == "Budget scaling or bid strategy"


@pytest.mark.parametrize("name", ALL)
def test_page_and_report_show_the_same_executive_diagnosis(name):
    diagnosis, marketing, currency, warnings = scenario(name)
    summary = executive.build(diagnosis, marketing, warnings)
    page = render.executive_html(summary)
    html = report.build_report(report.ReportInput(
        diagnosis, build_evidence(diagnosis, currency), "File", "All campaigns", "a", "b", "c",
        date(2026, 11, 3), currency, warnings, marketing=marketing,
    ))
    executive_part = html[html.index('<section class="executive">'):html.index('<h2 class="part">Detailed analysis</h2>')]

    assert html.index('<section class="executive">') < html.index("1. A. Confirmed findings")
    pieces = [summary.headline, *summary.narrative, *summary.data_notes]
    pieces += [part for p in summary.problems for part in (p.change, p.effect, p.why) if part]
    pieces += [part for c in summary.checks for part in (c.title, c.what, c.could_mean, c.why_first) if part]
    pieces += [part for s in summary.next_steps for part in (s.text, s.cause)]
    for piece in pieces:
        assert escape(piece) in page and escape(piece) in executive_part, piece
    assert page.count("Hidden by overall ROAS") == executive_part.count("Hidden by overall ROAS")


def test_next_steps_read_as_plain_professional_english():
    step = executive.build(*scenario("CTR decline")[:2]).next_steps[0]

    assert step.cause == "Creative fatigue"
    assert step.text == (
        "People may have seen the same ads too often, so fewer of them clicked. "
        "Test it with “CTR for each ad” (priority 1) and “Frequency and reach over time” (priority 3). "
        "If the evidence supports it, refresh or rotate the most-seen creatives, and consider a frequency cap."
    )


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
        assert summary.source.startswith("Synthetic demonstration data") and "not real campaign results" in summary.source
    else:
        assert summary.source == "Your uploaded data: my_export.csv. Every figure here comes from this file only."
    assert escape(summary.source) in render.executive_html(summary) and escape(summary.source) in html
    other = "Your uploaded data" if synthetic else "Synthetic demonstration data"
    assert other not in render.executive_html(summary) and other not in html
