"""AI layer tests. Every model response is mocked, so no key or network is used."""

import pytest
from google.genai import errors
from pydantic import BaseModel

from funnel_magnet.ai import service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import (
    AIRequestError,
    AIResponseError,
    GeminiProvider,
    ProviderResponse,
)
from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import view

SAMPLES = view.load_samples()


class SampleItem(BaseModel):
    name: str
    target_metric: str


class SampleReply(BaseModel):
    """A small structured reply, standing in for any schema a provider is given."""

    items: list[SampleItem]
    note: str


def evidence_for(index=0, granularity=WEEKLY):
    result = load_meta_csv(SAMPLES[index].path)
    return build_evidence(run_diagnosis(result.data, granularity), result.currency)


def valid_reply(evidence=None):
    """A correct reply for the CPM inflation scenario."""
    evidence = evidence or evidence_for()
    cpm = evidence.metric_fact_id("cpm")
    return {
        "hypotheses": [
            {
                "rank": 1,
                "diagnostic_node": "cpm",
                "mathematical_evidence": "CPM rose and accounts for most of the ROAS movement.",
                "unverified_business_cause": "Delivery costs may have risen because of stronger competition in the auction.",
                "evidence_ids": [cpm, "D1"],
                "confidence": "low",
                "uncertainty": "The data does not include auction or competitor information.",
            }
        ],
        "recommendations": [
            {
                "priority": 1,
                "hypothesis_rank": 1,
                "check_source": "uploaded_data",
                "investigation": "Compare CPM for each campaign separately.",
                "what_to_check": "See whether the rise appears in every campaign or only one.",
                "if_supported": "Treat the rise as account-wide and move to the external check.",
                "if_not_supported": "Review recent settings changes in the affected campaign.",
                "target_metric": "cpm",
                "expected_direction": "unclear",
                "expected_impact": "This shows where to focus and does not change CPM by itself.",
                "evidence_ids": [cpm, "W2"],
            },
            {
                "priority": 2,
                "hypothesis_rank": 1,
                "check_source": "external_data",
                "investigation": "Review placement and audience delivery reports.",
                "what_to_check": "Find which delivery segments have the largest cost rise.",
                "if_supported": "Shift budget away from the most expensive delivery.",
                "if_not_supported": "Keep the current delivery mix and review bidding settings.",
                "target_metric": "cpm",
                "expected_direction": "decrease",
                "expected_impact": "Lower delivery costs would support ROAS if the hypothesis holds.",
                "evidence_ids": [cpm, "G1"],
            },
        ],
        "additional_information": [
            {
                "information": "Placement and audience delivery data.",
                "reason": "It would show where delivery costs changed.",
                "evidence_ids": ["G1"],
            }
        ],
        "confidence_explanation": "Confidence is low because the data shows what changed, not why.",
    }


class FakeProvider:
    name = "Fake"
    model = "fake-model"

    def __init__(self, text=None, error=None):
        self.text, self.error = text, error
        self.calls = []

    def generate(self, system_prompt, user_prompt, schema):
        self.calls.append((system_prompt, user_prompt, schema))
        if self.error:
            raise self.error
        return ProviderResponse(self.text, 100, 50)


# Evidence


def test_fact_ids_are_unique_and_cover_every_kind():
    evidence = evidence_for()
    ids = [fact.id for fact in evidence.facts]

    assert len(ids) == len(set(ids))
    assert {fact.kind for fact in evidence.facts} == {
        "mathematical_diagnosis",
        "metric",
        "mathematical_driver",
        "diagnostic_path",
        "data_warning",
        "information_gap",
    }


def test_evidence_includes_threshold_and_path():
    evidence = evidence_for()
    payload = evidence.payload()

    assert payload["context"]["materiality_threshold_percent"] == 5
    assert any(fact.get("nodes") == ["roas", "cpa", "cpc", "cpm"] for fact in payload["facts"])


def test_evidence_values_come_from_the_engine():
    result = load_meta_csv(SAMPLES[0].path)
    diagnosis = run_diagnosis(result.data, WEEKLY)
    evidence = build_evidence(diagnosis, result.currency)
    fact = evidence.by_id[evidence.metric_fact_id("roas")]

    assert fact.data["current"] == pytest.approx(diagnosis.changes["roas"].current, abs=1e-4)


# Validation


def test_digits_in_fact_ids_and_period_labels_are_not_evidence():
    exact, _ = evidence_for().allowed_numbers()

    assert 2026 not in exact and 35 not in exact


# Regressions from the first live response


# Separating Python's diagnosis from the model's interpretation


def test_uploaded_data_checks_are_offered_to_the_model():
    checks = evidence_for().context["checks_available_in_uploaded_data"]

    assert any("each campaign" in check for check in checks)


def test_single_campaign_view_does_not_offer_a_campaign_check():
    result = load_meta_csv(SAMPLES[0].path)
    name = result.data["campaign"].iloc[0]
    diagnosis = run_diagnosis(result.data, WEEKLY, campaign=name)

    evidence = build_evidence(diagnosis, result.currency, single_campaign=True)

    assert not any(
        "each campaign" in check for check in evidence.context["checks_available_in_uploaded_data"]
    )


# Recommendation structure


# Missing evidence and duplicate reporting


