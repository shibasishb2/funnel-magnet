"""The structure the model must return."""

from typing import Literal

from pydantic import BaseModel

# High is left out on purpose. The evidence shows what changed, never why.
Confidence = Literal["low", "medium"]
Direction = Literal["increase", "decrease", "stabilize", "unclear"]


class Hypothesis(BaseModel):
    rank: int
    diagnostic_node: str
    statement: str
    evidence_ids: list[str]
    confidence: Confidence
    uncertainty: str


class Recommendation(BaseModel):
    priority: int
    action_type: Literal["investigate", "optimize"]
    action: str
    hypothesis_rank: int
    target_metric: str
    expected_direction: Direction
    expected_impact: str
    if_confirmed_next_step: str
    if_not_confirmed_next_step: str
    evidence_ids: list[str]


class InformationNeed(BaseModel):
    information: str
    reason: str
    evidence_ids: list[str]


class AIDiagnosis(BaseModel):
    executive_diagnosis: str
    executive_evidence_ids: list[str]
    hypotheses: list[Hypothesis]
    recommendations: list[Recommendation]
    additional_information: list[InformationNeed]
    confidence_explanation: str
