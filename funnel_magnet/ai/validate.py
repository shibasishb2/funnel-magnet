"""Find factual problems in model output. Harmless differences go to normalize.py.

Problems are invalid evidence IDs, unsupported numbers, invented metrics, definite
causal claims and breaks from the methodology. A section with a problem is withheld,
and the sections that pass can still be shown.
"""

import math
import re

from funnel_magnet.ai.evidence import NUMBER_PATTERN, parse_token, strip_identifiers
from funnel_magnet.ai.normalize import plain_hyphens
from funnel_magnet.ui import view

# Words that make a statement uncertain. Instruction verbs such as "check" are not
# hedges: "Check CPM because of fatigue" still asserts fatigue.
HEDGES = re.compile(
    r"\b(may|might|could|would|should|possibly|possible|potential|potentially|perhaps|whether|if|"
    r"hypothesis|unknown|unconfirmed|unverified|not|cannot|impossible|such as|like)\b",
    re.IGNORECASE,
)
# A sentence that starts with one of these asks for an investigation. Naming a
# possible cause as the thing to look into is not a claim that it happened.
INSTRUCTION = re.compile(
    r"^\W*(?:obtain|collect|gather|request|pull|export|compare|review|check|verify|"
    r"analy[sz]e|examine|investigate|assess|audit|inspect|look (?:at|into)|measure|"
    r"segment|break down|test|run|monitor|track|confirm|identify|explore)\b",
    re.IGNORECASE,
)
# A hedge counts for a causal link only before it, or in the two words after it.
HEDGE_AFTER_LINK = re.compile(r"^\W*(?:\w+\W+){0,2}?(possible|possibly|potential|potentially|a likely)\b", re.IGNORECASE)
POSSIBLE_CAUSE_PREFIX = "Possible cause: "
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
    r"\broot cause(?: of\b[^.;]*?)?\s+(?:is|was|lies|appears to be|remains|sits)\b"
    r"(?!\s+(?:un\w+|not|still|yet|to be|being|external|outside|beyond|open))"
    r"|\b(?:is|was|are|were)\s+the\s+root cause\b"
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
# "Checkout friction, such as slow forms or payment errors" gives examples of one cause.
# The list is ignored only when counting causes. Every other check still reads it.
EXAMPLE_LIST = re.compile(r"\bsuch as\b[^.;)]*", re.IGNORECASE)
CAMPAIGN_DATA = re.compile(r"\bcampaign[- ](?:level|split|breakdown)|\b(?:by|per|each) campaign\b", re.IGNORECASE)
# Words that only describe the metrics and how they moved. A business cause made of
# nothing else restates the arithmetic instead of proposing something to test.
ARITHMETIC_WORDS = frozenset(
    """
    roas aov cpa cpc cvr cpm ctr return ad ads spend spent cost costs per click clicks link
    impression impressions thousand order orders value average conversion conversions rate
    rates funnel stage stages landing page pages view views add adds cart carts checkout
    checkouts initiated purchase purchases delivery metric metrics revenue sales ratio
    rise rose risen rising fall fell fallen falling increase increased increases increasing
    decrease decreased decreases decline declined declines drop dropped drops improve
    improved improvement improvements worsen worsened worsening deteriorated deterioration
    change changed changes shift shifted higher lower up down offset offsets offsetting
    contributed contribute contributes contribution driven drove drive drives explained
    explains explain led reduced reduce reducing raised raise raising lowered lowering boost
    boosted hurt pushed pulled weighed lift lifted gain gained loss
    slight slightly small large overall partial partially modest material materially
    significant significantly
    the a an in of to for by on at and or but with from its their this that these those
    which may might could have has had be been being is was were are possibly possible
    potentially perhaps also some more less than between period periods week weeks month
    months current prior previous as it so not because due result results reflect reflects
    """.split()
)
DATA_NEED_WORDS = re.compile(
    r"\b(data|records?|reports?|logs?|information|details|benchmarks?|breakdowns?|history|"
    r"dates|figures|analytics|tests?|surveys?|feedback|counts|exports?|change ?logs?|levels?|"
    r"reasons|timestamps)\b",
    re.IGNORECASE,
)
# "Both hypotheses" is stale once one of them is withheld.
PLURAL_HYPOTHESES = re.compile(r"\b(?:both|all|two|three|each)\b[^.;]{0,20}\bhypothes", re.IGNORECASE)
STATED_LEVEL = re.compile(
    r"\b(medium|high)\b[^.;]{0,40}\bconfidence\b|\bconfidence\b[^.;]{0,40}\b(medium|high)\b",
    re.IGNORECASE,
)

