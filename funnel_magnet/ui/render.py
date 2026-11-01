"""Build the dashboard's HTML fragments. All data text is escaped."""

from html import escape
from pathlib import Path

from funnel_magnet.diagnose import Status
from funnel_magnet.ui.view import STATUS_LABELS, metric_label

STYLE_PATH = Path(__file__).with_name("style.css")

STATUS_CLASSES = {
    Status.IMPROVED: "better",
    Status.DETERIORATED: "worse",
    Status.STABLE: "stable",
    Status.UNDEFINED: "undefined",
}
STATUS_ICONS = {
    Status.IMPROVED: "✓",
    Status.DETERIORATED: "!",
    Status.STABLE: "–",
    Status.UNDEFINED: "?",
}


def style_tag():
    return f"<style>{STYLE_PATH.read_text(encoding='utf-8')}</style>"


def header_html():
    return (
        '<div class="fm-header">'
        '<div class="fm-brand">Funnel Magnet</div>'
        '<div class="fm-tagline">Marketing Performance Diagnostic Agent</div>'
        "</div>"
    )


def _pill(change, status):
    return (
        f'<span class="fm-pill fm-{STATUS_CLASSES[status]}">'
        f'<span aria-hidden="true">{STATUS_ICONS[status]}</span> '
        f"{escape(change)} · {STATUS_LABELS[status]}</span>"
    )


def kpis_html(cards, prior_label, current_label):
    tiles = "".join(
        '<div class="fm-kpi">'
        f'<div class="fm-kpi-label">{escape(card.label)}</div>'
        f'<div class="fm-kpi-value">{escape(card.current)}</div>'
        f'<div class="fm-kpi-prior">{escape(prior_label)}: {escape(card.prior)}</div>'
        f"{_pill(card.change, card.status)}"
        "</div>"
        for card in cards
    )
    return (
        f'<div class="fm-kpi-note">Large figures show {escape(current_label)}.</div>'
        f'<div class="fm-kpis">{tiles}</div>'
    )


def _node_html(node):
    classes = "fm-node fm-on-path" if node.on_path else "fm-node"
    badge = f'<span class="fm-badge">{escape(node.badge)}</span>' if node.badge else ""
    box = (
        f'<div class="{classes}">'
        f'<div class="fm-node-top"><span class="fm-node-label">{escape(node.label)}</span>{badge}</div>'
        f'<div class="fm-node-values">{escape(node.prior)} → {escape(node.current)}</div>'
        f"{_pill(node.change, node.status)}"
        "</div>"
    )
    if not node.children:
        return f'<div class="fm-tree-item">{box}</div>'

    children = "".join(
        f'<div class="fm-kid{" fm-kid-on" if child.on_path else ""}">{_node_html(child)}</div>'
        for child in node.children
    )
    return f'<div class="fm-tree-item">{box}<div class="fm-kids">{children}</div></div>'


def tree_html(root):
    return f'<div class="fm-tree-scroll"><div class="fm-tree">{_node_html(root)}</div></div>'


def _items(lines):
    return "".join(f"<li>{escape(line)}</li>" for line in lines)


def _citations(ids, evidence):
    known = evidence.by_id
    items = "".join(
        f"<li><strong>{escape(item)}</strong> {escape(known[item].statement)}</li>" for item in ids
    )
    return f'<details class="fm-evidence"><summary>Evidence: {escape(", ".join(ids))}</summary><ul>{items}</ul></details>'


def _hypothesis_html(item, evidence):
    return (
        '<article class="fm-ai-card">'
        f'<div class="fm-ai-card-tag">Hypothesis {item.rank} · {escape(item.confidence.capitalize())} confidence</div>'
        f"<p><strong>Mathematical evidence:</strong> {escape(item.mathematical_evidence)}</p>"
        f"<p><strong>Unverified business cause:</strong> {escape(item.unverified_business_cause)}</p>"
        f'<p class="fm-panel-note">Uncertainty: {escape(item.uncertainty)}</p>'
        f"{_citations(item.evidence_ids, evidence)}"
        "</article>"
    )


