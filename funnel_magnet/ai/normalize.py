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
    # Website and order records. A Meta Ads export has none of these.
    "Site performance data": (
        r"\b(?:page load(?: times?)?|load times?|server response(?: times?)?|site speed|core web vitals)\b"
    ),
    "Website change records": (
        r"\b(?:redesigns?|design changes?|page changes?|content changes?|change ?logs?|ui (?:changes?|tweaks?))\b"
    ),
    "Checkout and payment records": (
        r"\b(?:checkout (?:logs?|errors?|steps?|flow)|funnel logs?|payment (?:errors?|failures?|logs?|options?|gateway)"
        r"|abandonment reasons?|drop[- ]off reasons?|error rates?|form errors?)\b"
    ),
    "Order-level data": r"\b(?:order[- ]level|product (?:mix|categor\w*|skus?)|items per order|discount(?:s| tiers?)?)\b",
}


# Models often write "ad‑level" with a non-breaking hyphen, which plain patterns miss.
HYPHENS = str.maketrans({char: "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2212"})


def plain_hyphens(text):
    return text.translate(HYPHENS)


def _data_named_in(text):
    text = plain_hyphens(text)
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
                        # Quoting the check would make the app's own disclosure look like
                        # a claim to the validator, and a number would fail its number check.
                        reason="Needed for a recommended check that uses outside data.",
                        evidence_ids=item.evidence_ids,
                    )
                )
                listed += f" {name}"
                notes.append(f"Added {name.lower()} to the additional information needed.")
    return needs


def recover_target_metrics(reply, evidence):
    """Fill a recommendation's missing target_metric only from its own cited evidence.

    It is filled when the cited metric and driver facts name exactly one metric, and
    that metric matches the linked hypothesis's node if there is one. Otherwise the
    recommendation is removed and reported, because guessing would invent a claim.
    Returns (reply, notes, issues).
    """
    notes, issues = [], []
    if not isinstance(reply, dict) or not isinstance(reply.get("recommendations"), list):
        return reply, notes, issues

    nodes = {
        item.get("rank"): item.get("diagnostic_node")
        for item in reply.get("hypotheses") or []
        if isinstance(item, dict)
    }
    kept = []
    for item in reply["recommendations"]:
        if not isinstance(item, dict) or item.get("target_metric"):
            kept.append(item)
            continue
        cited = {
            evidence.by_id[fact_id].metric
            for fact_id in item.get("evidence_ids") or []
            if fact_id in evidence.by_id
            and evidence.by_id[fact_id].kind in ("metric", "mathematical_driver")
        }
        node = nodes.get(item.get("hypothesis_rank"))
        label = f"recommendation {item.get('priority', len(kept) + 1)}"
        if len(cited) == 1 and node in (None, *cited):
            metric = cited.pop()
            kept.append({**item, "target_metric": metric})
            notes.append(f"Filled the missing target metric of {label} with {metric}, the only metric it cites.")
        else:
            issues.append(f"{label.capitalize()} had no target metric, and its evidence doesn't identify one.")
    return {**reply, "recommendations": kept}, notes, issues


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