# The uploaded data supports only these checks. Anything else either needs outside
# data or repeats the two-period comparison the app has already calculated.
CAMPAIGN_CHECK = re.compile(r"\bcampaigns?\b", re.IGNORECASE)
EARLIER_PERIODS_CHECK = re.compile(
    r"\b(?:earlier|previous|prior|past|preceding|several|recent|last \w+)\s+(?:weeks|months|periods)\b"
    r"|\btrend\w*\b|\bover time\b|\b(?:week|month)[- ]by[- ](?:week|month)\b",
    re.IGNORECASE,
)


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", plain_hyphens(text)) if part.strip()]


def _restates_arithmetic(cause):
    words = re.findall(r"[a-z]+", plain_hyphens(cause).lower())
    return all(word in ARITHMETIC_WORDS for word in words)


def _misstated_level(text, levels):
    """True when the explanation claims a level no shown hypothesis has."""
    for sentence in _sentences(text):
        match = STATED_LEVEL.search(sentence)
        if match and not NEGATION.search(sentence) and (match.group(1) or match.group(2)).lower() not in levels:
            return True
    return False


def _reports_missing_evidence(sentence):
    return bool(MISSING_EVIDENCE.search(sentence)) and not (
        ASSERTED_CAUSE.search(sentence) or CAUSAL_CLAIMS.search(sentence)
    )


def _hedged_link(sentence, link):
    """A causal link is hedged only by uncertainty before it or right after it."""
    return bool(HEDGES.search(sentence[: link.start()]) or HEDGE_AFTER_LINK.search(sentence[link.end():]))


def _sentence_problem(sentence, conditional=False):
    """Return the first problem in a sentence, so one wording issue is reported once.

    A conditional step only applies if its check holds, so naming a cause there
    asserts nothing. An instruction to investigate a possible cause asserts nothing
    either. Definite causal claims are rejected in both.
    """
    hedged = bool(HEDGES.search(sentence))
    instruction = bool(INSTRUCTION.search(sentence))
    if PROOF_CLAIMS.search(sentence) and not NEGATION.search(sentence):
        return "presents a conclusion as proven."
    if any(not _hedged_link(sentence, link) for link in CAUSAL_CLAIMS.finditer(sentence)):
        return "states a cause as fact."
    if ROOT_CAUSE_CLAIMS.search(sentence) and not NEGATION.search(sentence):
        return "claims a root cause has been established."
    if (
        NAMED_CAUSES.search(sentence)
        and not conditional
        and not hedged
        and not (instruction and not ASSERTED_CAUSE.search(sentence))
        and not _reports_missing_evidence(sentence)
    ):
        return "names a cause as fact."
    if METRIC_CHANGE.search(sentence) and not (hedged or instruction):
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


def _explanation_issues(text, evidence, allowed, shown=None, returned=0):
    """Check the explanation against the hypotheses that will actually be shown."""
    field = "The confidence explanation"
    shown = [] if shown is None else shown
    issues = _check_text(field, text, allowed) + _check_availability(field, text, evidence)
    if _misstated_level(text, {item.confidence for item in shown} or {"low"}):
        issues.append(f"{field} states a confidence level that no validated hypothesis has.")
    if len(shown) < returned and (PLURAL_HYPOTHESES.search(text) or (not shown and "hypothes" in text.lower())):
        issues.append(f"{field} refers to hypotheses that were withheld.")
    return issues


def _shown_hypotheses(result, evidence, allowed):
    """The hypotheses that pass their own checks, which are the ones shown."""
    return [item for item in result.hypotheses if not _hypothesis_issues(item, evidence, allowed)]


