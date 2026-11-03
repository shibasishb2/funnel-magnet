"""The structure the model must return.

There is no diagnosis field, because Python writes the mathematical diagnosis.
"""

from typing import Literal

from pydantic import BaseModel, Field

# High is left out on purpose. The evidence shows what changed, never why.
Confidence = Literal["low", "medium"]
Direction = Literal["increase", "decrease", "stabilize", "unclear"]
CheckSource = Literal["uploaded_data", "external_data"]


ONE_CAUSE = (
    "Exactly one possible business cause, hedged, that a person could test. Valid: "
    "\"Creative fatigue may have contributed to lower CTR.\" Also valid, as a separate "
    "hypothesis: \"Audience saturation may have contributed to lower CTR.\" Invalid: "
    "\"Creative fatigue or audience saturation may have contributed to lower CTR.\" "
    "Invalid, because it only restates the arithmetic: \"ROAS fell because CPA rose.\""
)


class Hypothesis(BaseModel):
    rank: int
    diagnostic_node: str = Field(
        description="The internal name of a metric on a diagnostic path, such as "
        "link_clicks_to_landing_page_views. Several hypotheses may share it, one cause each."
    )
    mathematical_evidence: str
    unverified_business_cause: str = Field(description=ONE_CAUSE)
    evidence_ids: list[str]
    confidence: Confidence
    uncertainty: str


class Recommendation(BaseModel):
    priority: int
    hypothesis_rank: int
    check_source: CheckSource
    investigation: str
    what_to_check: str
    if_supported: str
    if_not_supported: str
    target_metric: str = Field(
        description="The internal name of the metric in the evidence that this check helps explain. "
        "A check of page load time explains link_clicks_to_landing_page_views. Never name an "
        "outside measure, such as page load time, here. Describe it in what_to_check."
    )
    expected_direction: Direction
    expected_impact: str
    evidence_ids: list[str]


class InformationNeed(BaseModel):
    information: str = Field(
        description="Evidence that is missing from the upload, such as \"Frequency and creative "
        "launch dates.\" Never a metric, driver or warning already in the evidence."
    )
    reason: str
    evidence_ids: list[str]


def restricted_schema(evidence):
    """Return the JSON schema for this evidence, or None when no value would be valid.

    diagnostic_node may only be a node on the diagnostic paths, and target_metric only a
    metric in the evidence, both by internal name. This stops a model inventing names
    such as landing_page_load_time. The validator still checks every reply in full.
    """
    nodes, metrics = sorted(evidence.path_nodes), sorted(evidence.metrics)
    if not nodes or not metrics:
        return None
    schema = AIDiagnosis.model_json_schema()
    schema["$defs"]["Hypothesis"]["properties"]["diagnostic_node"]["enum"] = nodes
    schema["$defs"]["Recommendation"]["properties"]["target_metric"]["enum"] = metrics
    return schema


class AIDiagnosis(BaseModel):
    hypotheses: list[Hypothesis]
    recommendations: list[Recommendation]
    additional_information: list[InformationNeed]
    confidence_explanation: str
