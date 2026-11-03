"""Build the downloadable report: one HTML file that needs no server or network.

This module only presents engine and AI results and calculates no metrics, apart
from totalling the contributions so a reader can check them. All data text is escaped.
"""

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from funnel_magnet import __version__, playbook
from funnel_magnet.ai.evidence import EvidencePack
from funnel_magnet.diagnose import Diagnosis
from funnel_magnet.ui import view
from funnel_magnet.ui.render import STATUS_CLASSES, STATUS_ICONS, _items

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
    "Possible causes, checks and actions come from Funnel Magnet's marketing playbook, matched to "
    "the KPIs that worsened materially. They are possibilities to test, not findings.",
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
    "This report contains no AI interpretation. Sections A to D come from Python's calculations "
    "and the marketing playbook, and are complete without it."
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
    # The dashboard passes the diagnosis it displayed, so the report matches it exactly.
    marketing: playbook.MarketingDiagnosis | None = None

    @property
    def sections(self):
        return self.marketing or playbook.build(self.diagnosis, self.currency)


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
        ("AI prioritisation", _ai_status(item.marketing)),
    )
    cells = "".join(f"<dt>{escape(name)}</dt><dd>{escape(value)}</dd>" for name, value in rows)
    return f'<dl class="details">{cells}</dl>'


def _ai_status(marketing):
    if marketing is None or marketing.ai is None:
        return "Not included"
    ai = marketing.ai
    partly = ", some content withheld" if ai.withheld else ""
    return f"Prioritised by {ai.provider} {ai.model}{partly}"
    model = f"{result.provider} {result.model}"
    if result.withheld:
        return f"Partly verified, some content withheld ({model})"
    return f"Passed the automated evidence checks ({model})"


def _ai_alert(marketing):
    if marketing is None or marketing.ai is None or not marketing.ai.withheld:
        return ""
    return (
        '<div class="alert" role="alert"><strong>Some AI content was withheld.</strong> Parts of the AI '
        "prioritisation failed the checks and are left out. Section 5 lists why.</div>"
    )


def _legend():
    return (
        '<p class="legend">'
        f"{_tag('confirmed', 'A. Confirmed findings')} calculated from the data. "
        f"{_tag('hypothesis', 'B. Possible causes')} from the playbook, not confirmed. "
        f"{_tag('recommendation', 'C. What to investigate')} and "
        f"{_tag('recommendation', 'D. Potential actions')} depend on what the checks find. "
        f"{_tag('driver', 'AI')} content is labelled separately."
        "</p>"
    )


def _move_html(move):
    extra = [f"Path: {move.path}"]
    if move.contribution:
        extra.append(f"Effect on ROAS: {move.contribution}" + (f" ({move.effect.lower()})" if move.effect else ""))
    return f'<li><strong>{escape(move.change)}</strong> <span class="note">{escape(". ".join(extra))}.</span></li>'


def _contribution_table(diagnosis):
    rows = [
        f'<tr data-metric="{escape(driver.metric)}">'
        f'<th scope="row">{escape(row["Metric"])}</th><td>{escape(row["Change"])}</td>'
        f'<td>{escape(row["ROAS contribution"])}</td><td>{escape(row["Share of movement"])}</td>'
        f'<td>{escape(row["Effect on ROAS"])}</td></tr>'
        for driver, row in zip(diagnosis.drivers, view.driver_table(diagnosis))
    ]
    total = sum(driver.contribution for driver in diagnosis.drivers)
    rows.append(
        f'<tr class="total"><th scope="row">Total ROAS change</th><td></td><td>{total:+.3f}</td>'
        "<td></td><td></td></tr>"
    )
    return _table(("Metric", "Change", "ROAS contribution", "Share of movement", "Effect on ROAS"), rows, "numbers")


def _findings_section(item):
    diagnosis, marketing = item.diagnosis, item.sections
    problems = "".join(_move_html(move) for move in marketing.problems)
    beneficial = "".join(_move_html(move) for move in marketing.beneficial)
    paths = "".join(
        f"<li>{escape(' → '.join(view.metric_label(node) for node in path))}</li>"
        for path in diagnosis.paths
    )
    rates = view.rate_metrics(diagnosis)
    findings = view.build_findings(diagnosis, item.currency)
    return (
        '<section class="confirmed"><h2>1. A. Confirmed findings</h2>'
        f"{_tag('confirmed', 'Calculated by Python')}"
        f"<p>{escape(' '.join(marketing.summary))}</p>"
        "<h3>KPIs that worsened materially</h3>"
        + (f"<ul>{problems}</ul>" if problems else "<p>No final KPI worsened by the threshold or more.</p>")
        + "<h3>KPIs that improved materially</h3>"
        + (f"<ul>{beneficial}</ul>" if beneficial else "<p>No final KPI improved by the threshold or more.</p>")
        + '<p class="note">Improvements are reported as calculated, with no causes suggested.</p>'
        + (f"<h3>Diagnostic path followed</h3><ul>{paths}</ul>" if paths else "")
        + f"<h3>KPI comparison</h3>{_metric_table(item, view.KPI_ORDER)}"
        + f"<ul>{_items(findings.observations)}</ul>"
        + f"<h3>Mathematical drivers</h3><ul>{_items(findings.drivers)}</ul>"
        + (f"{_contribution_table(diagnosis)}" if diagnosis.drivers else "")
        + f'<p class="note">{escape(diagnosis.driver_statement)}</p>'
        + "<h3>Conversion funnel</h3>"
        + (
            _metric_table(item, rates)
            if len(rates) > 1
            else "<p>Funnel stages between link clicks and purchases are not available in this data.</p>"
        )
        + "</section>"
    )


def _ai_note(label, text):
    return f'<p class="ai-note"><strong>{label}:</strong> {escape(text)}</p>' if text else ""