def _hypothesis_issues(item, evidence, allowed):
    field = f"Hypothesis {item.rank}"
    issues = _check_ids(field, item.evidence_ids, evidence)
    issues += _check_text(f"{field} mathematical evidence", item.mathematical_evidence, allowed)
    # The app's "Possible cause:" label marks the field, but the cause itself must
    # still be worded as possible: "AOV rose due to X" is a claim either way.
    cause = item.unverified_business_cause.removeprefix(POSSIBLE_CAUSE_PREFIX)
    issues += _check_text(f"{field} business cause", cause, allowed)
    issues += _check_text(f"{field} uncertainty", item.uncertainty, allowed)
    issues += _check_availability(f"{field} uncertainty", item.uncertainty, evidence)
    if NAMED_CAUSES.search(item.mathematical_evidence):
        issues.append(f"{field} mathematical evidence names a business cause.")
    if COMBINED_CAUSES.search(EXAMPLE_LIST.sub("", cause)):
        issues.append(f"{field} combines more than one business cause.")
    if _restates_arithmetic(cause):
        issues.append(f"{field} restates the metric changes without proposing a testable cause.")
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
    elif item.target_metric not in evidence.path_nodes:
        issues.append(f"{field} targets {item.target_metric}, which is not on a diagnostic path.")
    if item.check_source == "uploaded_data" and not _available_check(item, evidence):
        issues.append(
            f"{field} is labelled as using the uploaded data, but it repeats a comparison the app "
            "has already calculated or needs data the upload doesn't have."
        )
    return issues


def _available_check(item, evidence):
    text = plain_hyphens(f"{item.investigation} {item.what_to_check}")
    if evidence.campaign_data_available and CAMPAIGN_CHECK.search(text):
        return True
    return bool(EARLIER_PERIODS_CHECK.search(text))


def _key_drivers(evidence):
    """Return [(metric, description)] for the main driver and the largest offsetting one."""
    drivers = [fact for fact in evidence.facts if fact.kind == "mathematical_driver"]
    wanted = []
    main = next((f for f in drivers if f.data.get("effect_on_roas") in ("positive contributor", "adverse contributor")), None)
    offset = next((f for f in drivers if f.data.get("effect_on_roas") == "offsetting driver"), None)
    if main:
        wanted.append((main.metric, "the main driver"))
    if offset:
        wanted.append((offset.metric, "the largest offsetting driver"))
    return wanted


def coverage_issues(result, evidence):
    """Report a key driver that no shown hypothesis or recommendation addresses.

    This flags a gap without withholding anything, because the sections shown
    still passed their own checks.
    """
    if not evidence.needs_hypotheses:
        return []
    addressed = {item.diagnostic_node for item in result.hypotheses}
    addressed |= {item.target_metric for item in result.recommendations}
    return [
        f"The AI output doesn't address {view.metric_label(metric)}, {description}."
        for metric, description in _key_drivers(evidence)
        if metric not in addressed
    ]


def _need_issues(number, item, evidence, allowed):
    field = f"Information need {number}"
    issues = _check_ids(field, item.evidence_ids, evidence)
    # Naming missing data is the point of this section, so metric names are allowed.
    issues += _check_text(field, item.information, allowed, claims=False)
    if not DATA_NEED_WORDS.search(plain_hyphens(item.information)):
        issues.append(f"{field} doesn't name missing evidence. It may repeat a metric or warning.")
    issues += _check_text(f"{field} reason", item.reason, allowed)
    issues += _check_availability(field, f"Missing data: {item.information}", evidence)
    return issues


def validate_diagnosis(result, evidence):
    """Return a list of problems. An empty list means every section can be shown."""
    allowed = evidence.allowed_numbers()
    ranks = {item.rank for item in result.hypotheses}
    issues = _explanation_issues(
        result.confidence_explanation,
        evidence,
        allowed,
        _shown_hypotheses(result, evidence, allowed),
        len(result.hypotheses),
    )
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
            if _explanation_issues(
                explanation,
                evidence,
                allowed,
                _shown_hypotheses(result, evidence, allowed),
                len(result.hypotheses),
            )
            else explanation,
        }
    )
