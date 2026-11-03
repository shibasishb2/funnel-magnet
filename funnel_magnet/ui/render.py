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


# The four-part marketing diagnosis. The report renders the same MarketingDiagnosis.


def _move_html(move):
    extra = [f"Path: {move.path}"]
    if move.contribution:
        extra.append(f"Effect on ROAS: {move.contribution}" + (f" ({move.effect.lower()})" if move.effect else ""))
    return (
        f"<li><strong>{escape(move.change)}</strong>"
        f'<div class="fm-panel-note">{escape(". ".join(extra))}.</div></li>'
    )


def confirmed_findings_html(marketing):
    problems = "".join(_move_html(move) for move in marketing.problems)
    beneficial = "".join(_move_html(move) for move in marketing.beneficial)
    return (
        '<section class="fm-panel fm-confirmed fm-section">'
        '<div class="fm-panel-tag">A. Confirmed findings · Calculated by Python</div>'
        f"<p>{escape(' '.join(marketing.summary))}</p>"
        '<div class="fm-ai-heading">KPIs that worsened materially</div>'
        + (f"<ul>{problems}</ul>" if problems else "<p>No final KPI worsened by the threshold or more.</p>")
        + '<div class="fm-ai-heading">KPIs that improved materially</div>'
        + (f"<ul>{beneficial}</ul>" if beneficial else "<p>No final KPI improved by the threshold or more.</p>")
        + '<p class="fm-panel-note">Improvements are reported as calculated, with no causes suggested.</p>'
        "</section>"
    )


def _ai_note(label, text):
    return f'<p class="fm-ai-note"><strong>{label}:</strong> {escape(text)}</p>' if text else ""


def ai_status_html(ai):
    withheld = (
        '<div class="fm-panel-note">Withheld because they failed the checks:</div>'
        f'<ul class="fm-panel-note">{_items(ai.withheld)}</ul>'
        if ai.withheld
        else ""
    )
    relationship = (
        f"<p><strong>How the problems might relate (AI, unconfirmed):</strong> {escape(ai.relationship)}</p>"
        if ai.relationship
        else ""
    )
    return (
        '<section class="fm-panel fm-ai-status">'
        f'<div class="fm-panel-tag">AI prioritisation · {escape(ai.provider)} {escape(ai.model)}</div>'
        '<p class="fm-panel-note">The AI reordered and explained the playbook items below. It added no findings, '
        "and every cause remains unconfirmed.</p>"
        f"{relationship}{withheld}</section>"
    )


def _cause_card(cause):
    return (
        '<article class="fm-ai-card">'
        f'<div class="fm-ai-card-tag">{escape(cause.title)} · Could explain {escape(", ".join(cause.explains))}</div>'
        f"<p>{escape(cause.statement)}</p>{_ai_note('Why the AI ranks it here', cause.ai_note)}</article>"
    )


def _more(summary, body):
    return f'<details class="fm-more"><summary>{escape(summary)}</summary>{body}</details>'


def possible_causes_html(marketing):
    notes = "".join(f'<div class="fm-pattern">{escape(note)}</div>' for note in (*marketing.patterns, *marketing.component_notes))
    shared = "".join(_cause_card(cause) for cause in marketing.shared_causes)
    separate = "".join(_cause_card(cause) for cause in marketing.separate_causes)
    if not (notes or marketing.causes):
        body = "<p>No KPI worsened materially, so the playbook suggests no causes.</p>"
    else:
        body = notes
        if shared:
            body += (
                '<div class="fm-ai-heading">Possible shared explanations</div>'
                '<p class="fm-panel-note">Each could explain more than one problem. None is assumed.</p>'
                f"{shared}"
            )
        if separate:
            label = "Separate possible causes" if shared else "Possible causes"
            body += _more(f"{label} ({len(marketing.separate_causes)})", separate)
    return (
        '<section class="fm-panel fm-hypotheses fm-section">'
        '<div class="fm-panel-tag">B. Possible causes · From the marketing playbook, not confirmed</div>'
        '<p class="fm-panel-note">Each is a possibility to test. The data shows what changed, not why.</p>'
        f"{body}</section>"
    )


def _where(item):
    return "In your upload" if item.available else "Collect outside this app"


def _check_html(item):
    return (
        f"<li><strong>{escape(item.title)}</strong> · {_where(item)}. {escape(item.detail)}"
        f'<div class="fm-panel-note">Helps test: {escape(", ".join(item.tests))}.</div>'
        f"{_ai_note('AI', item.ai_note)}</li>"
    )


