"""Tidy harmless differences in model output, recording each change for the user.

Nothing here adds a claim. It reorders, relabels, lowers confidence or discloses
data needs, so factual problems are left for validate.py to reject.
"""

import re

from funnel_magnet.ai.schema import InformationNeed

MAX_HYPOTHESES = 3

SPECULATION = re.compile(
    r"\b(may|might|could|possibly|possible|potentially|perhaps)\b", re.IGNORECASE
)

# Parent metrics only move because their inputs move, so they can't support a hypothesis.
PARENT_METRICS = ("roas", "cpa", "cpc", "cvr")

# Data the app doesn't have. A recommendation that needs it must disclose it.
UNAVAILABLE_DATA = {
    "Frequency data": r"\bfrequency\b",
    "Reach data": r"\b(?:reach (?:data|metrics?|figures?)|(?:ad|audience|unique) reach)\b",
    "Placement data": r"\bplacements?\b",
    "Audience data": r"\baudiences?\b",
    "Competitor data": r"\bcompetit(?:ors?|ive|ion)\b",
    "Seasonality data": r"\bseasonal\w*\b",
    "Ad-level data": r"\bad[- ]level\b",
    "Bounce rate data": r"\bbounce rate\b",
    "Page speed data": r"\bpage speed\b",
    "Session data": r"\bsessions?\b",
    "Benchmark data": r"\bbenchmarks?\b",
    "Industry data": r"\bindustry\b",
}


def _data_named_in(text):
    return [
        (name, pattern)
        for name, pattern in UNAVAILABLE_DATA.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def has_direct_support(hypothesis, evidence):
    """Medium confidence needs a separate material metric, and enough purchase volume."""
    if any("directional" in fact.statement for fact in evidence.facts if fact.kind == "data_warning"):
        return False

    for fact_id in hypothesis.evidence_ids:
        fact = evidence.by_id.get(fact_id)
        if fact is None or fact.metric in (None, hypothesis.diagnostic_node, *PARENT_METRICS):
            continue
        if fact.data.get("status") not in (None, "stable", "undefined"):
            return True
        if fact.data.get("role") in ("adverse", "favorable"):
            return True
    return False


def _normalize_hypotheses(result, evidence, notes):
    ordered = sorted(result.hypotheses, key=lambda item: item.rank)
    if len(ordered) > MAX_HYPOTHESES:
        notes.append(f"Kept the top {MAX_HYPOTHESES} hypotheses.")

    kept, ranks = [], {}
    for number, item in enumerate(ordered[:MAX_HYPOTHESES], 1):
        updates = {"rank": number}
        ranks[item.rank] = number

        own_fact = evidence.metric_fact_id(item.diagnostic_node)
        if own_fact and own_fact not in item.evidence_ids:
            updates["evidence_ids"] = [own_fact, *item.evidence_ids]
        if not SPECULATION.search(item.unverified_business_cause):
            updates["unverified_business_cause"] = f"Possible cause: {item.unverified_business_cause}"
        if item.confidence != "low" and not has_direct_support(item, evidence):
            updates["confidence"] = "low"
            notes.append(f"Lowered hypothesis {number} to low confidence.")
        kept.append(item.model_copy(update=updates))

    dropped = {item.rank for item in ordered[MAX_HYPOTHESES:]}
    return kept, ranks, dropped


def _normalize_recommendations(result, ranks, dropped, notes):
    recommendations = []
    for item in result.recommendations:
        if item.hypothesis_rank in dropped:
            continue
        updates = {"hypothesis_rank": ranks.get(item.hypothesis_rank, item.hypothesis_rank)}
        needed = _data_named_in(f"{item.investigation} {item.what_to_check}")
        if item.check_source == "uploaded_data" and needed:
            updates["check_source"] = "external_data"
            notes.append(f'Relabelled "{item.investigation}" as an external check.')
        recommendations.append(item.model_copy(update=updates))

    # Stable sort, so the model's own priority order holds within each group.
    ordered = sorted(recommendations, key=lambda r: (r.check_source != "uploaded_data", r.priority))
    if [r.priority for r in ordered] != sorted(r.priority for r in recommendations):
        notes.append("Moved checks using the uploaded data ahead of external checks.")
    if ordered and all(r.check_source != "uploaded_data" for r in ordered):
        notes.append("No check using the uploaded data was suggested.")
    return [item.model_copy(update={"priority": n}) for n, item in enumerate(ordered, 1)]


def _disclose_data_needs(result, recommendations, notes):
    """List any outside data a recommendation mentions, so it is always disclosed."""
    needs = list(result.additional_information)
    listed = " ".join(need.information for need in needs)

    for item in recommendations:
        text = " ".join(
            [item.investigation, item.what_to_check, item.if_supported, item.if_not_supported, item.expected_impact]
        )
        for name, pattern in _data_named_in(text):
            if not re.search(pattern, listed, re.IGNORECASE):
                needs.append(
                    InformationNeed(
                        information=f"{name}.",
                        reason=f'Needed for this check: "{item.investigation}"',
                        evidence_ids=item.evidence_ids,
                    )
                )
                listed += f" {name}"
                notes.append(f"Added {name.lower()} to the additional information needed.")
    return needs


def normalize_diagnosis(result, evidence):
    """Return (tidied result, notes describing each change)."""
    notes = []
    hypotheses, ranks, dropped = _normalize_hypotheses(result, evidence, notes)
    recommendations = _normalize_recommendations(result, ranks, dropped, notes)
    needs = _disclose_data_needs(result, recommendations, notes)
    tidied = result.model_copy(
        update={
            "hypotheses": hypotheses,
            "recommendations": recommendations,
            "additional_information": needs,
        }
    )
    return tidied, notes
