"""Level 1: a plain-English executive diagnosis, written only by Python templates.

Everything here restates the engine's figures and the playbook diagnosis that the
detailed analysis shows, so it adds no finding. No AI writes any of it. When AI has
prioritised the diagnosis, the top three checks and the next steps follow that order.
"""

import re
from dataclasses import dataclass

from funnel_magnet.diagnose import Status
from funnel_magnet.ui import view

NEXT_STEPS = 3
STAGE_CHECKS = 4
# Both sides must be at least this share of the larger side to "largely cancel out".
CANCEL_RATIO = 0.5
SHARE_NOTE = (
    "\u201c% of all movement\u201d is each KPI\u2019s part of all ROAS movement in both directions added "
    "together, not a share of the net ROAS change."
)


@dataclass(frozen=True)
class ProblemKpi:
    label: str
    change: str
    effect: str
    hidden: bool
    why: str


@dataclass(frozen=True)
class TopCheck:
    rank: int
    title: str
    where: str
    what: str
    why_first: str
    look_at: str
    readings: tuple[str, ...]
    if_unclear: str
    ai_note: str = ""


@dataclass(frozen=True)
class StageCheck:
    title: str
    where: str
    look_at: str


@dataclass(frozen=True)
class NextStep:
    cause: str
    statement: str
    checks: tuple[tuple[int, str], ...]
    action: str
    # Causes with a near-identical action, folded into this step. Each keeps its own
    # action in the detailed analysis.
    related: tuple[str, ...] = ()

    @property
    def text(self):
        """The hypothesis, how to test it and what to consider, in the playbook's own words."""
        checks = _join(f"\u201c{title}\u201d (priority {rank})" for rank, title in self.checks)
        text = (
            f"{self.statement} Test it with {checks}. "
            f"If the evidence supports it, {self.action[0].lower()}{self.action[1:]}"
        )
        if self.related:
            text += (
                f" The same checks also bear on {_join(self.related)}, which leads to a similar action; "
                "its own action is in the detailed analysis."
            )
        return text


@dataclass(frozen=True)
class ExecutiveSummary:
    headline: str
    narrative: tuple[str, ...]
    problems: tuple[ProblemKpi, ...]
    checks: tuple[TopCheck, ...]
    next_steps: tuple[NextStep, ...]
    data_notes: tuple[str, ...]
    ai_relationship: str = ""
    source: str = ""
    synthetic: bool = False
    stage_checks: tuple[tuple[str, tuple[StageCheck, ...]], ...] = ()
    share_note: str = ""


def _join(items, word="and"):
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {word} {items[-1]}"


def _short(change):
    """Like "CPM up 18.3%". A funnel stage's plain name says enough, so it gets "down 18.2%"."""
    label = "" if "_to_" in change.metric else f"{view.metric_label(change.metric)} "
    if change.pct_change is None or change.pct_change == 0:
        return f"{label}unchanged"
    return f"{label}{'up' if change.pct_change > 0 else 'down'} {abs(change.pct_change):.1%}"


def _named(change):
    """Like "cost per 1,000 impressions (CPM up 18.3%)"."""
    plain = view.PLAIN_NAMES.get(change.metric)
    return f"{plain} ({_short(change)})" if plain else _short(change)


def _roas_text(roas):
    if roas.pct_change is None:
        return "ROAS couldn't be compared"
    if roas.status is Status.STABLE:
        return f"ROAS held steady ({roas.pct_change:+.1%})"
    return f"ROAS {'rose' if roas.pct_change > 0 else 'fell'} {abs(roas.pct_change):.1%}"


def _headline(diagnosis, marketing):
    if not diagnosis.assessable:
        return "ROAS can't be compared for these periods, because at least one has no spend."
    changes, threshold = diagnosis.changes, view.format_threshold(diagnosis.threshold)
    roas = changes["roas"]
    problems = [changes[move.metric] for move in marketing.problems]
    if not problems:
        if roas.status is Status.DETERIORATED:
            return f"{_roas_text(roas)}, but no single KPI worsened by {threshold} or more. The drop comes from several smaller changes."
        return f"{_roas_text(roas)}, and no KPI worsened by {threshold} or more."
    if roas.status is Status.DETERIORATED:
        headline = f"{_roas_text(roas)}. The biggest drag was {_named(problems[0])}"
        if len(problems) > 1:
            headline += f", and {_join(_named(c) for c in problems[1:])} also worsened"
        return headline + "."
    # ROAS held or rose, so something improving is hiding the problems.
    headline = f"{_roas_text(roas)}, but {_join(_named(c) for c in problems)} worsened"
    gains = [changes[move.metric] for move in marketing.beneficial]
    if gains:
        hidden = "it" if len(problems) == 1 else "them"
        headline += f". Gains in {_join(view.PLAIN_NAMES.get(c.metric, view.metric_label(c.metric)) for c in gains)} are hiding {hidden}"
    return headline + "."


