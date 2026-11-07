"""Level 1: a short executive diagnosis, written only by Python templates.

It answers three questions: what changed, what needs attention, and what to investigate
next. Everything here restates the engine's figures and the playbook diagnosis that the
detailed analysis shows, so it adds no finding, and no AI writes any of it. When AI has
prioritised the diagnosis, the investigations and their actions follow that order.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from funnel_magnet import playbook
from funnel_magnet.diagnose import Status
from funnel_magnet.ui import view

NEXT_STEPS = 3
STAGE_CHECKS = 4
# ROAS plus at most this many KPIs in the "What changed?" tiles.
EXTRA_TILES = 3
# The mechanism chains shown, largest effect first.
MECHANISMS = 2
# Both sides must be at least this share of the larger side to "largely cancel out".
CANCEL_RATIO = 0.5
SHARE_NOTE = (
    "“% of all movement” is each KPI’s part of all ROAS movement in both directions added "
    "together, not a share of the net ROAS change."
)
WHERE = {True: "In your upload", False: "Collect outside this app"}


@dataclass(frozen=True)
class ProblemKpi:
    label: str
    change: str
    effect: str
    hidden: bool
    why: str


@dataclass(frozen=True)
class NextStep:
    """A possible cause the top three checks test, with its action if the evidence supports it."""

    cause: str
    statement: str
    checks: tuple[tuple[int, str], ...]
    action: str
    # Causes with a near-identical action, folded into this step. Each keeps its own
    # action in the detailed analysis.
    related: tuple[str, ...] = ()
    # The priority of the check this step was chosen for.
    origin: int = 0


@dataclass(frozen=True)
class TopCheck:
    rank: int
    title: str
    where: str
    look_at: str
    why_first: str
    # The possible cause this check could support, the result that would support it,
    # and the action to consider if it does. Empty when no promoted cause belongs here.
    cause: str
    supports_if: str
    action: str
    related: tuple[str, ...]
    # Secondary interpretations and what to do if the result is unclear, shown folded.
    other_readings: tuple[str, ...]
    if_unclear: str


@dataclass(frozen=True)
class StageCheck:
    title: str
    where: str
    look_at: str


@dataclass(frozen=True)
class ExecutiveSummary:
    headline: str
    tiles: tuple[view.Kpi, ...]
    mechanisms: tuple[str, ...]
    cancel: str
    problems: tuple[ProblemKpi, ...]
    data_notes: tuple[str, ...]
    checks: tuple[TopCheck, ...]
    next_steps: tuple[NextStep, ...]
    stage_checks: tuple[tuple[str, tuple[StageCheck, ...]], ...] = ()
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


def _tile(diagnosis, metric, currency):
    change = diagnosis.changes[metric]
    return view.Kpi(
        metric,
        view.metric_label(metric),
        view.format_value(metric, change.prior, currency),
        view.format_value(metric, change.current, currency),
        view.format_change(change.pct_change),
        change.status,
    )


def _tiles(diagnosis, marketing, currency):
    """ROAS, then the KPIs that explain it: the worsened and improved final KPIs first,
    then the KPIs between them and ROAS on the diagnostic path."""
    finals = [move.metric for move in (*marketing.problems, *marketing.beneficial)]
    between = [m for metric in finals for m in reversed(_path_to(diagnosis, metric)[1:-1])]
    chosen = list(dict.fromkeys(m for m in (*finals, *between) if m in diagnosis.changes))[:EXTRA_TILES]
    return tuple(_tile(diagnosis, metric, currency) for metric in ("roas", *chosen))


def _mechanisms(diagnosis, marketing):
    """The diagnostic path each final KPI followed to ROAS, such as
    "CPM +18.3% → CPC +17.1% → CPA +16.8% → ROAS −0.64 points"."""
    if not diagnosis.assessable:
        return ()
    drivers = {driver.metric: driver for driver in diagnosis.drivers}
    moves = list(marketing.problems)
    # When ROAS held or rose, the gain that hid the problems belongs in the picture too.
    if marketing.problems and diagnosis.changes["roas"].status is not Status.DETERIORATED:
        moves += list(marketing.beneficial[:1])
    chains = []
    for move in moves[:MECHANISMS]:
        path = _path_to(diagnosis, move.metric)
        steps = [f"{view.metric_label(m)} {view.format_change(diagnosis.changes[m].pct_change)}" for m in reversed(path[1:])]
        driver = drivers.get(move.metric)
        if driver is not None:
            steps.append(f"ROAS {driver.contribution:+.2f} points")
        chains.append(" → ".join(steps))
    return tuple(chains)


def _cancelling(diagnosis):
    """Say plainly when gains and losses largely cancel out, so a small net change isn't misread."""
    gains = sum(d.contribution for d in diagnosis.drivers if d.contribution > 0)
    losses = -sum(d.contribution for d in diagnosis.drivers if d.contribution < 0)
    if not gains or not losses or min(gains, losses) < CANCEL_RATIO * max(gains, losses):
        return ""
    return (
        f"Gains of {gains:.2f} and losses of {losses:.2f} ROAS points largely cancel out, "
        f"leaving a net change of only {gains - losses:+.2f}."
    )


