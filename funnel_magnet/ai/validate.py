"""Check model output against the evidence. Any problem rejects the whole response."""

import math
import re

from funnel_magnet.ai.evidence import NUMBER_PATTERN, parse_token, strip_identifiers

HEDGES = re.compile(
    r"\b(may|might|could|possibly|possible|potentially|perhaps|whether|if|hypothesis|"
    r"unconfirmed|unverified|not|cannot|check|review|compare|test|investigate|assess|examine)\b",
    re.IGNORECASE,
)
SPECULATION = re.compile(
    r"\b(may|might|could|possibly|possible|potentially|perhaps)\b", re.IGNORECASE
)
NEGATION = re.compile(r"\b(not|cannot|no)\b", re.IGNORECASE)
PROOF_CLAIMS = re.compile(
    r"\b(proves?|proven|proof|definitely|certainly|undoubtedly|guaranteed?|"
    r"entirely attributable|solely attributable)\b",
    re.IGNORECASE,
)
CAUSAL_CLAIMS = re.compile(
    r"\b(caused by|causing|because of|due to|as a result of|resulted (?:in|from)|led to|"
    r"the reason)\b",
    re.IGNORECASE,
)
# Fine for arithmetic when labelled as such, as in "mathematically explained by CPM".
ATTRIBUTION_CLAIMS = re.compile(
    r"\b(driven by|explained by|attributable to|accounted for by)\b", re.IGNORECASE
)
MATHEMATICAL_WORDING = re.compile(r"\b(mathematically|arithmetically)\b", re.IGNORECASE)
ROOT_CAUSE = re.compile(r"\broot cause\b", re.IGNORECASE)
ROOT_CAUSE_UNKNOWN = re.compile(
    r"\broot cause\b.{0,60}\b(unknown|unconfirmed|unclear|undetermined|unverified|"
    r"not (?:yet )?(?:been )?(?:known|established|confirmed|proven))\b",
    re.IGNORECASE,
)
# "Lacks competitor data" reports a gap, so naming the missing data isn't a claim.
MISSING_EVIDENCE = re.compile(
    r"\b(lacks?|lacking|without|missing|unavailable|absent|does not (?:include|contain|show))\b"
    r"[^.;]*\b(data|information|insights?|context|evidence|details?|metrics?|reports?)\b",
    re.IGNORECASE,
)
ASSERTED_CAUSE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:the|a|an)?\s*(?:main\s+|likely\s+)?(?:cause|problem|reason|issue)\b",
    re.IGNORECASE,
)
NAMED_CAUSES = re.compile(
    r"\b(fatigue|saturat\w+|auction pressure|competit\w+|seasonal\w*|tracking|pixel|"
    r"page speed|outage|bug)\b",
    re.IGNORECASE,
)
INVENTED_METRICS = re.compile(
    r"\b(LTV|CLV|CLTV|CAC|MER|ARPU|NPS|CPL|CPI|CPV|CPE|POAS|ACOS)\b"
)
UNAVAILABLE_METRICS = r"(frequency|reach|bounce rate|relevance score|quality rank\w*|engagement rate)"
METRIC_CHANGE = re.compile(
    UNAVAILABLE_METRICS
    + r"\b.{0,40}\b(rose|risen|fell|fallen|increased|decreased|declined|dropped|grew|spiked|worsened|improved)\b",
    re.IGNORECASE,
)

# Parent metrics only move because their inputs move, so they can't support a hypothesis.
PARENT_METRICS = ("roas", "cpa", "cpc", "cvr")

# Data the engine doesn't have. A recommendation that needs it must list it as needed.
UNAVAILABLE_DATA = {
    "frequency": r"\bfrequency\b",
    "reach": r"\b(?:reach (?:data|metrics?|figures?)|(?:ad|audience|unique) reach)\b",
    "placement data": r"\bplacements?\b",
    "audience data": r"\baudiences?\b",
    "competitor data": r"\bcompetitors?\b",
    "seasonality data": r"\bseasonal\w*\b",
    "ad-level data": r"\bad[- ]level\b",
    "bounce rate": r"\bbounce rate\b",
    "page speed": r"\bpage speed\b",
    "session data": r"\bsessions?\b",
}


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", text) if part.strip()]


