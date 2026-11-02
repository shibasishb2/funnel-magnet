"""Build the downloadable report: one HTML file that needs no server or network.

This module only presents engine and AI results and calculates no metrics, apart
from totalling the contributions so a reader can check them. All data text is escaped.
"""

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from funnel_magnet import __version__
from funnel_magnet.ai.evidence import EvidencePack
from funnel_magnet.diagnose import Diagnosis
from funnel_magnet.ui import view
from funnel_magnet.ui.render import STATUS_CLASSES, STATUS_ICONS, WITHHELD_LINK_WARNING, _items

STYLE_PATH = Path(__file__).with_name("report.css")

# The file may be opened anywhere, so block scripts and every outside request.
CONTENT_POLICY = "default-src 'none'; style-src 'unsafe-inline'"

METRIC_DEFINITIONS = (
    ("ROAS", "Purchase value ÷ spend"),
    ("AOV", "Purchase value ÷ purchases"),
    ("CPA", "Spend ÷ purchases"),
    ("CPC", "Spend ÷ link clicks"),
    ("CVR", "Purchases ÷ link clicks"),
    ("CPM", "Spend ÷ impressions × 1,000"),
    ("CTR", "Link clicks ÷ impressions"),
    ("Funnel stage rate", "Count at a stage ÷ count at the stage before it"),
)
METHODOLOGY = (
    "The diagnostic follows one decision tree: ROAS splits into AOV and CPA, CPA into CPC and "
    "CVR, CPC into CPM and CTR, and CVR into the conversion funnel stages.",
    "Every metric is recalculated from period totals. Click-based metrics use link clicks, and "
    "reported ratios are never averaged. A metric with a zero or missing denominator is undefined.",
    "Contributions use the logarithmic mean Divisia index (LMDI), so they sum exactly to the "
    "ROAS change.",
    "Python performs every calculation. Any AI content interprets those results and cites them "
    "by fact ID, and automated checks withhold the sections that fail.",
)
LIMITATIONS = (
    "The calculations show how ROAS changed arithmetically, not why it changed.",
    "Results depend on the Meta Ads export and its attribution. Recent periods can still change.",
    "Combined results can hide a shift in spend between campaigns.",
    "Automated AI checks use rules and word patterns, so they can miss an unusual unsupported "
    "claim. AI hypotheses are unconfirmed and need review by a person.",
    "This is an ESCP MBA prototype. Use synthetic or anonymized data only.",
)
NO_AI_TEXT = (
    "This report contains no AI interpretation, because none was generated or none passed the "
    "evidence checks for this comparison. Every figure in this report is calculated by Python "
    "and is unaffected."
)


@dataclass(frozen=True)
class ReportInput:
    diagnosis: Diagnosis
    evidence: EvidencePack
    source_name: str
    scope: str
    prior_period: str
    current_period: str
    data_range: str
    analysis_date: date
    currency: str | None = None
    load_warnings: tuple[str, ...] = ()
    ai_result: object | None = None


def file_name(diagnosis):
    return f"funnel-magnet-report-{diagnosis.current_label}.html"


def _tag(kind, text):
    return f'<span class="tag tag-{kind}">{escape(text)}</span>'