def _problems(diagnosis, marketing):
    drivers = {driver.metric: driver for driver in diagnosis.drivers}
    hidden = diagnosis.assessable and diagnosis.changes["roas"].status is not Status.DETERIORATED
    items = []
    for move in marketing.problems:
        driver = drivers.get(move.metric)
        effect = (
            f"Took {abs(driver.contribution):.2f} off ROAS."
            if driver is not None and driver.contribution < 0 else ""
        )
        items.append(ProblemKpi(move.label, move.change, effect, hidden, view.WHY_IT_MATTERS.get(move.metric, "")))
    return tuple(items)


def _stems(text):
    return {word[:5] for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 3 and word not in {"then", "that", "with", "same", "time"}}


def _overlaps(first, second, actions):
    """Two causes whose titles share most words and whose actions name the same things,
    such as "Payment errors" and "Payment options", read as one step to a marketer."""
    titles = [_stems(first), _stems(second)]
    shared_title = len(titles[0] & titles[1]) / max(1, min(len(titles[0]), len(titles[1])))
    shared_action = _stems(actions[first].if_supported) & _stems(actions[second].if_supported)
    return shared_title >= 0.5 and len(shared_action) >= 2


def _next_steps(marketing):
    """One possible cause for each of the top three checks, with its action if supported.

    Each check offers the first cause it tests, in the diagnosis's cause order, that isn't
    already shown. A cause whose usual explanation relies on a movement that went the
    other way isn't promoted: it stays in the detailed analysis and the check offers its
    next cause instead, never an unrelated one. A cause with a near-identical action to
    one already shown is folded into that one as related.
    """
    actions = {action.cause: action for action in marketing.actions}
    top = marketing.top_investigations
    chosen, related, origin = [], {}, {}
    for rank, item in enumerate(top, 1):
        queue = [c.title for c in marketing.causes if c.title in item.tests and c.title in actions and not c.contradicted]
        for cause in queue:
            if cause in chosen or any(cause in r for r in related.values()):
                continue
            twin = next((c for c in chosen if _overlaps(c, cause, actions)), None)
            if twin:
                related[twin] = (*related.get(twin, ()), cause)
                continue
            chosen.append(cause)
            origin[cause] = rank
            break
    statements = {cause.title: cause.statement for cause in marketing.causes}
    return tuple(
        NextStep(
            cause,
            statements[cause],
            tuple((rank, item.title) for rank, item in enumerate(top, 1) if cause in item.tests),
            actions[cause].if_supported,
            related.get(cause, ()),
            origin[cause],
        )
        for cause in chosen
    )


# Readings that say a cause became less likely don't describe a supporting result.
WEAKENING = re.compile(r"\b(?:less likely|unlikely|barely)\b", re.IGNORECASE)


# Title words too common to show that a reading is about one particular cause.
GENERIC_TITLE_STEMS = frozenset({"accou", "campa", "chang", "shift", "probl"})


