"""Find factual problems in model output. Harmless differences go to normalize.py.

Problems are invalid evidence IDs, unsupported numbers, invented metrics, definite
causal claims and breaks from the methodology. A section with a problem is withheld,
and the sections that pass can still be shown.
"""

import math
import re

from funnel_magnet.ai.evidence import NUMBER_PATTERN, parse_token, strip_identifiers

HEDGES = re.compile(
    r"\b(may|might|could|would|should|possibly|possible|potential|potentially|perhaps|whether|if|"
    r"hypothesis|unknown|unconfirmed|unverified|not|cannot|impossible|check|review|compare|test|"
    r"investigate|assess|examine|analy[sz]e|explore|identify|determine|consider|such as|like)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(not|cannot|no|impossible)\b", re.IGNORECASE)
PROOF_CLAIMS = re.compile(
    r"\b(proves?|proven|proof|definitely|certainly|undoubtedly|guaranteed?)\b", re.IGNORECASE
)
CAUSAL_CLAIMS = re.compile(
    r"\b(caused by|causing|because of|due to|as a result of|resulted (?:in|from)|led to|"
    r"the reason)\b",
    re.IGNORECASE,
)
# Only a positive claim counts. "The root cause is unknown" says the opposite.
ROOT_CAUSE_CLAIMS = re.compile(
    r"\broot cause(?: of\b[^.;]*?)?\s+(?:is|was|lies|appears to be)\b"
    r"(?!\s+(?:un\w+|not|still|yet|to be|being))"
    r"|\b(?:found|identified|established|confirmed|determined|located)\s+(?:the\s+)?root cause\b",
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
INVENTED_METRICS = re.compile(r"\b(LTV|CLV|CLTV|CAC|MER|ARPU|NPS|CPL|CPI|CPV|CPE|POAS|ACOS)\b")
METRIC_CHANGE = re.compile(
    r"(frequency|reach|bounce rate|relevance score|quality rank\w*|engagement rate)"
    r"\b.{0,40}\b(rose|risen|fell|fallen|increased|decreased|declined|dropped|grew|spiked|worsened|improved)\b",
    re.IGNORECASE,
)
COMBINED_CAUSES = re.compile(r"\bor\b", re.IGNORECASE)
CAMPAIGN_DATA = re.compile(r"\bcampaign[- ](?:level|split|breakdown)|\b(?:by|per|each) campaign\b", re.IGNORECASE)


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", text) if part.strip()]


def _reports_missing_evidence(sentence):
    return bool(MISSING_EVIDENCE.search(sentence)) and not (
        ASSERTED_CAUSE.search(sentence) or CAUSAL_CLAIMS.search(sentence)
    )


def _sentence_problem(sentence, conditional=False):
    """Return the first problem in a sentence, so one wording issue is reported once.

    A conditional step only applies if its check holds, so naming a cause there
    asserts nothing. Definite causal claims are still rejected.
    """
    hedged = bool(HEDGES.search(sentence))
    if PROOF_CLAIMS.search(sentence) and not NEGATION.search(sentence):
        return "presents a conclusion as proven."
    if CAUSAL_CLAIMS.search(sentence) and not hedged:
        return "states a cause as fact."
    if ROOT_CAUSE_CLAIMS.search(sentence) and not NEGATION.search(sentence):
        return "claims a root cause has been established."
    if (
        NAMED_CAUSES.search(sentence)
        and not conditional
        and not hedged
        and not _reports_missing_evidence(sentence)
    ):
        return "names a cause as fact."
    if METRIC_CHANGE.search(sentence) and not hedged:
        return "claims a change in a metric that is not in the evidence."
    return None


def _check_claims(field, text, conditional=False):
    problems = [_sentence_problem(sentence, conditional) for sentence in _sentences(text)]
    issues = [f"{field} {problem}" for problem in problems if problem]
    if INVENTED_METRICS.search(text):
        issues.append(f"{field} uses a metric that is not in the evidence.")
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


def _check_text(field, text, allowed, claims=True, conditional=False):
    if not text.strip():
        return [f"{field} is empty."]
    issues = _check_numbers(field, text, allowed)
    if claims:
        issues += _check_claims(field, text, conditional)
    return issues


def _check_availability(field, text, evidence):
    """Campaign data is in the upload, so it can be unchecked but never missing."""
    if not evidence.campaign_data_available:
        return []
    denied = any(
        MISSING_EVIDENCE.search(sentence) and CAMPAIGN_DATA.search(sentence)
        for sentence in _sentences(text)
    )
    return [f"{field} describes campaign-level data as missing, but it is available."] if denied else []


def _check_ids(field, ids, evidence):
    if not ids:
        return [f"{field} cites no evidence."]
    known = evidence.by_id
    return [f"{field} cites an unknown evidence ID: {item}." for item in ids if item not in known]


def _explanation_issues(text, evidence, allowed):
    field = "The confidence explanation"
    return _check_text(field, text, allowed) + _check_availability(field, text, evidence)


def _hypothesis_issues(item, evidence, allowed):
    field = f"Hypothesis {item.rank}"
    issues = _check_ids(field, item.evidence_ids, evidence)
    issues += _check_text(f"{field} mathematical evidence", item.mathematical_evidence, allowed)
    issues += _check_text(f"{field} business cause", item.unverified_business_cause, allowed)
    issues += _check_text(f"{field} uncertainty", item.uncertainty, allowed)
    issues += _check_availability(f"{field} uncertainty", item.uncertainty, evidence)
    if NAMED_CAUSES.search(item.mathematical_evidence):
        issues.append(f"{field} mathematical evidence names a business cause.")
    if COMBINED_CAUSES.search(item.unverified_business_cause):
        issues.append(f"{field} combines more than one business cause.")
    if item.diagnostic_node not in evidence.path_nodes:
        issues.append(f"{field} is about {item.diagnostic_node}, which is not on a diagnostic path.")
    return issues


def _recommendation_issues(item, ranks, evidence, allowed):
    field = f"Recommendation {item.priority}"
    issues = _check_ids(field, item.evidence_ids, evidence)
    for label, text in (
        ("investigation", item.investigation),
        ("what to check", item.what_to_check),
        ("expected impact", item.expected_impact),
    ):
        issues += _check_text(f"{field} {label}", text, allowed)
    for label, text in (
        ("if-supported step", item.if_supported),
        ("if-not-supported step", item.if_not_supported),
    ):
        issues += _check_text(f"{field} {label}", text, allowed, conditional=True)
    if item.hypothesis_rank not in ranks:
        issues.append(f"{field} is not linked to a hypothesis.")
    if item.target_metric not in evidence.metrics:
        issues.append(f"{field} targets a metric that is not in the evidence: {item.target_metric}.")
    return issues


def _need_issues(number, item, evidence, allowed):
    field = f"Information need {number}"
    issues = _check_ids(field, item.evidence_ids, evidence)
    # Naming missing data is the point of this section, so metric names are allowed.
    issues += _check_text(field, item.information, allowed, claims=False)
    issues += _check_text(f"{field} reason", item.reason, allowed)
    issues += _check_availability(field, f"Missing data: {item.information}", evidence)
    return issues


def validate_diagnosis(result, evidence):
    """Return a list of problems. An empty list means every section can be shown."""
    allowed = evidence.allowed_numbers()
    ranks = {item.rank for item in result.hypotheses}
    issues = _explanation_issues(result.confidence_explanation, evidence, allowed)
    if evidence.needs_hypotheses and not result.hypotheses:
        issues.append("The diagnostic found a material issue, but no hypothesis was returned.")
    for item in result.hypotheses:
        issues += _hypothesis_issues(item, evidence, allowed)
    for item in result.recommendations:
        issues += _recommendation_issues(item, ranks, evidence, allowed)
    for number, item in enumerate(result.additional_information, 1):
        issues += _need_issues(number, item, evidence, allowed)
    return list(dict.fromkeys(issues))


def keep_valid_sections(result, evidence):
    """Return the result with every section that has a problem removed.

    Ranks and priorities stay as they were, so the reported problems still match.
    """
    allowed = evidence.allowed_numbers()
    ranks = {item.rank for item in result.hypotheses}
    explanation = result.confidence_explanation
    return result.model_copy(
        update={
            "hypotheses": [
                item for item in result.hypotheses if not _hypothesis_issues(item, evidence, allowed)
            ],
            "recommendations": [
                item
                for item in result.recommendations
                if not _recommendation_issues(item, ranks, evidence, allowed)
            ],
            "additional_information": [
                item
                for number, item in enumerate(result.additional_information, 1)
                if not _need_issues(number, item, evidence, allowed)
            ],
            "confidence_explanation": ""
            if _explanation_issues(explanation, evidence, allowed)
            else explanation,
        }
    )
