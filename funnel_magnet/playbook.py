"""Turn the engine's result into the four-part marketing diagnosis, with no AI.

The playbook file lists possible causes for each KPI that can worsen. Python matches
them to the KPIs the decision tree found materially worse, so every cause, check and
action here comes from the playbook, never from a model. Causes are possibilities for
a person to test. Only part A states what the data establishes.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from funnel_magnet.diagnose import Status
from funnel_magnet.metrics import rate_name
from funnel_magnet.ui import view

PLAYBOOK_PATH = Path(__file__).with_name("playbook.toml")

FUNNEL_STAGES = (
    rate_name("link_clicks", "landing_page_views"),
    rate_name("landing_page_views", "add_to_carts"),
    rate_name("add_to_carts", "checkouts_initiated"),
    rate_name("checkouts_initiated", "purchases"),
)
KNOWN_METRICS = frozenset({"aov", "cpm", "ctr", "cpc", "cpa", "cvr", *FUNNEL_STAGES})
SOURCES = ("campaigns", "periods", "external")
TOP_INVESTIGATIONS = 3
# Checks that confirm the numbers are measured correctly. A funnel drop can be a
# counting fault, so these come first whenever a funnel stage worsened.
MEASUREMENT_CHECKS = frozenset({"tracking_diagnostics"})
# An investigation's readings say what a result could mean. None may claim proof.
OVERCLAIM = re.compile(r"\b(confirms?|confirmed|proven|definitely|certainly)\b", re.IGNORECASE)
PROVES = re.compile(r"\bproves?\b", re.IGNORECASE)
NOT_PROVES = re.compile(r"\b(?:not|n't)\s+prove\b", re.IGNORECASE)


@dataclass(frozen=True)
class Playbook:
    causes: tuple[dict, ...]
    combinations: tuple[dict, ...]
    component_rules: dict
    investigations: dict
    general_investigations: tuple[str, ...] = ()


@dataclass(frozen=True)
class KpiMove:
    metric: str
    label: str
    change: str
    path: str
    contribution: str | None = None
    effect: str | None = None


@dataclass(frozen=True)
class CauseItem:
    id: str
    title: str
    statement: str
    explains: tuple[str, ...]
    ai_note: str = ""

    @property
    def shared(self):
        """True when one cause could explain more than one of the problems."""
        return len(self.explains) > 1


@dataclass(frozen=True)
class InvestigationItem:
    id: str
    title: str
    detail: str
    available: bool
    tests: tuple[str, ...]
    reason: str = ""
    ai_note: str = ""
    look_at: str = ""
    readings: tuple[str, ...] = ()
    if_unclear: str = ""


@dataclass(frozen=True)
class ActionItem:
    cause: str
    if_supported: str
    if_not_supported: str
    ai_note: str = ""


@dataclass(frozen=True)
class AIPriorities:
    """What an AI added: order, explanations and checks. Never findings."""

    provider: str
    model: str
    relationship: str = ""
    withheld: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class MarketingDiagnosis:
    summary: tuple[str, ...]
    problems: tuple[KpiMove, ...]
    beneficial: tuple[KpiMove, ...]
    patterns: tuple[str, ...]
    component_notes: tuple[str, ...]
    causes: tuple[CauseItem, ...]
    investigations: tuple[InvestigationItem, ...]
    actions: tuple[ActionItem, ...]
    ai: AIPriorities | None = field(default=None)

    @property
    def has_funnel_problem(self):
        return any(move.metric in FUNNEL_STAGES or move.metric == "cvr" for move in self.problems)

    @property
    def shared_causes(self):
        return tuple(cause for cause in self.causes if cause.shared)

    @property
    def separate_causes(self):
        return tuple(cause for cause in self.causes if not cause.shared)

    @property
    def top_investigations(self):
        return self.investigations[:TOP_INVESTIGATIONS]

    @property
    def other_investigations(self):
        return self.investigations[TOP_INVESTIGATIONS:]


def _check(condition, message):
    if not condition:
        raise ValueError(f"Playbook error: {message}")


def load_playbook(path=PLAYBOOK_PATH):
    """Read and check the playbook. A mistake in the file fails loudly, never silently."""
    data = tomllib.loads(Path(path).read_text("utf-8"))
    investigations = {item["id"]: item for item in data.get("investigation", [])}
    combinations = tuple(data.get("combination", []))
    combination_ids = {item["id"] for item in combinations}
    causes = tuple(data.get("cause", []))

    _check(len(investigations) == len(data.get("investigation", [])), "investigation ids repeat.")
    _check(len({c["id"] for c in causes}) == len(causes), "cause ids repeat.")
    for item in investigations.values():
        _check(item.get("source") in SOURCES, f"investigation {item['id']} has an unknown source.")
        _check(item.get("title") and item.get("detail"), f"investigation {item['id']} needs a title and detail.")
        if item["source"] != "external":
            _check(item.get("unavailable_detail"), f"investigation {item['id']} needs unavailable_detail.")
        texts = [item.get("look_at"), *item.get("readings", []), item.get("if_unclear")]
        _check(all(texts) and item.get("readings"), f"investigation {item['id']} needs look_at, readings and if_unclear.")
        for text in texts:
            _check(not OVERCLAIM.search(text) and (not PROVES.search(text) or NOT_PROVES.search(text)),
                   f"investigation {item['id']} presents a result as proof.")
            _check(set(re.findall(r"\{[^}]*\}", text)) <= {"{kpis}"}, f"investigation {item['id']} has an unknown placeholder.")
    for cause in causes:
        name = cause.get("id")
        _check(" may " in f" {cause.get('statement', '')} ", f"cause {name} must be hedged with 'may'.")
        _check(set(cause.get("explains", [])) <= KNOWN_METRICS and cause.get("explains"), f"cause {name} explains an unknown metric.")
        _check(set(cause.get("investigations", [])) <= set(investigations), f"cause {name} uses an unknown investigation.")
        _check(cause.get("if_supported") and cause.get("if_not_supported"), f"cause {name} needs both actions.")
        _check(cause.get("only_with") in (None, *combination_ids), f"cause {name} has an unknown only_with.")
        _check(cause.get("requires") in (None, "campaigns"), f"cause {name} has an unknown requires.")
        conditional = cause.get("check_only_if", {})
        _check(set(conditional) <= set(cause.get("investigations", [])), f"cause {name} has check_only_if for a check it doesn't use.")
        _check(set(conditional.values()) <= KNOWN_METRICS, f"cause {name} has check_only_if with an unknown metric.")
    cause_ids = {c["id"] for c in causes}
    for rule in combinations:
        _check(set(rule.get("causes", [])) <= cause_ids, f"combination {rule['id']} uses an unknown cause.")
        _check(set(rule.get("when_all", [])) | set(rule.get("when_any", [])) <= KNOWN_METRICS, f"combination {rule['id']} uses an unknown metric.")
    general = tuple(data.get("general_investigations", []))
    _check(set(general) <= set(investigations), "general_investigations uses an unknown investigation.")
    rules = {rule["metric"]: rule for rule in data.get("component_rule", [])}
    for rule in rules.values():
        _check(set(rule.get("investigations", [])) <= set(investigations), f"component rule {rule['metric']} uses an unknown investigation.")
    return Playbook(causes, combinations, rules, investigations, general)


def _moves(diagnosis, currency, status):
    """KPIs that end a followed path and moved in the given direction, largest effect first."""
    drivers = {driver.metric: driver for driver in diagnosis.drivers}
    moves = []
    for path in diagnosis.paths:
        metric = path[-1]
        if len(path) < 2 or diagnosis.changes[metric].status is not status or metric in {m.metric for m in moves}:
            continue
        driver = drivers.get(metric)
        moves.append(
            KpiMove(
                metric=metric,
                label=view.metric_label(metric),
                change=view._observation(diagnosis.changes[metric], currency),
                path=" → ".join(view.metric_label(node) for node in path),
                contribution=None if driver is None else (
                    f"{driver.contribution:+.2f} ROAS points, {driver.weight:.0%} of all movement"
                ),
                effect=None if driver is None else view.contribution_label(diagnosis, driver),
            )
        )
    weight = {d.metric: abs(d.contribution) for d in diagnosis.drivers}
    return tuple(sorted(moves, key=lambda move: -weight.get(move.metric, 0)))


def keep_measurement_first(investigations, has_funnel_problem):
    """Put a measurement check in the top three for funnel problems, whatever the order."""
    items = list(investigations)
    measurement = [item for item in items if item.id in MEASUREMENT_CHECKS]
    if has_funnel_problem and measurement and measurement[0] not in items[:TOP_INVESTIGATIONS]:
        items.remove(measurement[0])
        items.insert(TOP_INVESTIGATIONS - 1, measurement[0])
    return tuple(items)


def _active(rule, problems):
    return set(rule.get("when_all", [])) <= problems and (
        not rule.get("when_any") or bool(set(rule["when_any"]) & problems)
    )


def _shares(diagnosis, problems):
    """Each problem's share of the problems' combined ROAS effect, equal when unknown."""
    effect = {d.metric: abs(d.contribution) for d in diagnosis.drivers}
    total = sum(effect.get(move.metric, 0) for move in problems)
    if not total:
        return {move.metric: 1 / len(problems) for move in problems} if problems else {}
    return {move.metric: effect.get(move.metric, 0) / total for move in problems}


def _rank(checks, shares, labels, has_funnel_problem):
    """Order checks by the five approved criteria, and say why the first three come first.

    Each check scores for the share of the ROAS problem it covers, how many causes it
    can tell apart, whether it tests a shared explanation, whether the data is already
    in the upload, and, for funnel problems, whether it checks the measurement itself.
    """
    most_causes = max((len(c["causes"]) for c in checks), default=0) or 1

    def score(check):
        impact = sum(shares[m] for m in check["kpis"])
        return (
            2 * impact
            + len(check["causes"]) / most_causes
            + 0.5 * bool(check["shared"])
            + 0.5 * check["available"]
            + (1.0 if check["measurement"] and has_funnel_problem else 0.0)
        )

    ordered = sorted(checks, key=lambda c: (-round(score(c), 6), c["first_cause"], c["order"]))
    # A funnel drop may be a counting fault, which would make every other check
    # misleading, so a measurement check always makes the top three.
    measurement = [check for check in ordered if check["measurement"]]
    if has_funnel_problem and measurement and measurement[0] not in ordered[:TOP_INVESTIGATIONS]:
        ordered.remove(measurement[0])
        ordered.insert(TOP_INVESTIGATIONS - 1, measurement[0])
    whose = "problems'" if len(shares) > 1 else "problem's"
    # Every check gets its reason, because an AI may move any of them into the top three.
    for check in ordered:
        parts = []
        if check["measurement"] and has_funnel_problem:
            parts.append("Rules out a counting fault before anything else")
        kpis = [labels[m] for m in check["kpis"]]
        if kpis:
            share = sum(shares[m] for m in check["kpis"])
            parts.append(f"Covers {', '.join(kpis)}, {share:.0%} of the {whose} ROAS effect")
        if check["shared"]:
            parts.append("Tests an explanation shared by more than one problem")
        if len(check["causes"]) > 1:
            parts.append(f"Can tell apart {len(check['causes'])} possible causes")
        parts.append("Uses data already in your upload" if check["available"] else "Needs data from outside this app")
        check["reason"] = ". ".join(parts) + "."
    return ordered


def build(diagnosis, currency=None, several_campaigns=False, earlier_periods=False, playbook=None):
    """Return the four-part diagnosis for one comparison."""
    playbook = playbook or load_playbook()
    problems = _moves(diagnosis, currency, Status.DETERIORATED)
    beneficial = _moves(diagnosis, currency, Status.IMPROVED)
    problem_metrics = {move.metric for move in problems}
    labels = {move.metric: move.label for move in problems}

    active = [rule for rule in playbook.combinations if _active(rule, problem_metrics)]
    active_ids = {rule["id"] for rule in active}
    emphasised = [cause_id for rule in active for cause_id in rule.get("causes", [])]

    matched = []
    for cause in playbook.causes:
        if cause.get("only_with") and cause["only_with"] not in active_ids:
            continue
        if cause.get("requires") == "campaigns" and not several_campaigns:
            continue
        explained = [metric for metric in cause["explains"] if metric in problem_metrics]
        if explained:
            matched.append((cause, explained))
    # Causes that could explain more of the problems come first, then those a combination
    # rule highlights, then those listed mainly for a problem KPI, then playbook order.
    matched.sort(
        key=lambda pair: (
            -len(pair[1]),
            pair[0]["id"] not in emphasised,
            pair[0]["explains"][0] not in problem_metrics,
        )
    )

    causes = tuple(
        CauseItem(cause["id"], cause["title"], cause["statement"], tuple(labels[m] for m in explained))
        for cause, explained in matched
    )

    cause_order = [cause["id"] for cause in playbook.causes]
    component = [playbook.component_rules[m] for m in problem_metrics if m in playbook.component_rules]
    checks = {}

    def note(check_id, test, kpis, cause=None, shared=False):
        entry = checks.setdefault(
            check_id,
            {"id": check_id, "tests": [], "causes": set(), "kpis": set(), "shared": False, "first_cause": len(cause_order)},
        )
        entry["tests"].append(test)
        entry["kpis"] |= set(kpis)
        entry["shared"] |= shared
        if cause:
            entry["causes"].add(cause["id"])
            entry["first_cause"] = min(entry["first_cause"], cause_order.index(cause["id"]))

    if problems:
        for check_id in playbook.general_investigations:
            note(check_id, "Every problem above", problem_metrics)
    for cause, explained in matched:
        conditional = cause.get("check_only_if", {})
        for check_id in cause["investigations"]:
            if check_id in conditional and conditional[check_id] not in problem_metrics:
                continue
            note(check_id, cause["title"], explained, cause, shared=len(explained) > 1)
    for rule in component:
        for check_id in rule.get("investigations", []):
            note(check_id, f"Where the {view.metric_label(rule['metric'])} change comes from", [rule["metric"]])

    availability = {"campaigns": several_campaigns, "periods": earlier_periods, "external": False}
    order = list(playbook.investigations)
    for entry in checks.values():
        item = playbook.investigations[entry["id"]]
        entry["available"] = availability[item["source"]]
        entry["measurement"] = entry["id"] in MEASUREMENT_CHECKS
        entry["order"] = order.index(entry["id"])
        entry["kpis"] = [m for m in labels if m in entry["kpis"]]
    has_funnel_problem = bool(problem_metrics & set(FUNNEL_STAGES)) or "cvr" in problem_metrics

    investigations = []
    for entry in _rank(list(checks.values()), _shares(diagnosis, problems), labels, has_funnel_problem):
        item = playbook.investigations[entry["id"]]
        # Name the KPIs this check covers, as in "If CPM worsened mainly in one campaign".
        names = [view.metric_label(m) for m in entry["kpis"]] or ["these KPIs"]
        kpis = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        investigations.append(
            InvestigationItem(
                id=entry["id"],
                title=item["title"],
                detail=item["detail"] if entry["available"] or item["source"] == "external" else item["unavailable_detail"],
                available=entry["available"],
                tests=tuple(dict.fromkeys(entry["tests"])),
                reason=entry.get("reason", ""),
                look_at=item["look_at"].replace("{kpis}", kpis),
                readings=tuple(reading.replace("{kpis}", kpis) for reading in item["readings"]),
                if_unclear=item["if_unclear"].replace("{kpis}", kpis),
            )
        )

    return MarketingDiagnosis(
        summary=tuple(view.mathematical_diagnosis(diagnosis, currency)),
        problems=problems,
        beneficial=beneficial,
        patterns=tuple(rule["note"] for rule in active),
        component_notes=tuple(rule["note"] for rule in component),
        causes=causes,
        investigations=tuple(investigations),
        actions=tuple(ActionItem(c["title"], c["if_supported"], c["if_not_supported"]) for c, _ in matched),
    )