@lru_cache(maxsize=1)
def _cause_titles():
    return tuple(cause["title"].lower() for cause in playbook.load_playbook().causes)


def _about(reading, cause):
    """True when a reading refers to this cause, by its title or a distinctive title word,
    and doesn't name a different playbook cause instead."""
    lowered = reading.lower()
    title = cause.lower()
    if title in lowered:
        return True
    if any(other in lowered for other in _cause_titles() if other != title and other not in title):
        return False
    stems = {word[:5] for word in re.findall(r"[a-z]+", title) if len(word) > 3} - GENERIC_TITLE_STEMS
    return any(stem in lowered for stem in stems)


def _best_reading(readings, step):
    """The index of the reading that would support the step's cause, or None when none does.

    Readings that make a cause less likely never count as support, and neither does a
    reading about a different cause, so a card never borrows another cause's evidence.
    """
    supporting = [i for i, reading in enumerate(readings) if not WEAKENING.search(reading)]
    if step is None:
        return supporting[0] if supporting else None
    about = [i for i in supporting if _about(readings[i], step.cause)]
    if not about:
        return None
    words = _stems(f"{step.cause} {step.statement}")
    return max(about, key=lambda i: (len(_stems(readings[i]) & words), -i))


def _neutral(cause):
    """A conditional line for a card with no reading about its cause."""
    name = cause if cause[1:2].isupper() else cause[0].lower() + cause[1:]
    return f"If this check's results point to {name}, that would support this possible cause."


def _checks(marketing, steps):
    """The top three, each with the result that would support its cause and the action."""
    cards = []
    for rank, item in enumerate(marketing.top_investigations, 1):
        step = next((s for s in steps if s.origin == rank), None)
        pick = _best_reading(item.readings, step)
        cards.append(
            TopCheck(
                rank=rank,
                title=item.title,
                where=WHERE[item.available],
                look_at=item.look_at,
                why_first=item.reason,
                cause=step.cause if step else "",
                supports_if=item.readings[pick] if pick is not None else (_neutral(step.cause) if step else ""),
                action=step.action if step else "",
                related=step.related if step else (),
                other_readings=tuple(r for i, r in enumerate(item.readings) if i != pick),
                if_unclear=item.if_unclear,
            )
        )
    return tuple(cards)


def _stage_checks(marketing):
    """Checks aimed at each worsened funnel stage that the top three leave out."""
    top = {item.id for item in marketing.top_investigations}
    order = {item.id: item for item in marketing.investigations}
    stages = []
    for label, ids in marketing.stage_checks:
        items = [order[i] for i in order if i in ids and i not in top][:STAGE_CHECKS]
        if items:
            stages.append((label, tuple(StageCheck(i.title, WHERE[i.available], i.look_at) for i in items)))
    return tuple(stages)


def _data_notes(messages):
    """One short line per kind of warning. The full messages are under Data quality."""
    notes = []
    for group, items in view.group_warnings(messages).items():
        first = re.split(r"(?<=\.)\s", items[0], maxsplit=1)[0]
        more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
        notes.append(f"{group}: {first}{more}")
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
        return f"Synthetic sample data (“{source_name}”), not real campaign results."
    return f"Your uploaded data: {source_name}."


def build(diagnosis, marketing, warnings=(), source_name="", synthetic=False, currency=None):
    """Return the executive diagnosis for the same diagnosis the detailed analysis shows."""
    steps = _next_steps(marketing)
    return ExecutiveSummary(
        headline=_headline(diagnosis, marketing),
        tiles=_tiles(diagnosis, marketing, currency) if diagnosis.assessable else (),
        mechanisms=_mechanisms(diagnosis, marketing),
        cancel=_cancelling(diagnosis),
        problems=_problems(diagnosis, marketing),
        data_notes=_data_notes([*warnings, *diagnosis.warnings]),
        checks=_checks(marketing, steps),
        next_steps=steps,
        stage_checks=_stage_checks(marketing),
        source=source_text(source_name, synthetic),
        synthetic=synthetic,
    )