def _path_to(diagnosis, metric):
    return next((path for path in diagnosis.paths if path[-1] == metric), (metric,))


def _movement(diagnosis, move, drivers, status):
    """How one final KPI's change reached ROAS, in figures.

    A KPI between it and ROAS is named only when it moved the same way, so an offset
    step, such as a CPC that fell while CPM rose, is never said to carry the change.
    """
    change = diagnosis.changes[move.metric]
    plain = view.PLAIN_NAMES.get(move.metric)
    subject = f"{plain[0].upper()}{plain[1:]} ({move.label})" if plain else move.label
    verb = "rose" if (change.pct_change or 0) > 0 else "fell"
    sentence = f"{subject} {verb} {abs(change.pct_change or 0):.1%}"
    middle = [diagnosis.changes[m] for m in reversed(_path_to(diagnosis, move.metric)[1:-1])]
    middle = [c for c in middle if c.status is status]
    if middle:
        sentence += f", feeding into {_join(_named(c) for c in middle)}"
    driver = drivers.get(move.metric)
    if driver is not None:
        effect = "took" if driver.contribution < 0 else "added"
        sentence += f", and {effect} {abs(driver.contribution):.2f} {'off' if driver.contribution < 0 else 'to'} ROAS"
    return sentence + "."


def _narrative(diagnosis, marketing):
    if not diagnosis.assessable:
        return ()
    drivers = {driver.metric: driver for driver in diagnosis.drivers}
    sentences = [_movement(diagnosis, move, drivers, Status.DETERIORATED) for move in marketing.problems]
    sentences += [_movement(diagnosis, move, drivers, Status.IMPROVED) for move in marketing.beneficial]
    roas = diagnosis.changes["roas"]
    prior, current = (view.format_value("roas", value) for value in (roas.prior, roas.current))
    if marketing.problems and roas.status is not Status.DETERIORATED:
        movement = "still rose" if roas.status is Status.IMPROVED else "held steady"
        problem = "the problem" if len(marketing.problems) == 1 else "these problems"
        sentences.append(f"Overall, ROAS {movement}, from {prior} to {current}, which can hide {problem}.")
    else:
        sentences.append(f"Overall, ROAS went from {prior} to {current}.")
    sentences += _cancelling(diagnosis)
    sentences += marketing.patterns
    sentences.append("These figures show what changed, not why.")
    return tuple(sentences)


def _cancelling(diagnosis):
    """Say plainly when gains and losses largely cancel out, so a small net change isn't misread."""
    gains = sum(d.contribution for d in diagnosis.drivers if d.contribution > 0)
    losses = -sum(d.contribution for d in diagnosis.drivers if d.contribution < 0)
    if not gains or not losses or min(gains, losses) < CANCEL_RATIO * max(gains, losses):
        return []
    return [
        f"These movements largely cancel out: across all KPIs, the gains added {gains:.2f} to ROAS and "
        f"the losses took {losses:.2f} off, so the net change was only {gains - losses:+.2f}."
    ]


def _problems(diagnosis, marketing):
    drivers = {driver.metric: driver for driver in diagnosis.drivers}
    hidden = diagnosis.assessable and diagnosis.changes["roas"].status is not Status.DETERIORATED
    items = []
    for move in marketing.problems:
        driver = drivers.get(move.metric)
        effect = (
            f"Took {abs(driver.contribution):.2f} off ROAS, {driver.weight:.0%} of all movement."
            if driver is not None and driver.contribution < 0 else ""
        )
        items.append(ProblemKpi(move.label, move.change, effect, hidden, view.WHY_IT_MATTERS.get(move.metric, "")))
    return tuple(items)


def _checks(marketing):
    return tuple(
        TopCheck(
            rank,
            item.title,
            "In your upload" if item.available else "Collect outside this app",
            item.detail,
            item.reason,
            item.look_at,
            item.readings,
            item.if_unclear,
            item.ai_note,
        )
        for rank, item in enumerate(marketing.top_investigations, 1)
    )


def _stems(text):
    return {word[:5] for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 3 and word not in {"then", "that", "with", "same", "time"}}


def _overlaps(first, second, actions):
    """Two causes whose titles share most words and whose actions name the same things,
    such as "Payment errors" and "Payment options", read as one step to a marketer."""
    titles = [_stems(first), _stems(second)]
    shared_title = len(titles[0] & titles[1]) / max(1, min(len(titles[0]), len(titles[1])))
    shared_action = _stems(actions[first].if_supported) & _stems(actions[second].if_supported)
    return shared_title >= 0.5 and len(shared_action) >= 2


