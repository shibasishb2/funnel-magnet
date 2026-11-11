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


def info_icon(entry):
    """A small clickable "i" that opens the KPI's name, meaning, formula and example."""
    if not entry:
        return ""
    name, definition, formula, example = entry
    return (
        f'<details class="fm-info"><summary title="What is {escape(name)}?" aria-label="What is {escape(name)}?">i</summary>'
        f'<div class="fm-info-body"><strong>{escape(name)}</strong><p>{escape(definition)}</p>'
        f"<p><strong>Formula:</strong> {escape(formula)}</p>"
        + (f"<p><strong>Here:</strong> {escape(example)}</p>" if example else "")
        + "</div></details>"
    )


def kpis_html(cards, prior_label, current_label, help=None, plain=False):
    """KPI tiles. With help, each label gets an info icon; plain shows the full name first."""
    help = dict(help or ())

    def label(card):
        entry = help.get(card.metric)
        if plain and entry:
            return f'{escape(entry[0].split(" (")[0])} <span class="fm-abbr">{escape(card.label)}</span>{info_icon(entry)}'
        return f"{escape(card.label)}{info_icon(entry)}"

    tiles = "".join(
        '<div class="fm-kpi">'
        f'<div class="fm-kpi-label">{label(card)}</div>'
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
    from funnel_magnet.ui.executive import ai_summary

    summary = ai_summary(ai)
    withheld = (
        f'<p class="fm-panel-note">{escape(summary)}</p>'
        + (_more("Technical details", f'<ul class="fm-panel-note">{_items(ai.withheld)}</ul>') if ai.withheld else "")
        if summary or ai.withheld
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
        '<p class="fm-panel-note">It reordered the playbook\'s checks and causes and added no findings. '
        "Causes remain unconfirmed.</p>"
        f"{relationship}{withheld}</section>"
    )


def _signs_note(cause, note_class):
    notes = [text for _, kind, text in cause.signs if kind != "supports"]
    return f'<p class="{note_class}">{escape(" ".join(notes))}</p>' if notes else ""


def _cause_card(cause):
    return (
        '<article class="fm-ai-card">'
        f'<div class="fm-ai-card-tag">{escape(cause.title)} · Could explain {escape(", ".join(cause.explains))}</div>'
        f"<p>{escape(cause.statement)}</p>{_signs_note(cause, 'fm-panel-note')}"
        f"{_ai_note('Why the AI ranks it here', cause.ai_note)}</article>"
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
    return "Uses data already in this app" if item.available else "Needs data from outside this app"


def interpretation_html(item):
    """What to look at, what a result could mean, and what to do if it's unclear."""
    if not item.readings:
        return ""
    readings = "".join(f"<li>{escape(reading)}</li>" for reading in item.readings)
    return (
        f'<div class="fm-reading"><p><strong>What to look at:</strong> {escape(item.look_at)}</p>'
        f"<p><strong>What the result could mean:</strong></p><ul>{readings}</ul>"
        f"<p><strong>If the result is unclear:</strong> {escape(item.if_unclear)}</p></div>"
    )


def _check_html(item):
    folded = _more("What the result could mean", interpretation_html(item)) if item.readings else ""
    return (
        f"<li><strong>{escape(item.title)}</strong> · {_where(item)}. {escape(item.detail)}{folded}"
        f'<div class="fm-panel-note">Helps test: {escape(", ".join(item.tests))}.</div>'
        f"{_ai_note('AI', item.ai_note)}</li>"
    )


def _top_check_html(rank, item):
    """A top-three check in the detailed analysis. Its interpretation is in the summary above."""
    why = f'<p class="fm-panel-note">Why first: {escape(item.reason)}</p>' if item.reason else ""
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


def _exec_card(check):
    """One check in plain words: what to check, the evidence to look for, and what to do."""
    lines = f"<p><strong>What to check:</strong> {escape(check.look_at)}</p>"
    if check.supports_if:
        lines += f"<p><strong>Evidence to look for:</strong> {escape(check.supports_if)}</p>"
    if check.cause:
        related = f" (related: {escape(', '.join(check.related))})" if check.related else ""
        lines += (
            f"<p><strong>Possible cause:</strong> {escape(check.cause)}{related}</p>"
            f"<p><strong>If supported, consider:</strong> {escape(check.action)}</p>"
        )
    more = "".join(f"<li>{escape(reading)}</li>" for reading in check.other_readings)
    folded = _more(
        "Other possible results",
        (f"<ul>{more}</ul>" if more else "") + f"<p><strong>If the result is unclear:</strong> {escape(check.if_unclear)}</p>",
    ) if (more or check.if_unclear) else ""
    return (
        '<article class="fm-ai-card fm-exec-check">'
        f'<div class="fm-ai-card-tag">Check {check.rank} · {escape(check.where)}</div>'
        f"<p><strong>{escape(check.title)}</strong></p>{lines}{folded}</article>"
    )


def executive_html(summary, prior_label="", current_label=""):
    """The business summary: what changed, what needs attention, and what to check next."""
    help = dict(summary.help)
    source_class = "fm-source fm-source-synthetic" if summary.synthetic else "fm-source"
    source = f'<p class="{source_class}">{escape(summary.source)}</p>' if summary.source else ""
    tiles = ""
    if summary.tiles:
        tiles = kpis_html(summary.tiles, prior_label, current_label, summary.help, plain=True)
        tiles = tiles[tiles.index('<div class="fm-kpis'):].replace('class="fm-kpis"', 'class="fm-kpis fm-kpis-compact"', 1)
    effects = "".join(f"<p>{escape(effect)}</p>" for effect in summary.effects)
    cancel = f"<p>{escape(summary.cancel)}</p>" if summary.cancel else ""

    if summary.problems:
        problems = "<ul>" + "".join(
            f"<li><strong>{escape(p.name or p.label)}</strong>{info_icon(help.get(p.metric))}"
            + (' <span class="fm-hidden">Hidden by the overall result</span>' if p.hidden else "")
            + (f"<div>{escape(p.effect)}</div>" if p.effect else "")
            + (f'<div class="fm-panel-note">{escape(p.why)}</div>' if p.why else "")
            + "</li>"
            for p in summary.problems
        ) + "</ul>"
    else:
        problems = "<p>No key figure worsened by enough to count as a problem.</p>"
    notes = "".join(f"<li>{escape(note)}</li>" for note in summary.data_notes)

    checks = "".join(_exec_card(check) for check in summary.checks)
    stage_checks = "".join(
        f'<p class="fm-panel-note"><strong>Also check the {escape(label)} step, after tracking:</strong> '
        f"{escape(' · '.join(c.title for c in checks_))}.</p>"
        for label, checks_ in summary.stage_checks
    )
    investigate = (checks + stage_checks) if checks else "<p>Nothing needs checking for this comparison.</p>"
    return (
        '<section class="fm-panel fm-executive fm-section">'
        f"{source}"
        '<div class="fm-question">What changed?</div>'
        f'<p class="fm-headline">{escape(summary.headline)}</p>{tiles}{effects}{cancel}'
        '<div class="fm-question">What needs attention?</div>'
        f"{problems}"
        + (f'<ul class="fm-panel-note">{notes}</ul>' if notes else "")
        + '<div class="fm-question">What should I check next?</div>'
        f"{investigate}"
        "</section>"
    )


def glossary_html(help, mechanisms=()):
    """Every KPI and funnel step in one place, with how they fit together."""
    from funnel_magnet.ui.view import GLOSSARY_NOTE, KPI_RELATIONSHIPS

    rows = "".join(
        f'<div class="fm-glossary-item"><strong>{escape(name)}</strong><p>{escape(definition)}</p>'
        f'<p class="fm-panel-note">Formula: {escape(formula)}</p>'
        + (f'<p class="fm-panel-note">Here: {escape(example)}</p>' if example else "")
        + "</div>"
        for _, (name, definition, formula, example) in help
    )
    links = "".join(f"<li>{escape(line)}</li>" for line in KPI_RELATIONSHIPS)
    paths = "".join(f"<li>{escape(chain)}</li>" for chain in mechanisms)
    return (
        '<section class="fm-panel fm-section">'
        '<div class="fm-panel-tag">KPI glossary</div>'
        f'<p class="fm-panel-note">{escape(GLOSSARY_NOTE)}</p>{rows}'
        f'<div class="fm-ai-heading">How the KPIs fit together</div><ul>{links}</ul>'
        + (f'<div class="fm-ai-heading">The diagnostic path in this comparison</div><ul>{paths}</ul>' if paths else "")
        + "</section>"
    )
