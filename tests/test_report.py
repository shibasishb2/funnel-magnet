"""Report tests. AI responses are mocked, so no key or network is used."""

import json
import re
from datetime import date
from html import escape, unescape

import pytest

from funnel_magnet import playbook
from funnel_magnet.ai import prioritise
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import ProviderResponse
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
from funnel_magnet.metrics import Totals, compute_metrics
from funnel_magnet.periods import MONTHLY, WEEKLY, aggregate, latest_two_complete
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import report, view

SAMPLES = view.load_samples()
FAKE_KEY = "AIzaFAKE-test-only-key"


class FakeProvider:
    name = "Fake"
    model = "fake-model"

    def __init__(self, text):
        self.text = text

    def generate(self, system_prompt, user_prompt, schema):
        return ProviderResponse(self.text, 10, 20)


def ai_reply(rationale="Auction competition may have raised delivery costs."):
    """A valid AI prioritisation for the CPM inflation scenario."""
    return json.dumps({
        "cause_priorities": [{"cause_id": "auction_competition", "rationale": rationale}],
        "check_priorities": [{"check_id": "account_change_log", "rationale": "It could rule self-made changes in or out."}],
        "relationship": "",
        "action_guidance": [{"cause_id": "auction_competition", "guidance": "If benchmarks show a market-wide rise, review bids."}],
    })


def report_input(index=0, granularity=WEEKLY, ai_text=None, source_name="Sample", scope="All campaigns"):
    loaded = load_meta_csv(SAMPLES[index].path)
    diagnosis = run_diagnosis(loaded.data, granularity)
    warnings = tuple(issue.message for issue in loaded.warnings)
    evidence = build_evidence(diagnosis, loaded.currency, warnings)
    marketing = None
    if ai_text:
        marketing = prioritise.prioritise(playbook.build(diagnosis, loaded.currency), evidence, FakeProvider(ai_text))
    return report.ReportInput(
        diagnosis=diagnosis,
        evidence=evidence,
        source_name=source_name,
        scope=scope,
        prior_period="2026-W34 (17 Aug to 23 Aug 2026)",
        current_period="2026-W35 (24 Aug to 30 Aug 2026)",
        data_range="01 Jun 2026 to 31 Aug 2026",
        analysis_date=date(2026, 11, 2),
        currency=loaded.currency,
        load_warnings=warnings,
        marketing=marketing,
    )


def row_cells(html, metric):
    row = re.search(rf'<tr data-metric="{metric}">(.*?)</tr>', html, re.DOTALL).group(1)
    return [unescape(re.sub(r"<[^>]+>", "", cell)) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)]


# Every scenario


@pytest.mark.parametrize("index", range(len(SAMPLES)), ids=[item.name for item in SAMPLES])
@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
def test_kpis_match_the_engine_in_every_scenario(index, granularity):
    item = report_input(index, granularity)
    html = report.build_report(item)

    for metric in view.KPI_ORDER:
        change = item.diagnosis.changes[metric]
        cells = row_cells(html, metric)
        assert cells[1] == view.format_value(metric, change.prior, item.currency)
        assert cells[2] == view.format_value(metric, change.current, item.currency)
        assert cells[3] == view.format_change(change.pct_change)
        assert view.STATUS_LABELS[change.status] in cells[4]


@pytest.mark.parametrize("index", range(len(SAMPLES)), ids=[item.name for item in SAMPLES])
def test_funnel_and_contributions_match_the_engine(index):
    item = report_input(index)
    html = report.build_report(item)

    assert escape(" ".join(view.mathematical_diagnosis(item.diagnosis, item.currency))) in html
    rows = re.findall(r'<tr data-metric="([^"]+)">(.*?)</tr>', html, re.DOTALL)
    for driver in item.diagnosis.drivers:
        contribution = f"<td>{driver.contribution:+.3f}</td><td>{driver.weight:.0%}</td>"
        assert any(metric == driver.metric and contribution in row for metric, row in rows)
    if len(view.rate_metrics(item.diagnosis)) > 1:
        for metric in view.rate_metrics(item.diagnosis):
            change = item.diagnosis.changes[metric]
            value = f"<td>{escape(view.format_value(metric, change.current, item.currency))}</td>"
            assert any(name == metric and value in row for name, row in rows)


@pytest.mark.parametrize("index", range(len(SAMPLES)), ids=[item.name for item in SAMPLES])
def test_contribution_total_equals_the_roas_change(index):
    item = report_input(index)
    roas = item.diagnosis.changes["roas"]
    expected = roas.current - roas.prior

    assert re.search(r'Total ROAS change</th><td></td><td>([+-][\d.]+)<', report.build_report(item)).group(1) == (
        f"{expected:+.3f}"
    )


def test_report_figures_match_a_recalculation_from_the_data():
    loaded = load_meta_csv(SAMPLES[0].path)
    prior, current = latest_two_complete(loaded.data, WEEKLY)
    expected = compute_metrics(aggregate(loaded.data, current))
    html = report.build_report(report_input())

    assert row_cells(html, "roas")[2] == f"{expected['roas']:.2f}"
    assert row_cells(html, "ctr")[2] == f"{expected['ctr']:.2%}"
    assert row_cells(html, "cpm")[2] == f"€{expected['cpm']:,.2f}"


