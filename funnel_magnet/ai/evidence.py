"""Build the only data the model ever sees: engine results with fact IDs.

Nothing here reads CSV rows, campaign names or file names. Fact IDs start with
X (Python's diagnosis), M (metric), D (driver), T (tree path), W (data warning)
or G (information gap).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from funnel_magnet.ui import view

NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*")
FACT_ID = re.compile(r"\b[XMDTWG]\d+\b")
PERIOD_LABEL = re.compile(r"\b\d{4}-(?:W\d{2}|\d{2})\b")


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    statement: str
    metric: str | None = None
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePack:
    context: dict
    facts: tuple[Fact, ...]
    path_nodes: frozenset[str]
    needs_hypotheses: bool

    def payload(self):
        return {
            "context": self.context,
            "facts": [
                {
                    "id": fact.id,
                    "kind": fact.kind,
                    "metric": fact.metric,
                    "statement": fact.statement,
                    **fact.data,
                }
                for fact in self.facts
            ],
        }

    def to_json(self):
        return json.dumps(self.payload(), indent=1, ensure_ascii=False)

    def fingerprint(self):
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def by_id(self):
        return {fact.id: fact for fact in self.facts}

    def metric_fact_id(self, metric):
        return next((f.id for f in self.facts if f.kind == "metric" and f.metric == metric), None)

    @property
    def metrics(self):
        return {fact.metric for fact in self.facts if fact.kind == "metric"}

    @property
    def campaign_data_available(self):
        return any("campaign" in check for check in self.context.get("checks_available_in_uploaded_data", ()))

    def allowed_numbers(self):
        """Return (numbers in the evidence, whole percentages they round to).

        Fact IDs and period labels are identifiers, so their digits don't count.
        """
        exact, whole_percents = set(), set()
        text = "\n".join(
            [json.dumps(self.context), *(f"{f.statement} {json.dumps(f.data)}" for f in self.facts)]
        )
        for token in NUMBER_PATTERN.findall(strip_identifiers(text)):
            value = parse_token(token)
            exact.update({value, round(value, 1), round(value, 2)})
            whole_percents.add(round(value))
        return exact, whole_percents


def strip_identifiers(text):
    # Period labels go first, because W35 in 2026-W35 also looks like a fact ID.
    return FACT_ID.sub(" ", PERIOD_LABEL.sub(" ", text))


def parse_token(token):
    """Read a number token. Commas are thousands separators in engine text."""
    parts = token.replace(",", "").split(".")
    return float(parts[0] + ("." + "".join(parts[1:]) if len(parts) > 1 else ""))


def _rounded(value, digits=4):
    return None if value is None else round(value, digits)


def _metric_facts(diagnosis, currency):
    metrics = [*view.KPI_ORDER, *view.rate_metrics(diagnosis)]
    facts = []
    for number, metric in enumerate(metrics, 1):
        change = diagnosis.changes[metric]
        prior = view.format_value(metric, change.prior, currency)
        current = view.format_value(metric, change.current, currency)
        label = view.metric_label(metric)
        statement = (
            f"{label}: {prior} to {current} "
            f"({view.format_change(change.pct_change)}, {view.STATUS_LABELS[change.status].lower()})."
        )
        facts.append(
            Fact(
                f"M{number}",
                "metric",
                statement,
                metric,
                {
                    "prior": _rounded(change.prior),
                    "current": _rounded(change.current),
                    "change_percent": None
                    if change.pct_change is None
                    else round(change.pct_change * 100, 1),
                    "status": change.status.value,
                },
            )
        )
    return facts


def _driver_facts(diagnosis):
    return [
        Fact(
            f"D{number}",
            "mathematical_driver",
            f"{view.metric_label(driver.metric)} contributed {driver.contribution:+.2f} to ROAS "
            f"({driver.weight:.0%} of all movement, {view.contribution_label(diagnosis, driver).lower()}).",
            driver.metric,
            {
                "roas_contribution": round(driver.contribution, 3),
                "share_of_movement_percent": round(driver.weight * 100),
                "role": driver.role,
                "effect_on_roas": view.contribution_label(diagnosis, driver).lower(),
            },
        )
        for number, driver in enumerate(diagnosis.drivers, 1)
    ]


def _path_facts(diagnosis):
    return [
        Fact(
            f"T{number}",
            "diagnostic_path",
            " → ".join(view.metric_label(node) for node in path) + ".",
            data={"nodes": list(path)},
        )
        for number, path in enumerate(diagnosis.paths, 1)
    ]


def _uploaded_data_checks(diagnosis, single_campaign):
    """List checks the user can already do in this dashboard."""
    checks = ["Compare these metrics across earlier weekly or monthly periods."]
    if not single_campaign and any("several campaigns" in text for text in diagnosis.warnings):
        checks.insert(0, "Select each campaign separately to see whether the change is isolated.")
    return checks


def build_evidence(diagnosis, currency=None, load_warnings=(), single_campaign=False):
    """Turn a diagnosis into structured evidence for the model."""
    warnings = [*load_warnings, *diagnosis.warnings]
    facts = [
        Fact(
            "X1",
            "mathematical_diagnosis",
            " ".join(view.mathematical_diagnosis(diagnosis, currency)),
        ),
        *_metric_facts(diagnosis, currency),
        *_driver_facts(diagnosis),
        *_path_facts(diagnosis),
        *[
            Fact(f"W{number}", "data_warning", view.humanize(text))
            for number, text in enumerate(warnings, 1)
        ],
        *[
            Fact(f"G{number}", "information_gap", text)
            for number, text in enumerate(diagnosis.data_needed, 1)
        ],
    ]
    context = {
        "prior_period": diagnosis.prior_label,
        "current_period": diagnosis.current_label,
        "scope": "one campaign" if single_campaign else "all campaigns combined",
        "currency": currency,
        "materiality_threshold_percent": round(diagnosis.threshold * 100, 2),
        "contributions_note": diagnosis.drivers_undefined_reason,
        "checks_available_in_uploaded_data": _uploaded_data_checks(diagnosis, single_campaign),
    }
    return EvidencePack(
        context,
        tuple(facts),
        frozenset(node for path in diagnosis.paths for node in path),
        needs_hypotheses=any(len(path) > 1 for path in diagnosis.paths),
    )
