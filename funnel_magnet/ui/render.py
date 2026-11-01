"""Build the dashboard's HTML fragments. All data text is escaped."""

from html import escape
from pathlib import Path

from funnel_magnet.diagnose import Status
from funnel_magnet.ui.view import STATUS_LABELS

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
        '<p class="fm-panel-note">None generated. The data here cannot show causes.</p>'
        f"{needed}</section>"
        "</div>"
    )