def _stage_checks(marketing):
    """Checks aimed at each worsened funnel stage that the top three leave out."""
    top = {item.id for item in marketing.top_investigations}
    order = {item.id: item for item in marketing.investigations}
    stages = []
    for label, ids in marketing.stage_checks:
        items = [order[i] for i in order if i in ids and i not in top][:STAGE_CHECKS]
        if items:
            where = lambda item: "In your upload" if item.available else "Collect outside this app"
            stages.append((label, tuple(StageCheck(i.title, where(i), i.look_at) for i in items)))
    return tuple(stages)


def _next_steps(marketing):
    """Conditional actions for causes the top three checks test.

    The checks take turns in their priority order, each offering its next cause in the
    diagnosis's cause order, so the steps cover more than one check. The full action
    plan for every cause stays in the detailed analysis.
    """
    actions = {action.cause: action for action in marketing.actions}
    top = marketing.top_investigations
    # A cause whose usual explanation relies on a movement that went the other way isn't
    # promoted. It stays in the detailed analysis, and its slot goes to the next cause the
    # same checks test, never to an unrelated one.
    queues = [
        [c.title for c in marketing.causes if c.title in item.tests and c.title in actions and not c.contradicted]
        for item in top
    ]
    chosen, related = [], {}
    while len(chosen) < NEXT_STEPS and any(queues):
        for queue in queues:
            while queue and (queue[0] in chosen or any(queue[0] in r for r in related.values())):
                queue.pop(0)
            if queue and len(chosen) < NEXT_STEPS:
                cause = queue.pop(0)
                twin = next((c for c in chosen if _overlaps(c, cause, actions)), None)
                if twin:
                    related[twin] = (*related.get(twin, ()), cause)
                else:
                    chosen.append(cause)
    statements = {cause.title: cause.statement for cause in marketing.causes}
    return tuple(
        NextStep(
            cause,
            statements[cause],
            tuple((rank, item.title) for rank, item in enumerate(top, 1) if cause in item.tests),
            actions[cause].if_supported,
            related.get(cause, ()),
        )
        for cause in chosen
    )


def _data_notes(messages):
    notes = []
    for group, items in view.group_warnings(messages).items():
        more = f" ({len(items) - 1} more in the detailed analysis)" if len(items) > 1 else ""
        notes.append(f"{group}: {items[0]}{more}")
    return tuple(notes)


def ai_summary(ai):
    """A short, plain sentence about AI explanations left out, or "" when none were."""
    withheld = [issue for issue in ai.withheld if not issue.startswith("A ") or "was ignored" not in issue]
    parts = []
    if withheld:
        count = len(withheld)
        parts.append(
            f"{count} AI explanation{'s' if count > 1 else ''} didn't pass the accuracy checks and "
            f"{'were' if count > 1 else 'was'} left out, so the playbook text is shown instead."
        )
    if ai.repeated:
        parts.append(
            f"{ai.repeated} AI note{'s' if ai.repeated > 1 else ''} only repeated the playbook and "
            f"{'were' if ai.repeated > 1 else 'was'} left out."
        )
    if not parts and ai.withheld:
        parts.append("Some AI suggestions referred to items outside the playbook and were ignored.")
    return " ".join(parts)


def source_text(source_name, synthetic):
    """Say plainly whether the figures are a synthetic demonstration or the user's own file."""
    if not source_name:
        return ""
    if synthetic:
        return (
            f"Synthetic demonstration data: the \u201c{source_name}\u201d sample scenario. "
            "These figures are invented to show the method and are not real campaign results."
        )
    return f"Your uploaded data: {source_name}. Every figure here comes from this file only."


def build(diagnosis, marketing, warnings=(), source_name="", synthetic=False):
    """Return the executive diagnosis for the same diagnosis the detailed analysis shows."""
    return ExecutiveSummary(
        headline=_headline(diagnosis, marketing),
        narrative=_narrative(diagnosis, marketing),
        problems=_problems(diagnosis, marketing),
        checks=_checks(marketing),
        next_steps=_next_steps(marketing),
        data_notes=_data_notes([*warnings, *diagnosis.warnings]),
        ai_relationship=marketing.ai.relationship if marketing.ai else "",
        source=source_text(source_name, synthetic),
        synthetic=synthetic,
        stage_checks=_stage_checks(marketing),
        share_note=SHARE_NOTE if any(p.effect for p in _problems(diagnosis, marketing)) else "",
    )
