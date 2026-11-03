"""Sentence checks for AI wording: unsupported numbers and unhedged causal claims.

prioritise.py runs these on every explanation the model writes. A text that fails is
withheld, never shown.
"""

import math
import re

from funnel_magnet.ai.evidence import NUMBER_PATTERN, parse_token, strip_identifiers

# Models often write "ad‑level" with a non-breaking hyphen, which plain patterns miss.
HYPHENS = str.maketrans({char: "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2212"})


def plain_hyphens(text):
    return text.translate(HYPHENS)

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


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?;])\s+", plain_hyphens(text)) if part.strip()]


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