def _reports_missing_evidence(sentence):
    return bool(MISSING_EVIDENCE.search(sentence)) and not (
        ASSERTED_CAUSE.search(sentence) or CAUSAL_CLAIMS.search(sentence)
    )


def _sentence_problem(sentence):
    """Return the first problem in a sentence, so one wording issue is reported once."""
    hedged = bool(HEDGES.search(sentence))
    if PROOF_CLAIMS.search(sentence) and not NEGATION.search(sentence):
        return "presents a conclusion as proven."
    if CAUSAL_CLAIMS.search(sentence) and not hedged:
        return "states a cause as fact."
    if ATTRIBUTION_CLAIMS.search(sentence) and not (MATHEMATICAL_WORDING.search(sentence) or hedged):
        return "states a cause as fact instead of a mathematical relationship."
    if (
        ROOT_CAUSE.search(sentence)
        and not ROOT_CAUSE_UNKNOWN.search(sentence)
        and not SPECULATION.search(sentence)
    ):
        return "claims a root cause has been established."
    if NAMED_CAUSES.search(sentence) and not hedged and not _reports_missing_evidence(sentence):
        return "names a cause without evidence."
    if METRIC_CHANGE.search(sentence) and not hedged:
        return "claims a change in a metric that is not in the evidence."
    return None


def _check_claims(field, text):
    problems = filter(None, map(_sentence_problem, _sentences(text)))
    return [f"{field} {problem}" for problem in problems]


def _check_numbers(field, text, allowed):
    exact, whole_percents = allowed
    cleaned = strip_identifiers(text)
    issues = []
    for match in NUMBER_PATTERN.finditer(cleaned):
        value = parse_token(match.group())
        is_percent = cleaned[match.end():match.end() + 1] == "%"
        matches_exact = any(math.isclose(value, number, abs_tol=1e-9) for number in exact)
        if not matches_exact and not (is_percent and value in whole_percents):
            issues.append(f"{field} contains a number that is not in the evidence: {match.group()}.")
    return issues


def _check_text(field, text, allowed, claims=True):
    issues = _check_numbers(field, text, allowed)
    if not text.strip():
        issues.append(f"{field} is empty.")
    if claims:
        issues += _check_claims(field, text)
        if INVENTED_METRICS.search(text):
            issues.append(f"{field} uses a metric that is not in the evidence.")
    return issues


def _check_ids(field, ids, evidence):
    if not ids:
        return [f"{field} cites no evidence."]
    known = evidence.by_id
    return [f"{field} cites an unknown evidence ID: {item}." for item in ids if item not in known]


def _check_sequence(field, values):
    if sorted(values) != list(range(1, len(values) + 1)):
        return [f"{field} must be numbered 1 upward without gaps."]
    return []


def _has_direct_support(item, evidence):
    """Medium confidence needs a separate material metric, and enough purchase volume."""
    if any("directional" in fact.statement for fact in evidence.facts if fact.kind == "data_warning"):
        return False

    for fact_id in item.evidence_ids:
        fact = evidence.by_id.get(fact_id)
        if fact is None or fact.metric in (None, item.diagnostic_node, *PARENT_METRICS):
            continue
        if fact.data.get("status") not in (None, "stable", "undefined"):
            return True
        if fact.data.get("role") in ("adverse", "favorable"):
            return True
    return False


