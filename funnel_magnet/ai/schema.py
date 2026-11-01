"""The structure the model must return.

There is no diagnosis field, because Python writes the mathematical diagnosis.
"""

from typing import Literal

from pydantic import BaseModel

# High is left out on purpose. The evidence shows what changed, never why.
Confidence = Literal["low", "medium"]
Direction = Literal["increase", "decrease", "stabilize", "unclear"]
CheckSource = Literal["uploaded_data", "external_data"]


class Hypothesis(BaseModel):
    rank: int
    diagnostic_node: str
    mathematical_evidence: str
    unverified_business_cause: str
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
    target_metric: str
    expected_direction: Direction
    expected_impact: str
    evidence_ids: list[str]


class InformationNeed(BaseModel):
    information: str
    reason: str
    evidence_ids: list[str]


class AIDiagnosis(BaseModel):
    hypotheses: list[Hypothesis]
    recommendations: list[Recommendation]
    additional_information: list[InformationNeed]
    confidence_explanation: str