@pytest.mark.parametrize("index", range(len(SAMPLES)), ids=[item.name for item in SAMPLES])
def test_every_required_section_is_present(index):
    html = report.build_report(report_input(index))

    for text in (
        "Funnel Magnet",
        "Meta Ads Diagnostic Report",
        "Analysis date",
        "02 November 2026",
        "Campaign or account",
        "1. A. Confirmed findings",
        "2. B. Possible causes",
        "3. C. What to investigate",
        "4. D. Potential actions",
        "KPI comparison",
        "Mathematical drivers",
        "Conversion funnel",
        "AI interpretation",
        "Data quality",
        "Methodology and limitations",
        "Metric definitions",
        "Spend ÷ impressions × 1,000",
    ):
        assert text in html


def test_warnings_and_investigations_are_included():
    item = report_input()
    html = report.build_report(item)

    # Warnings appear in plain English with the same substance.
    for text in item.diagnosis.warnings:
        assert escape(view.plain_warning(text)) in html
    for check in item.sections.investigations:
        assert escape(check.detail) in html


# AI cases


def test_no_ai_report_is_complete_and_factual():
    html = report.build_report(report_input())

    assert "contains no AI interpretation" in html
    assert "Not included" in html
    assert "Mathematical drivers" in html
    assert "6. Data quality" in html and "7. Methodology" in html
    assert "Partly verified" not in html


# Undefined results


def test_zero_spend_report_is_complete_and_says_ratios_are_undefined():
    empty = Totals(0, 0, 0, 0, 0)
    active = Totals(100, 10000, 200, 10, 500)
    diagnosis = diagnose_totals(empty, active)
    diagnosis.prior_label, diagnosis.current_label = "2026-W01", "2026-W02"
    item = report.ReportInput(
        diagnosis, build_evidence(diagnosis, "EUR"), "File", "All campaigns", "a", "b", "c", date(2026, 11, 2), "EUR"
    )

    html = report.build_report(item)

    assert row_cells(html, "roas")[1] == view.UNDEFINED
    assert "can&#x27;t be compared" in html
    assert "Funnel stages between link clicks and purchases are not available" in html


# Safety


def test_warning_text_is_escaped():
    item = report_input()
    html = report.build_report(
        report.ReportInput(**{**item.__dict__, "load_warnings": ("<b>bad</b>",)})
    )

    assert "<b>bad</b>" not in html


def test_file_name_uses_only_the_period_label():
    assert report.file_name(report_input().diagnosis) == "funnel-magnet-report-2026-W35.html"


# Safety and AI content


@pytest.mark.parametrize("reply", [None, ai_reply()], ids=["none", "ai"])
def test_every_report_says_it_is_decision_support(reply):
    assert view.DECISION_SUPPORT_NOTICE in report.build_report(report_input(ai_text=reply))


def test_report_shows_ai_priorities_with_their_labels():
    html = report.build_report(report_input(ai_text=ai_reply()))

    assert "5. AI prioritisation" in html and "Prioritised by Fake fake-model" in html
    assert "Auction competition may have raised delivery costs." in html
    assert "AI guidance, conditional" in html
    assert "contains no AI interpretation" not in html


def test_withheld_ai_content_is_disclosed_at_the_top():
    html = report.build_report(report_input(ai_text=ai_reply("Auction competition caused the rise in CPM.")))

    assert html.index("Some AI explanations were left out.") < html.index("1. A. Confirmed findings")
    assert "1 AI explanation didn&#x27;t pass the accuracy checks" in html and "<h3>Technical details</h3>" in html
    assert "Auction competition caused the rise" not in html


def test_uploaded_and_ai_text_is_escaped():
    attack = '<script>alert("x")</script>'
    item = report_input(ai_text=ai_reply(f"Competition may matter {attack}"), source_name=attack, scope=f'"><img src=x>{attack}')

    html = report.build_report(item)

    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html


def test_warning_text_is_escaped():
    item = report_input()
    html = report.build_report(
        report.ReportInput(**{**item.__dict__, "load_warnings": ("<b>bad</b>",)})
    )

    assert "<b>bad</b>" not in html


@pytest.mark.parametrize("reply", [None, ai_reply()], ids=["none", "ai"])
def test_report_is_self_contained(reply):
    html = report.build_report(report_input(ai_text=reply))

    assert html.startswith("<!doctype html>")
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert "@import" not in html
    assert "url(" not in html
    assert not re.search(r"\b(?:src|href)=", html)
    assert "http://" not in html and "https://" not in html
    assert "default-src &#x27;none&#x27;" in html or "default-src 'none'" in html


def test_keys_are_never_in_a_report(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    html = report.build_report(report_input(ai_text=ai_reply()))

    assert FAKE_KEY not in html
    assert "GEMINI_API_KEY" not in html and "GROQ_API_KEY" not in html