def _check_hypotheses(result, evidence, allowed):
    issues = []
    if len(result.hypotheses) > 3:
        issues.append("There are more than three hypotheses.")
    if evidence.needs_hypotheses and not result.hypotheses:
        issues.append("The diagnostic found a material issue, but no hypothesis was returned.")
    issues += _check_sequence("Hypothesis ranks", [item.rank for item in result.hypotheses])

    for item in result.hypotheses:
        field = f"Hypothesis {item.rank}"
        issues += _check_ids(field, item.evidence_ids, evidence)
        issues += _check_text(f"{field} mathematical evidence", item.mathematical_evidence, allowed)
        issues += _check_text(f"{field} business cause", item.unverified_business_cause, allowed)
        issues += _check_text(f"{field} uncertainty", item.uncertainty, allowed)
        if NAMED_CAUSES.search(item.mathematical_evidence):
            issues.append(f"{field} mathematical evidence names a business cause.")
        if not SPECULATION.search(item.unverified_business_cause):
            issues.append(f"{field} is not worded as a possibility.")
        if item.confidence != "low" and not _has_direct_support(item, evidence):
            issues.append(f"{field} has medium confidence without direct supporting evidence.")
        if item.diagnostic_node not in evidence.path_nodes:
            issues.append(f"{field} is about {item.diagnostic_node}, which is not on a diagnostic path.")
        elif evidence.metric_fact_id(item.diagnostic_node) not in item.evidence_ids:
            issues.append(f"{field} does not cite the metric fact for {item.diagnostic_node}.")
    return issues


def _check_data_needs(field, item, listed):
    """Keep uploaded checks to uploaded data, and list any outside data as needed."""
    text = " ".join(
        [item.investigation, item.what_to_check, item.expected_impact, item.if_supported, item.if_not_supported]
    )
    needed = [
        (name, pattern)
        for name, pattern in UNAVAILABLE_DATA.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]
    if item.check_source == "uploaded_data":
        return [f"{field} is marked as uploaded data but relies on {name}." for name, _ in needed]
    return [
        f"{field} relies on {name} without listing it as additional information needed."
        for name, pattern in needed
        if not re.search(pattern, listed, re.IGNORECASE)
    ]


def _check_order(recommendations):
    """Require checks on the uploaded data to come before external ones."""
    sources = [item.check_source for item in sorted(recommendations, key=lambda r: r.priority)]
    if not sources:
        return []
    if sources[0] != "uploaded_data":
        return ["Recommendations must start with a check using the uploaded data."]
    if sources != sorted(sources, reverse=True):
        return ["Checks using the uploaded data must come before external checks."]
    return []


def _check_recommendations(result, evidence, allowed):
    issues = _check_sequence("Recommendation priorities", [r.priority for r in result.recommendations])
    issues += _check_order(result.recommendations)
    ranks = {item.rank for item in result.hypotheses}
    listed = " ".join(need.information for need in result.additional_information)

    for item in result.recommendations:
        field = f"Recommendation {item.priority}"
        issues += _check_ids(field, item.evidence_ids, evidence)
        issues += _check_data_needs(field, item, listed)
        for label, text in (
            ("investigation", item.investigation),
            ("what to check", item.what_to_check),
            ("if-supported step", item.if_supported),
            ("if-not-supported step", item.if_not_supported),
            ("expected impact", item.expected_impact),
        ):
            issues += _check_text(f"{field} {label}", text, allowed)
        if len(_sentences(item.investigation)) > 1:
            issues.append(f"{field} must describe one investigation.")
        if item.hypothesis_rank not in ranks:
            issues.append(f"{field} is not linked to a hypothesis.")
        if item.target_metric not in evidence.metrics:
            issues.append(f"{field} targets a metric that is not in the evidence: {item.target_metric}.")

    missing = ranks - {item.hypothesis_rank for item in result.recommendations}
    issues += [f"Hypothesis {rank} has no recommendation." for rank in sorted(missing)]
    return issues


def validate_diagnosis(result, evidence):
    """Return a list of problems. An empty list means the response can be shown."""
    allowed = evidence.allowed_numbers()
    issues = _check_text("The confidence explanation", result.confidence_explanation, allowed)
    issues += _check_hypotheses(result, evidence, allowed)
    issues += _check_recommendations(result, evidence, allowed)

    for number, item in enumerate(result.additional_information, 1):
        field = f"Information need {number}"
        issues += _check_ids(field, item.evidence_ids, evidence)
        # Naming missing data is the point of this section, so metric names are allowed.
        issues += _check_text(field, item.information, allowed, claims=False)
        issues += _check_text(f"{field} reason", item.reason, allowed)
    return list(dict.fromkeys(issues))
