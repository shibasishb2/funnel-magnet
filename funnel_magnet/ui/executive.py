"""Level 1: a plain-English executive diagnosis, written only by Python templates.

Everything here restates the engine's figures and the playbook diagnosis that the
detailed analysis shows, so it adds no finding. No AI writes any of it. When AI has
prioritised the diagnosis, the top three checks and the next steps follow that order.
"""

from dataclasses import dataclass

from funnel_magnet.diagnose import Status
from funnel_magnet.ui import view

NEXT_STEPS = 3
EVERY_PROBLEM = "Every problem above"


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
    could_mean: str
    ai_note: str = ""


@dataclass(frozen=True)
class NextStep:
    cause: str
    statement: str
    checks: tuple[tuple[int, str], ...]
    action: str

    @property
    def text(self):
        """The hypothesis, how to test it and what to consider, in the playbook's own words."""
        checks = _join(f"\u201c{title}\u201d (priority {rank})" for rank, title in self.checks)
        return (
            f"{self.statement} Test it with {checks}. "
            f"If the evidence supports it, {self.action[0].lower()}{self.action[1:]}"
        )


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
    sentences += marketing.patterns
    sentences.append("These figures show what changed, not why.")
    return tuple(sentences)


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


def _could_mean(item, cause_titles):
    causes = [test for test in item.tests if test in cause_titles]
    parts = []
    if causes:
        parts.append(
            f"A result that fits {_join(causes, 'or')} makes that cause more likely. "
            "A result that doesn't makes it less likely."
        )
    for test in item.tests:
        if test == EVERY_PROBLEM:
            parts.append("It applies to every problem above.")
        elif test not in cause_titles:
            parts.append(f"It shows {test[0].lower()}{test[1:]}.")
    return " ".join(dict.fromkeys(parts))


def _checks(marketing):
    cause_titles = {cause.title for cause in marketing.causes}
    return tuple(
        TopCheck(
            rank,
            item.title,
            "In your upload" if item.available else "Collect outside this app",
            item.detail,
            item.reason,
            _could_mean(item, cause_titles),
            item.ai_note,
        )
        for rank, item in enumerate(marketing.top_investigations, 1)
    )


def _next_steps(marketing):
    """Conditional actions for causes the top three checks test.

    The checks take turns in their priority order, each offering its next cause in the
    diagnosis's cause order, so the steps cover more than one check. The full action
    plan for every cause stays in the detailed analysis.
    """
    actions = {action.cause: action for action in marketing.actions}
    top = marketing.top_investigations
    queues = [[c.title for c in marketing.causes if c.title in item.tests and c.title in actions] for item in top]
    chosen = []
    while len(chosen) < NEXT_STEPS and any(queues):
        for queue in queues:
            while queue and queue[0] in chosen:
                queue.pop(0)
            if queue and len(chosen) < NEXT_STEPS:
                chosen.append(queue.pop(0))
    statements = {cause.title: cause.statement for cause in marketing.causes}
    return tuple(
        NextStep(
            cause,
            statements[cause],
            tuple((rank, item.title) for rank, item in enumerate(top, 1) if cause in item.tests),
            actions[cause].if_supported,
        )
        for cause in chosen
    )


def _data_notes(messages):
    notes = []
    for group, items in view.group_warnings(messages).items():
        more = f" ({len(items) - 1} more in the detailed analysis)" if len(items) > 1 else ""
        notes.append(f"{group}: {items[0]}{more}")
    return tuple(notes)


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
    )