def _cause_card(cause):
    return (
        '<article class="card">'
        f'<div class="card-tag">{escape(cause.title)} · Could explain {escape(", ".join(cause.explains))}</div>'
        f"<p>{escape(cause.statement)}</p>{_ai_note('Why the AI ranks it here', cause.ai_note)}</article>"
    )


def _where(check):
    return "In your upload" if check.available else "Collect outside this app"


def _causes_section(item):
    marketing = item.sections
    notes = "".join(f'<div class="alert pattern">{escape(note)}</div>' for note in (*marketing.patterns, *marketing.component_notes))
    body = notes
    if marketing.shared_causes:
        body += (
            "<h3>Possible shared explanations</h3>"
            '<p class="note">Each could explain more than one problem. None is assumed.</p>'
            + "".join(_cause_card(cause) for cause in marketing.shared_causes)
        )
    if marketing.separate_causes:
        body += f"<h3>{'Separate possible causes' if marketing.shared_causes else 'Possible causes'}</h3>"
        body += "".join(_cause_card(cause) for cause in marketing.separate_causes)
    body = body or "<p>No KPI worsened materially, so the playbook suggests no causes.</p>"
    return (
        '<section class="hypothesis"><h2>2. B. Possible causes</h2>'
        f"{_tag('hypothesis', 'From the marketing playbook · Not confirmed')}"
        '<p class="note">Each is a possibility to test. The data shows what changed, not why.</p>'
        f"{body}</section>"
    )


def _check_html(check):
    return (
        f"<li><strong>{escape(check.title)}</strong> · {_where(check)}. {escape(check.detail)} "
        f'<span class="note">Helps test: {escape(", ".join(check.tests))}.</span>'
        f"{_ai_note('AI', check.ai_note)}</li>"
    )


def _investigations_section(item):
    marketing = item.sections
    if not marketing.investigations:
        body = "<p>Nothing needs investigating for this comparison.</p>"
    else:
        body = "<h3>Top three investigations</h3>" + "".join(
            '<article class="card priority">'
            f'<div class="card-tag">Priority {rank} · {_where(check)}</div>'
            f"<p><strong>{escape(check.title)}.</strong> {escape(check.detail)}</p>"
            + (f"<p><strong>Why first:</strong> {escape(check.reason)}</p>" if check.reason else "")
            + _ai_note("Why the AI ranks it here", check.ai_note)
            + f'<p class="note">Helps test: {escape(", ".join(check.tests))}.</p></article>'
            for rank, check in enumerate(marketing.top_investigations, 1)
        )
        if marketing.other_investigations:
            body += "<h3>All other checks</h3><ul>" + "".join(_check_html(c) for c in marketing.other_investigations) + "</ul>"
    return (
        '<section class="recommendation"><h2>3. C. What to investigate</h2>'
        f"{_tag('recommendation', 'Top three first')}{body}</section>"
    )


def _actions_section(item):
    cards = "".join(
        '<article class="card">'
        f'<div class="card-tag">{escape(action.cause)}</div>'
        f"<p><strong>If the evidence supports this cause:</strong> {escape(action.if_supported)}</p>"
        f"<p><strong>If it doesn't:</strong> {escape(action.if_not_supported)}</p>"
        f"{_ai_note('AI guidance, conditional', action.ai_note)}</article>"
        for action in item.sections.actions
    )
    return (
        '<section class="recommendation"><h2>4. D. Potential actions</h2>'
        f"{_tag('recommendation', 'Only after the checks above')}"
        '<p class="note">Nothing here is confirmed. Act only on what the investigation supports.</p>'
        f"{cards or '<p>No actions are suggested for this comparison.</p>'}</section>"
    )


def _ai_priorities_section(ai):
    relationship = (
        f"<p><strong>How the problems might relate (AI, unconfirmed):</strong> {escape(ai.relationship)}</p>"
        if ai.relationship
        else ""
    )
    withheld = (
        f"<h3>Withheld because they failed the checks</h3><ul>{_items(ai.withheld)}</ul>" if ai.withheld else ""
    )
    return (
        '<section class="driver"><h2>5. AI prioritisation</h2>'
        f"{_tag('driver', 'AI-generated · Order and explanations only')}"
        f'<p class="note">Prioritised by {escape(ai.provider)} {escape(ai.model)}. The AI reordered and explained '
        "the playbook items in sections B to D and added no findings. Every cause remains unconfirmed.</p>"
        f"{relationship}{withheld}</section>"
    )


def _ai_sections(item):
    if item.marketing is not None and item.marketing.ai is not None:
        return _ai_priorities_section(item.marketing.ai)
    return (
        '<section class="driver"><h2>5. AI prioritisation</h2>'
        f"{_tag('driver', 'Not included')}<p>{NO_AI_TEXT}</p></section>"
    )


def _quality_section(item, number):
    messages = [*item.load_warnings, *item.diagnosis.warnings]
    groups = "".join(
        f"<h3>{escape(group)}</h3><ul>{_items(rows)}</ul>"
        for group, rows in view.group_warnings(messages).items()
    )
    return (
        f"<section><h2>{number}. Data quality</h2>"
        f"{groups or '<p>No data quality warnings for this comparison.</p>'}</section>"
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
    first_free = 6
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
        f"{_ai_alert(item.marketing)}{_legend()}"
        f"{_findings_section(item)}{_causes_section(item)}{_investigations_section(item)}"
        f"{_actions_section(item)}{_ai_sections(item)}"
        f"{_quality_section(item, first_free)}{_method_section(first_free + 1)}"
        f'<footer>Funnel Magnet version {__version__}. ESCP MBA prototype. '
        "Python calculated the figures in this report.</footer>"
        "</main></body></html>\n"
    )