def _table(headers, rows, class_name=""):
    head = "".join(f'<th scope="col">{escape(item)}</th>' for item in headers)
    return (
        f'<div class="table-wrap"><table class="{class_name}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _metric_table(item, metrics):
    diagnosis, rows = item.diagnosis, []
    for metric in metrics:
        change = diagnosis.changes[metric]
        status = (
            f'<span aria-hidden="true">{STATUS_ICONS[change.status]}</span> '
            f"{view.STATUS_LABELS[change.status]}"
        )
        rows.append(
            f'<tr data-metric="{escape(metric)}">'
            f"<th scope=\"row\">{escape(view.metric_label(metric))}</th>"
            f"<td>{escape(view.format_value(metric, change.prior, item.currency))}</td>"
            f"<td>{escape(view.format_value(metric, change.current, item.currency))}</td>"
            f"<td>{escape(view.format_change(change.pct_change))}</td>"
            f'<td class="status-{STATUS_CLASSES[change.status]}">{status}</td></tr>'
        )
    headers = ("Metric", diagnosis.prior_label, diagnosis.current_label, "Change", "Status")
    return _table(headers, rows, "numbers")


def _details_html(item):
    diagnosis = item.diagnosis
    rows = (
        ("Analysis date", f"{item.analysis_date:%d %B %Y}"),
        ("Data source", item.source_name),
        ("Campaign or account", item.scope),
        ("Current period", item.current_period),
        ("Prior period", item.prior_period),
        ("Data available", item.data_range),
        ("Materiality threshold", view.format_threshold(diagnosis.threshold)),
        ("Currency", item.currency or "Not stated"),
        ("AI interpretation", _ai_status(item.ai_result)),
    )
    cells = "".join(f"<dt>{escape(name)}</dt><dd>{escape(value)}</dd>" for name, value in rows)
    return f'<dl class="details">{cells}</dl>'


def _ai_status(result):
    if result is None:
        return "Not included"
    model = f"{result.provider} {result.model}"
    if result.withheld:
        return f"Partly verified, some content withheld ({model})"
    return f"Passed the automated evidence checks ({model})"


def _withheld_alert(result):
    if result is None or not result.withheld:
        return ""
    return (
        '<div class="alert" role="alert"><strong>Partly verified AI content.</strong> Some AI '
        "sections failed the evidence checks and are withheld from this report. It includes only "
        "the accepted sections, and they are not validated conclusions.</div>"
    )


def _legend():
    return (
        '<p class="legend">'
        f"{_tag('confirmed', 'Confirmed Observations')} calculated from the data. "
        f"{_tag('driver', 'Mathematical Drivers')} how the arithmetic changed, not why. "
        f"{_tag('hypothesis', 'AI Hypotheses')} unconfirmed possible causes. "
        f"{_tag('recommendation', 'Recommendations')} AI-suggested checks for a person to review."
        "</p>"
    )


def _diagnosis_section(item):
    sentences = view.mathematical_diagnosis(item.diagnosis, item.currency)
    paths = "".join(
        f"<li>{escape(' → '.join(view.metric_label(node) for node in path))}</li>"
        for path in item.diagnosis.paths
    )
    return (
        '<section class="confirmed"><h2>1. Mathematical diagnosis</h2>'
        f"{_tag('confirmed', 'Calculated by Python')}"
        f"<p>{escape(' '.join(sentences))}</p>"
        + (f"<h3>Diagnostic path followed</h3><ul>{paths}</ul>" if paths else "")
        + "</section>"
    )


def _observations_section(item):
    findings = view.build_findings(item.diagnosis, item.currency)
    return (
        '<section class="confirmed"><h2>2. Confirmed Observations</h2>'
        f"{_tag('confirmed', 'Calculated from your data')}"
        f"{_metric_table(item, view.KPI_ORDER)}"
        f"<ul>{_items(findings.observations)}</ul></section>"
    )


def _contribution_table(diagnosis):
    rows = [
        f'<tr data-metric="{escape(driver.metric)}">'
        f'<th scope="row">{escape(row["Metric"])}</th><td>{escape(row["Change"])}</td>'
        f'<td>{escape(row["ROAS contribution"])}</td><td>{escape(row["Share of movement"])}</td>'
        f'<td>{escape(row["Role"])}</td></tr>'
        for driver, row in zip(diagnosis.drivers, view.driver_table(diagnosis))
    ]
    total = sum(driver.contribution for driver in diagnosis.drivers)
    rows.append(
        f'<tr class="total"><th scope="row">Total ROAS change</th><td></td><td>{total:+.3f}</td>'
        "<td></td><td></td></tr>"
    )
    return _table(("Metric", "Change", "ROAS contribution", "Share of movement", "Role"), rows, "numbers")


def _drivers_section(item):
    diagnosis = item.diagnosis
    findings = view.build_findings(diagnosis, item.currency)
    return (
        '<section class="driver"><h2>3. Mathematical Drivers</h2>'
        f"{_tag('driver', 'How the arithmetic changed, not why')}"
        f"<ul>{_items(findings.drivers)}</ul>"
        + (f"<h3>Contribution analysis</h3>{_contribution_table(diagnosis)}" if diagnosis.drivers else "")
        + f'<p class="note">{escape(diagnosis.driver_statement)}</p></section>'
    )


def _funnel_section(item):
    rates = view.rate_metrics(item.diagnosis)
    body = (
        _metric_table(item, rates)
        if len(rates) > 1
        else "<p>Funnel stages between link clicks and purchases are not available in this data.</p>"
    )
    return (
        '<section class="confirmed"><h2>4. Conversion funnel</h2>'
        f"{_tag('confirmed', 'Calculated from your data')}{body}</section>"
    )


def _citations(ids, evidence):
    known = evidence.by_id
    rows = "".join(
        f"<li><strong>{escape(fact)}</strong> {escape(known[fact].statement)}</li>" for fact in ids
    )
    return f'<div class="evidence"><div class="evidence-title">Evidence</div><ul>{rows}</ul></div>'


def _hypothesis_html(item, evidence):
    return (
        '<article class="card">'
        f'<div class="card-tag">Hypothesis {item.rank} · '
        f"{escape(item.confidence.capitalize())} confidence · Unconfirmed</div>"
        f"<p><strong>Mathematical evidence:</strong> {escape(item.mathematical_evidence)}</p>"
        f"<p><strong>Unverified business cause:</strong> {escape(item.unverified_business_cause)}</p>"
        f"<p><strong>Uncertainty:</strong> {escape(item.uncertainty)}</p>"
        f"{_citations(item.evidence_ids, evidence)}</article>"
    )


def _recommendation_html(item, evidence, shown_ranks):
    source = "Uploaded data" if item.check_source == "uploaded_data" else "External data"
    withheld = item.hypothesis_rank not in shown_ranks
    link = f"Tests hypothesis {item.hypothesis_rank}" + (" (withheld)" if withheld else "")
    warning = f'<p class="card-warning">{WITHHELD_LINK_WARNING}</p>' if withheld else ""
    return (
        '<article class="card">'
        f'<div class="card-tag">Priority {item.priority} · {source} · {link}</div>{warning}'
        f"<p><strong>{escape(item.investigation)}</strong></p>"
        f"<p><strong>What to check:</strong> {escape(item.what_to_check)}</p>"
        f"<p><strong>If supported:</strong> {escape(item.if_supported)}</p>"
        f"<p><strong>If not supported:</strong> {escape(item.if_not_supported)}</p>"
        f"<p><strong>Expected direction for {escape(view.metric_label(item.target_metric))}:</strong> "
        f"{escape(item.expected_direction.capitalize())}. {escape(item.expected_impact)}</p>"
        f"{_citations(item.evidence_ids, evidence)}</article>"
    )


def _withheld_html(result):
    if not result.withheld:
        return ""
    return (
        '<div class="alert"><strong>Withheld content.</strong> These problems were found, and the '
        f"affected text is not shown.<ul>{_items(result.withheld)}</ul></div>"
    )


def _hypotheses_html(result, evidence):
    cards = "".join(_hypothesis_html(item, evidence) for item in result.diagnosis.hypotheses)
    if cards:
        return cards
    if result.withheld:
        return "<p>No AI hypothesis passed the evidence checks.</p>"
    return "<p>This comparison needed no hypothesis.</p>"


def _recommendations_html(result, evidence):
    diagnosis = result.diagnosis
    shown_ranks = {item.rank for item in diagnosis.hypotheses}
    cards = "".join(
        _recommendation_html(item, evidence, shown_ranks) for item in diagnosis.recommendations
    )
    return cards or "<p>This report includes no AI recommendations.</p>"


def _ai_sections(item):
    result = item.ai_result
    if result is None:
        return (
            '<section class="hypothesis"><h2>5. AI Hypotheses and Recommendations</h2>'
            f"{_tag('hypothesis', 'Not included')}<p>{NO_AI_TEXT}</p></section>"
        )

    diagnosis = result.diagnosis
    explanation = diagnosis.confidence_explanation
    return (
        '<section class="hypothesis"><h2>5. AI Hypotheses</h2>'
        f"{_tag('hypothesis', 'AI-generated · Causes not confirmed')}"
        f'<p class="note">Generated by {escape(result.provider)} {escape(result.model)}.</p>'
        f"{_withheld_html(result)}{_hypotheses_html(result, item.evidence)}"
        + (f"<h3>Confidence and uncertainty</h3><p>{escape(explanation)}</p>" if explanation else "")
        + "</section>"
        '<section class="recommendation"><h2>6. Recommendations</h2>'
        f"{_tag('recommendation', 'AI-generated · For a person to review')}"
        f"{_recommendations_html(result, item.evidence)}"
        + (
            f'<h3>Adjusted by the app</h3><ul class="note">{_items(result.notes)}</ul>'
            if result.notes
            else ""
        )
        + "</section>"
    )


def _needs_html(item):
    needs = [escape(text) for text in item.diagnosis.data_needed]
    if item.ai_result is not None:
        needs += [
            f"{escape(need.information)} {escape(need.reason)} "
            f"<span class=\"note\">(AI-suggested. Evidence: {escape(', '.join(need.evidence_ids))})</span>"
            for need in item.ai_result.diagnosis.additional_information
        ]
    if not needs:
        return "<p>No further information is requested for this comparison.</p>"
    return "<ul>" + "".join(f"<li>{need}</li>" for need in needs) + "</ul>"


def _quality_section(item, number):
    messages = [*item.load_warnings, *item.diagnosis.warnings]
    groups = "".join(
        f"<h3>{escape(group)}</h3><ul>{_items(rows)}</ul>"
        for group, rows in view.group_warnings(messages).items()
    )
    return (
        f"<section><h2>{number}. Data quality and missing information</h2>"
        f"{groups or '<p>No data quality warnings for this comparison.</p>'}"
        f"<h3>Information needed before naming a cause</h3>{_needs_html(item)}</section>"
    )


def _method_section(number):
    rows = [
        f'<tr><th scope="row">{escape(name)}</th><td>{escape(formula)}</td></tr>'
        for name, formula in METRIC_DEFINITIONS
    ]
    return (
        f"<section><h2>{number}. Methodology and limitations</h2>"
        f"<h3>Methodology</h3><ul>{_items(METHODOLOGY)}</ul>"
        f"<h3>Metric definitions</h3>{_table(('Metric', 'Definition'), rows)}"
        f"<h3>Limitations</h3><ul>{_items(LIMITATIONS)}</ul></section>"
    )


def build_report(item):
    """Return the complete report as one HTML string."""
    first_free = 6 if item.ai_result is None else 7
    title = f"Funnel Magnet Diagnostic Report: {item.diagnosis.current_label}"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_POLICY}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE_PATH.read_text(encoding='utf-8')}</style>"
        '</head><body><main class="report">'
        '<header><div class="brand">Funnel Magnet</div>'
        '<div class="tagline">Marketing Performance Diagnostic Agent</div>'
        "<h1>Meta Ads Diagnostic Report</h1></header>"
        f"{_details_html(item)}"
        f'<p class="notice">{escape(view.DECISION_SUPPORT_NOTICE)}</p>'
        f"{_withheld_alert(item.ai_result)}{_legend()}"
        f"{_diagnosis_section(item)}{_observations_section(item)}{_drivers_section(item)}"
        f"{_funnel_section(item)}{_ai_sections(item)}"
        f"{_quality_section(item, first_free)}{_method_section(first_free + 1)}"
        f'<footer>Funnel Magnet version {__version__}. ESCP MBA prototype. '
        "Python calculated the figures in this report.</footer>"
        "</main></body></html>\n"
    )
