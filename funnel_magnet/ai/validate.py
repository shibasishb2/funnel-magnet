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
    r"\b(proves?|proven|proof|definitely|certainly|undoubtedly|guaranteed?|root cause)\b",
    re.IGNORECASE,
)
CAUSAL_CLAIMS = re.compile(
    r"\b(caused by|causing|because of|due to|as a result of|resulted (?:in|from)|led to|"
    r"the reason)\b",
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


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", text) if part.strip()]


def _check_claims(field, text):
    issues = []
    for sentence in _sentences(text):
        if PROOF_CLAIMS.search(sentence) and not NEGATION.search(sentence):
            issues.append(f"{field} presents a conclusion as proven.")
        if CAUSAL_CLAIMS.search(sentence) and not HEDGES.search(sentence):
            issues.append(f"{field} states a cause as fact.")
        if NAMED_CAUSES.search(sentence) and not HEDGES.search(sentence):
            issues.append(f"{field} names a cause without evidence.")
        if METRIC_CHANGE.search(sentence) and not HEDGES.search(sentence):
            issues.append(f"{field} claims a change in a metric that is not in the evidence.")
    return issues


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
        issues += _check_text(field, item.statement, allowed)
        issues += _check_text(f"{field} uncertainty", item.uncertainty, allowed)
        if not SPECULATION.search(item.statement):
            issues.append(f"{field} is not worded as a possibility.")
        if item.diagnostic_node not in evidence.path_nodes:
            issues.append(f"{field} is about {item.diagnostic_node}, which is not on a diagnostic path.")
        elif evidence.metric_fact_id(item.diagnostic_node) not in item.evidence_ids:
            issues.append(f"{field} does not cite the metric fact for {item.diagnostic_node}.")
    return issues


def _check_recommendations(result, evidence, allowed):
    issues = _check_sequence("Recommendation priorities", [r.priority for r in result.recommendations])
    ranks = {item.rank for item in result.hypotheses}

    for item in result.recommendations:
        field = f"Recommendation {item.priority}"
        issues += _check_ids(field, item.evidence_ids, evidence)
        issues += _check_text(field, item.action, allowed)
        issues += _check_text(f"{field} expected impact", item.expected_impact, allowed)
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
    issues = _check_ids("The executive diagnosis", result.executive_evidence_ids, evidence)
    issues += _check_text("The executive diagnosis", result.executive_diagnosis, allowed)
    issues += _check_text("The confidence explanation", result.confidence_explanation, allowed)
    issues += _check_hypotheses(result, evidence, allowed)
    issues += _check_recommendations(result, evidence, allowed)

    for number, item in enumerate(result.additional_information, 1):
        field = f"Information need {number}"
        issues += _check_ids(field, item.evidence_ids, evidence)
        # Naming missing data is the point of this section, so metric names are allowed.
        issues += _check_text(field, item.information, allowed, claims=False)
        issues += _check_text(f"{field} reason", item.reason, allowed)
    return issues