# Harmless differences are normalized, not rejected


# Factual problems are still rejected


# Conditional next steps


# Campaign data


def test_campaign_data_rule_needs_several_campaigns():
    result = load_meta_csv(SAMPLES[0].path)
    campaign = result.data["campaign"].iloc[0]
    evidence = build_evidence(run_diagnosis(result.data, WEEKLY, campaign=campaign), result.currency, (), True)

    assert not evidence.campaign_data_available


# Replay of the final live Gemini response, stored as a fixture


# Replay of the third live Gemini response, stored as a fixture


# Service


# Gemini adapter


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, **options):
        self.models = FakeModels(**options)


class FakeUsage:
    prompt_token_count = 11
    candidates_token_count = 22


class FakeResponse:
    usage_metadata = FakeUsage()

    def __init__(self, text):
        self.text = text


def test_gemini_requests_structured_json():
    client = FakeClient(response=FakeResponse('{"a": 1}'))

    reply = GeminiProvider("test", "gemini-test", client).generate("sys", "user", SampleReply)

    assert (reply.text, reply.input_tokens, reply.output_tokens) == ('{"a": 1}', 11, 22)
    kwargs = client.models.kwargs
    assert kwargs["model"] == "gemini-test"
    assert kwargs["contents"] == "user"
    assert kwargs["config"].system_instruction == "sys"
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_json_schema == SampleReply.model_json_schema()


@pytest.mark.parametrize(
    "error, expected",
    [
        (errors.ClientError(429, {"error": {"message": "x"}}), "usage limit"),
        (errors.ClientError(403, {"error": {"message": "x"}}), "rejected the API key"),
        (errors.ClientError(404, {"error": {"message": "x"}}), "model is not available"),
        (errors.ServerError(503, {"error": {"message": "x"}}), "having a problem"),
        (TimeoutError("timed out"), "could not be reached"),
    ],
)
def test_gemini_failures_give_clear_messages(error, expected):
    provider = GeminiProvider("test", "gemini-test", FakeClient(error=error))

    with pytest.raises(AIRequestError, match=expected):
        provider.generate("sys", "user", SampleReply)


def test_provider_errors_never_show_the_key():
    secret = "fake-key-abc123"
    error = RuntimeError(f"failed for https://example.test?key={secret}")
    provider = GeminiProvider(secret, "gemini-test", FakeClient(error=error))

    with pytest.raises(AIRequestError) as caught:
        provider.generate("sys", "user", SampleReply)

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_empty_gemini_reply_is_rejected():
    provider = GeminiProvider("test", "gemini-test", FakeClient(response=FakeResponse("")))

    with pytest.raises(AIResponseError, match="empty"):
        provider.generate("sys", "user", SampleReply)


# Settings


@pytest.fixture
def clean_environment(monkeypatch):
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TIER", "FM_SHARED_DEPLOYMENT", "FM_AI_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


ENABLED = {"FM_AI_ENABLED": "true"}


def test_ai_is_off_by_default(clean_environment):
    settings = service.load_settings()

    assert settings.api_key is None
    assert settings.model == "gemini-3.1-flash-lite"
    assert settings.tier == "free"
    assert settings.unavailable_reason == service.AI_OFF_MESSAGE


@pytest.mark.parametrize("value", [None, "false", "no", "1", ""])
def test_a_key_alone_never_enables_ai(clean_environment, value):
    secrets = {"GEMINI_API_KEY": "abc", "GEMINI_TIER": "paid", "FM_SHARED_DEPLOYMENT": "true"}
    if value is not None:
        secrets["FM_AI_ENABLED"] = value

    assert service.load_settings(secrets).unavailable_reason == service.AI_OFF_MESSAGE


def test_enabled_ai_still_needs_a_key(clean_environment):
    assert service.load_settings(ENABLED).unavailable_reason == service.NO_KEY_MESSAGE


def test_key_can_come_from_secrets(clean_environment):
    assert service.load_settings({**ENABLED, "GEMINI_API_KEY": "abc"}).unavailable_reason is None


def test_key_can_come_from_the_environment(clean_environment):
    clean_environment.setenv("GEMINI_API_KEY", "abc")

    assert service.load_settings().api_key == "abc"


def test_model_can_be_replaced(clean_environment):
    assert service.load_settings({"GEMINI_MODEL": "gemini-x"}).model == "gemini-x"


def test_shared_deployment_on_the_free_tier_is_blocked(clean_environment):
    settings = service.load_settings(
        {**ENABLED, "GEMINI_API_KEY": "abc", "FM_SHARED_DEPLOYMENT": "true"}
    )

    assert settings.unavailable_reason == service.SHARED_FREE_TIER_MESSAGE


def test_shared_deployment_on_the_paid_tier_is_allowed(clean_environment):
    settings = service.load_settings(
        {**ENABLED, "GEMINI_API_KEY": "abc", "FM_SHARED_DEPLOYMENT": "true", "GEMINI_TIER": "paid"}
    )

    assert settings.unavailable_reason is None


# Rendering


# Instructions to investigate versus claims about causes


# Checks the uploaded data can actually run


# Covering the key drivers


# Example lists and off-path recommendations


# Final Groq cycle: one testable cause, real data needs, matching confidence
