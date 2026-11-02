"""Report tests. AI responses are mocked, so no key or network is used."""

import json
import re
from datetime import date
from html import escape, unescape
from pathlib import Path

import pytest

from funnel_magnet.ai import service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import ProviderResponse
from funnel_magnet.diagnose import diagnose_totals, run_diagnosis
from funnel_magnet.metrics import Totals, compute_metrics
from funnel_magnet.periods import MONTHLY, WEEKLY, aggregate, latest_two_complete
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import report, view

SAMPLES = view.load_samples()
FIXTURE = Path(__file__).parent / "fixtures" / "live_response_final_cpm_inflation.json"
FAKE_KEY = "AIzaFAKE-test-only-key"


class FakeProvider:
    name = "Fake"
    model = "fake-model"

    def __init__(self, text):
        self.text = text

    def generate(self, system_prompt, user_prompt, schema):
        return ProviderResponse(self.text, 10, 20)


def report_input(index=0, granularity=WEEKLY, ai_text=None, source_name="Sample", scope="All campaigns"):
    loaded = load_meta_csv(SAMPLES[index].path)
    diagnosis = run_diagnosis(loaded.data, granularity)
    warnings = tuple(issue.message for issue in loaded.warnings)
    evidence = build_evidence(diagnosis, loaded.currency, warnings)
    ai_result = service.generate_diagnosis(evidence, FakeProvider(ai_text)) if ai_text else None
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
        ai_result=ai_result,
    )


def full_reply():
    reply = json.loads(FIXTURE.read_text("utf-8"))
    reply["hypotheses"][0]["unverified_business_cause"] = "CPM may have risen with market competition."
    reply["hypotheses"][1]["unverified_business_cause"] = "Spend may have moved toward costlier campaigns."
    reply["confidence_explanation"] = "Confidence is low because campaign results have not been checked yet."
    return json.dumps(reply)


def partial_reply():
    return FIXTURE.read_text("utf-8")


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
        "Mathematical diagnosis",
        "Confirmed Observations",
        "Mathematical Drivers",
        "Conversion funnel",
        "AI Hypotheses",
        "Data quality and missing information",
        "Methodology and limitations",
        "Metric definitions",
        "Spend ÷ impressions × 1,000",
    ):
        assert text in html


def test_warnings_and_information_needs_are_included():
    item = report_input()
    html = report.build_report(item)

    for text in [*item.diagnosis.warnings, *item.diagnosis.data_needed]:
        assert escape(view.humanize(text)) in html


# AI cases


def test_no_ai_report_is_complete_and_factual():
    html = report.build_report(report_input())

    assert "contains no AI interpretation" in html
    assert "Not included" in html
    assert "Mathematical Drivers" in html
    assert "6. Data quality" in html and "7. Methodology" in html
    assert "Partly verified" not in html


def test_full_ai_report_shows_every_section_with_evidence_and_model():
    item = report_input(ai_text=full_reply())
    html = report.build_report(item)

    assert item.ai_result.withheld == []
    assert "Partly verified" not in html
    assert "Fake fake-model" in html
    assert "Hypothesis 1" in html and "Hypothesis 2" in html
    assert "CPM by campaign for the current and prior period." in html
    assert '<div class="evidence-title">Evidence</div>' in html
    assert "CPM contributed -0.64" in html
    assert "(withheld)" not in html
    assert "7. Data quality" in html and "8. Methodology" in html


def test_partial_ai_report_discloses_and_labels_withheld_content():
    html = report.build_report(report_input(ai_text=partial_reply()))

    assert html.index("Partly verified AI content") < html.index("1. Mathematical diagnosis")
    assert "Partly verified, some content withheld" in html
    assert "Hypothesis 1 combines more than one business cause." in html
    assert "Tests hypothesis 1 (withheld)" in html and "Tests hypothesis 2 (withheld)" in html
    assert html.count("not a validated conclusion") == 2
    assert "No AI hypothesis passed the evidence checks." in html


def test_partial_ai_report_leaves_out_rejected_text():
    html = report.build_report(report_input(ai_text=partial_reply()))

    for text in ("heightened market competition", "expensive audiences", "campaign-level splits"):
        assert text not in html


def test_ai_information_needs_are_labelled_as_ai_suggested():
    html = report.build_report(report_input(ai_text=partial_reply()))

    assert "AI-suggested. Evidence: G1" in html


def test_four_kinds_of_content_have_distinct_labels():
    html = report.build_report(report_input(ai_text=full_reply()))

    for kind in ("confirmed", "driver", "hypothesis", "recommendation"):
        assert f'class="tag tag-{kind}"' in html
        assert f'<section class="{kind}">' in html


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


def test_uploaded_and_ai_text_is_escaped():
    attack = '<script>alert("x")</script>'
    reply = json.loads(full_reply())
    reply["hypotheses"][0]["uncertainty"] = attack
    reply["recommendations"][0]["what_to_check"] = attack
    reply["additional_information"][0]["reason"] = attack
    item = report_input(ai_text=json.dumps(reply), source_name=attack, scope=f'"><img src=x>{attack}')

    html = report.build_report(item)

    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html


def test_warning_text_is_escaped():
    item = report_input()
    html = report.build_report(
        report.ReportInput(**{**item.__dict__, "load_warnings": ("<b>bad</b>",)})
    )

    assert "<b>bad</b>" not in html


@pytest.mark.parametrize("reply", [None, full_reply(), partial_reply()], ids=["none", "full", "partial"])
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
    html = report.build_report(report_input(ai_text=full_reply()))

    assert FAKE_KEY not in html
    assert "GEMINI_API_KEY" not in html
    assert "AIza" not in html


def test_file_name_uses_only_the_period_label():
    assert report.file_name(report_input().diagnosis) == "funnel-magnet-report-2026-W35.html"