def _top_check_html(rank, item):
    why = f"<p><strong>Why first:</strong> {escape(item.reason)}</p>" if item.reason else ""
    return (
        '<article class="fm-ai-card fm-priority">'
        f'<div class="fm-ai-card-tag">Priority {rank} · {_where(item)}</div>'
        f"<p><strong>{escape(item.title)}.</strong> {escape(item.detail)}</p>{why}"
        f"{_ai_note('Why the AI ranks it here', item.ai_note)}"
        f'<p class="fm-panel-note">Helps test: {escape(", ".join(item.tests))}.</p></article>'
    )


def investigations_html(marketing):
    if not marketing.investigations:
        body = "<p>Nothing needs investigating for this comparison.</p>"
    else:
        body = "".join(_top_check_html(rank, item) for rank, item in enumerate(marketing.top_investigations, 1))
        others = marketing.other_investigations
        if others:
            body += _more(f"All other checks ({len(others)})", f"<ul>{''.join(_check_html(item) for item in others)}</ul>")
    return (
        '<section class="fm-panel fm-section">'
        '<div class="fm-panel-tag">C. What to investigate · Top three first</div>'
        f"{body}</section>"
    )


def actions_html(marketing):
    cards = "".join(
        '<article class="fm-ai-card">'
        f'<div class="fm-ai-card-tag">{escape(action.cause)}</div>'
        f"<p><strong>If the evidence supports this cause:</strong> {escape(action.if_supported)}</p>"
        f"<p><strong>If it doesn't:</strong> {escape(action.if_not_supported)}</p>"
        f"{_ai_note('AI guidance, conditional', action.ai_note)}</article>"
        for action in marketing.actions
    )
    body = _more(f"Conditional actions for every cause ({len(marketing.actions)})", cards) if cards else (
        "<p>No actions are suggested for this comparison.</p>"
    )
    return (
        '<section class="fm-panel fm-section">'
        '<div class="fm-panel-tag">D. Potential actions · Only after the checks above</div>'
        '<p class="fm-panel-note">Nothing here is confirmed. Act only on what the investigation supports.</p>'
        f"{body}</section>"
    )


def executive_html(summary):
    """Level 1: the executive diagnosis, from the same diagnosis as the detailed analysis."""
    narrative = "".join(f"<p>{escape(sentence)}</p>" for sentence in summary.narrative)
    if summary.problems:
        problems = "<ul>" + "".join(
            f"<li><strong>{escape(p.change)}</strong>"
            + (' <span class="fm-hidden">Hidden by overall ROAS</span>' if p.hidden else "")
            + (f'<div class="fm-panel-note">{escape(p.effect)}</div>' if p.effect else "")
            + (f"<div>Why it matters: {escape(p.why)}</div>" if p.why else "")
            + "</li>"
            for p in summary.problems
        ) + "</ul>"
    else:
        problems = "<p>No final KPI worsened by the threshold or more.</p>"
    checks = "".join(
        '<article class="fm-ai-card fm-exec-check">'
        f'<div class="fm-ai-card-tag">Priority {c.rank} · {escape(c.where)}</div>'
        f"<p><strong>{escape(c.title)}.</strong> {escape(c.what)}</p>"
        + (f"<p><strong>What it could mean:</strong> {escape(c.could_mean)}</p>" if c.could_mean else "")
        + (f'<p class="fm-panel-note">Why first: {escape(c.why_first)}</p>' if c.why_first else "")
        + _ai_note("Why the AI ranks it here", c.ai_note)
        + "</article>"
        for c in summary.checks
    )
    steps = "".join(f"<li><strong>{escape(s.cause)}.</strong> {escape(s.text)}</li>" for s in summary.next_steps)
    source_class = "fm-source fm-source-synthetic" if summary.synthetic else "fm-source"
    source = f'<p class="{source_class}">{escape(summary.source)}</p>' if summary.source else ""
    notes = "".join(f"<li>{escape(note)}</li>" for note in summary.data_notes)
    return (
        '<section class="fm-panel fm-executive fm-section">'
        '<div class="fm-panel-tag">Plain-English summary · Written from Python\'s calculations</div>'
        f'{source}<p class="fm-headline">{escape(summary.headline)}</p>{narrative}'
        f"{_ai_note('AI, how the problems may relate', summary.ai_relationship)}"
        f'<div class="fm-ai-heading">KPIs that need attention</div>{problems}'
        + (f'<div class="fm-ai-heading">Top three investigations</div>{checks}' if checks else "")
        + (
            '<div class="fm-ai-heading">Next steps, only if the evidence supports them</div>'
            f'<ul>{steps}</ul><p class="fm-panel-note">The full action plan for every cause is in the detailed analysis.</p>'
            if steps else ""
        )
        + (f'<div class="fm-ai-heading">Data notes</div><ul class="fm-panel-note">{notes}</ul>' if notes else "")
        + "</section>"
    )