def _recommendation_html(item, evidence):
    direction = {
        "increase": "Increase",
        "decrease": "Decrease",
        "stabilize": "Stabilize",
        "unclear": "Unclear",
    }[item.expected_direction]
    source = "Uploaded data" if item.check_source == "uploaded_data" else "External data"
    return (
        '<article class="fm-ai-card">'
        f'<div class="fm-ai-card-tag">Priority {item.priority} · {source} '
        f"· Tests hypothesis {item.hypothesis_rank}</div>"
        f"<p><strong>{escape(item.investigation)}</strong></p>"
        f"<p><strong>What to check:</strong> {escape(item.what_to_check)}</p>"
        f"<p><strong>If supported:</strong> {escape(item.if_supported)}</p>"
        f"<p><strong>If not supported:</strong> {escape(item.if_not_supported)}</p>"
        f'<p class="fm-panel-note">Expected direction for {escape(metric_label(item.target_metric))}: '
        f"{direction}. {escape(item.expected_impact)}</p>"
        f"{_citations(item.evidence_ids, evidence)}"
        "</article>"
    )


def _information_html(items, evidence):
    if not items:
        return ""
    rows = "".join(
        f"<article class=\"fm-ai-card\"><p><strong>{escape(item.information)}</strong> "
        f"{escape(item.reason)}</p>{_citations(item.evidence_ids, evidence)}</article>"
        for item in items
    )
    return f'<div class="fm-ai-heading">Additional information needed</div>{rows}'


def _adjustments_html(notes):
    if not notes:
        return ""
    return (
        '<div class="fm-ai-heading">Adjusted by the app</div>'
        f'<ul class="fm-panel-note">{_items(notes)}</ul>'
    )


def ai_diagnosis_html(result, evidence):
    diagnosis = result.diagnosis
    hypotheses = "".join(_hypothesis_html(item, evidence) for item in diagnosis.hypotheses)
    recommendations = "".join(
        _recommendation_html(item, evidence) for item in diagnosis.recommendations
    )
    return (
        '<section class="fm-ai">'
        f'<div class="fm-panel-tag">AI-generated · {escape(result.provider)} {escape(result.model)}</div>'
        '<p class="fm-panel-note">Interpretation of the calculated evidence. Causes are not confirmed, '
        "and every action is a suggestion for you to review.</p>"
        + (f'<div class="fm-ai-heading">AI-generated hypotheses</div>{hypotheses}' if hypotheses else "")
        + (
            f'<div class="fm-ai-heading">AI-generated recommendations</div>{recommendations}'
            if recommendations
            else ""
        )
        + _information_html(diagnosis.additional_information, evidence)
        + '<div class="fm-ai-heading">Confidence and uncertainty</div>'
        f"<p>{escape(diagnosis.confidence_explanation)}</p>"
        + _adjustments_html(result.notes)
        + "</section>"
    )


def mathematical_diagnosis_html(sentences):
    return (
        '<section class="fm-panel fm-confirmed">'
        '<div class="fm-panel-tag">Calculated by Python</div>'
        f"<p>{escape(' '.join(sentences))}</p>"
        "</section>"
    )


def findings_html(findings):
    needed = (
        f"<p>Evidence needed before naming a cause:</p><ul>{_items(findings.evidence_needed)}</ul>"
        if findings.evidence_needed
        else "<p>No further evidence is requested for this comparison.</p>"
    )
    return (
        '<div class="fm-findings">'
        '<section class="fm-panel fm-confirmed">'
        '<div class="fm-panel-tag">Confirmed observations</div>'
        '<p class="fm-panel-note">Calculated from your data.</p>'
        f"<ul>{_items(findings.observations)}</ul></section>"
        '<section class="fm-panel fm-confirmed">'
        '<div class="fm-panel-tag">Mathematical drivers</div>'
        '<p class="fm-panel-note">How the ROAS arithmetic changed, not why.</p>'
        f"<ul>{_items(findings.drivers)}</ul></section>"
        '<section class="fm-panel fm-hypotheses">'
        '<div class="fm-panel-tag">Hypotheses</div>'
        '<p class="fm-panel-note">The calculations cannot show causes. Any AI-generated hypotheses '
        "appear only in the AI diagnosis section.</p>"
        f"{needed}</section>"
        "</div>"
    )
