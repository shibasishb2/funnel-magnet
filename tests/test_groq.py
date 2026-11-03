"""Groq provider tests. Every response is mocked, so no key or network is used."""

import json
from pathlib import Path

import httpx
import pytest

from funnel_magnet.ai import service
from funnel_magnet.ai.providers import (
    GROQ_MAX_COMPLETION_TOKENS,
    GROQ_URL,
    AIRequestError,
    AIResponseError,
    GeminiProvider,
    GroqProvider,
    inline_refs,
    strict_schema,
)
from funnel_magnet.ai.schema import AIDiagnosis, restricted_schema
from test_ai import evidence_for, valid_reply

FAKE_KEY = "gsk_test_only_not_a_real_key"


class FakeGroq:
    """Answers each request from a list, recording what was sent."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def completion(content, finish_reason="stop", usage=None):
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 3300, "completion_tokens": 700},
    }
    return httpx.Response(200, json=body)


def provider_with(*responses):
    fake = FakeGroq(*responses)
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return GroqProvider(FAKE_KEY, "openai/gpt-oss-20b", client), fake


def generate(*responses):
    provider, fake = provider_with(*responses)
    return provider.generate("system", "user", AIDiagnosis), fake


# Request


def test_request_uses_strict_structured_output():
    _, fake = generate(completion(json.dumps(valid_reply())))
    request = fake.requests[0]
    body = json.loads(request.content)

    assert str(request.url) == GROQ_URL
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_completion_tokens"] == GROQ_MAX_COMPLETION_TOKENS
    assert body["include_reasoning"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_key_travels_only_in_the_authorization_header():
    _, fake = generate(completion(json.dumps(valid_reply())))
    request = fake.requests[0]

    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in request.content.decode()


def walk(schema):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from walk(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from walk(item)


def test_strict_schema_requires_every_field_and_allows_no_extras():
    schema = strict_schema(AIDiagnosis.model_json_schema())
    objects = [node for node in walk(schema) if node.get("type") == "object"]

    assert len(objects) == 4
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
    assert not any("title" in node for node in walk(schema) if "type" in node)


def test_strict_schema_keeps_a_field_named_title():
    schema = {"type": "object", "title": "Thing", "properties": {"title": {"type": "string", "title": "Title"}}}

    assert strict_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "additionalProperties": False,
        "required": ["title"],
    }


# Responses


def test_reply_and_usage_are_returned():
    response, _ = generate(completion('{"x": 1}', usage={"prompt_tokens": 3400, "completion_tokens": 650}))

    assert (response.text, response.input_tokens, response.output_tokens) == ('{"x": 1}', 3400, 650)


def test_groq_reply_goes_through_the_same_validation():
    provider, _ = provider_with(completion(json.dumps(valid_reply())))

    result = service.generate_diagnosis(evidence_for(), provider)

    assert (result.provider, result.model) == ("Groq", "openai/gpt-oss-20b")
    assert result.withheld == []
    assert result.diagnosis.hypotheses[0].diagnostic_node == "cpm"


def test_groq_reply_with_an_unsupported_number_is_withheld():
    reply = valid_reply()
    reply["hypotheses"][0]["uncertainty"] = "Costs rose by 77% in one day."
    provider, _ = provider_with(completion(json.dumps(reply)))

    result = service.generate_diagnosis(evidence_for(), provider)

    assert result.diagnosis.hypotheses == []
    assert any("77" in issue for issue in result.withheld)


def test_reply_that_is_not_the_schema_is_rejected():
    provider, _ = provider_with(completion('{"summary": "CPM rose"}'))

    with pytest.raises(AIResponseError, match="required format"):
        service.generate_diagnosis(evidence_for(), provider)


@pytest.mark.parametrize(
    "response, message",
    [
        (completion('{"x": 1', finish_reason="length"), "cut off"),
        (completion(""), "empty"),
        (completion(None), "empty"),
        (httpx.Response(200, text="<html>not json</html>"), "could not be read"),
        (httpx.Response(200, json={"choices": []}), "could not be read"),
        (httpx.Response(400, json={"error": {"code": "json_validate_failed"}}), "required format"),
    ],
)
def test_unusable_replies_raise_a_response_error(response, message):
    with pytest.raises(AIResponseError, match=message):
        generate(response)


# Failures


def test_rate_limit_reports_the_wait_and_is_not_retried():
    with pytest.raises(AIRequestError, match="about 23 seconds") as caught:
        generate(httpx.Response(429, headers={"retry-after": "22.6"}, json={"error": {}}))

    assert "limit" in str(caught.value)


def test_rate_limit_is_requested_once_only():
    provider, fake = provider_with(httpx.Response(429, json={"error": {}}), completion("{}"))

    with pytest.raises(AIRequestError, match="usage limit"):
        provider.generate("system", "user", AIDiagnosis)
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "status, message",
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (404, "model is not available"),
        (413, "token limit"),
        (400, "rejected the request"),
        (500, "having a problem"),
        (503, "having a problem"),
    ],
)
def test_http_failures_give_clear_messages(status, message):
    body = {"error": {"message": f"Invalid API Key {FAKE_KEY}"}}
    with pytest.raises(AIRequestError, match=message) as caught:
        generate(httpx.Response(status, json=body))

    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize(
    "error, message",
    [
        (httpx.ReadTimeout("slow"), "did not respond in time"),
        (httpx.ConnectError("offline"), "could not be reached"),
    ],
)
def test_network_failures_give_clear_messages(error, message):
    with pytest.raises(AIRequestError, match=message):
        generate(error)


# Settings


@pytest.fixture
def clean_environment(monkeypatch):
    for name in (
        "AI_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL",
        "GEMINI_TIER", "FM_SHARED_DEPLOYMENT", "FM_AI_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


ENABLED_GROQ = {"FM_AI_ENABLED": "true", "AI_PROVIDER": "groq"}


def test_gemini_stays_the_default_provider(clean_environment):
    settings = service.load_settings({"FM_AI_ENABLED": "true", "GEMINI_API_KEY": "abc"})

    assert settings.provider == "gemini"
    assert isinstance(service.create_provider(settings), GeminiProvider)


def test_groq_key_can_come_from_the_environment(clean_environment):
    clean_environment.setenv("GROQ_API_KEY", FAKE_KEY)
    settings = service.load_settings(ENABLED_GROQ)

    assert settings.api_key == FAKE_KEY
    assert settings.model == "openai/gpt-oss-120b"
    assert settings.unavailable_reason is None
    assert isinstance(service.create_provider(settings), GroqProvider)


def test_groq_key_can_come_from_secrets(clean_environment):
    settings = service.load_settings({**ENABLED_GROQ, "GROQ_API_KEY": FAKE_KEY, "GROQ_MODEL": "other/model"})

    assert (settings.api_key, settings.model) == (FAKE_KEY, "other/model")


def test_groq_never_uses_the_gemini_key(clean_environment):
    settings = service.load_settings({**ENABLED_GROQ, "GEMINI_API_KEY": "abc"})

    assert settings.api_key is None
    assert settings.unavailable_reason == service.NO_KEY_MESSAGE


def test_groq_stays_off_until_ai_is_enabled(clean_environment):
    settings = service.load_settings({"AI_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY})

    assert settings.unavailable_reason == service.AI_OFF_MESSAGE


def test_groq_is_blocked_in_a_shared_deployment(clean_environment):
    settings = service.load_settings({**ENABLED_GROQ, "GROQ_API_KEY": FAKE_KEY, "FM_SHARED_DEPLOYMENT": "true"})

    assert settings.unavailable_reason == service.SHARED_GROQ_MESSAGE


def test_unknown_provider_is_reported(clean_environment):
    settings = service.load_settings({"FM_AI_ENABLED": "true", "AI_PROVIDER": "other", "GROQ_API_KEY": FAKE_KEY})

    assert settings.unavailable_reason == service.UNKNOWN_PROVIDER_MESSAGE


def test_usage_note_names_the_provider(clean_environment):
    assert "Groq" in service.load_settings(ENABLED_GROQ).usage_note
    assert "Gemini" in service.load_settings({"FM_AI_ENABLED": "true"}).usage_note
    assert service.load_settings({"GEMINI_TIER": "paid"}).usage_note is None


# Schema enforcement


def test_groq_schema_has_no_references_left():
    schema = json.loads(generate(completion(json.dumps(valid_reply())))[1].requests[0].content)["response_format"]["json_schema"]["schema"]
    text = json.dumps(schema)

    assert "$ref" not in text and "$defs" not in text
    assert "target_metric" in schema["properties"]["recommendations"]["items"]["required"]


def test_inline_refs_resolves_nested_definitions():
    schema = {
        "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "string"}}}},
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Inner"}}},
    }

    assert inline_refs(schema)["properties"]["items"]["items"] == schema["$defs"]["Inner"]


# Recovering a reply Groq refused for its format


def refused(reply):
    body = {"error": {"code": "json_validate_failed", "failed_generation": json.dumps(reply)}}
    return httpx.Response(400, json=body)


def without_target(reply, index=0, evidence_ids=None):
    reply = json.loads(json.dumps(reply))
    del reply["recommendations"][index]["target_metric"]
    if evidence_ids is not None:
        reply["recommendations"][index]["evidence_ids"] = evidence_ids
    return reply


def test_missing_target_is_filled_from_its_only_cited_metric():
    provider, _ = provider_with(refused(without_target(valid_reply(), evidence_ids=["M6", "W2"])))

    result = service.generate_diagnosis(evidence_for(), provider)

    assert result.diagnosis.recommendations[0].target_metric == "cpm"
    assert any("Filled the missing target metric of recommendation 1 with cpm" in note for note in result.notes)
    assert (result.input_tokens, result.output_tokens) == (None, None)


@pytest.mark.parametrize(
    "evidence_ids",
    [
        ["M6", "M7"],  # two metrics, so ambiguous
        ["W2"],  # no metric at all
        ["M7"],  # CTR, but the linked hypothesis is about CPM
        ["M99"],  # not in the evidence
    ],
)
def test_missing_target_is_never_guessed(evidence_ids):
    reply = without_target(valid_reply(), evidence_ids=evidence_ids)
    provider, _ = provider_with(refused(reply))

    result = service.generate_diagnosis(evidence_for(), provider)

    assert [r.investigation for r in result.diagnosis.recommendations] == [valid_reply()["recommendations"][1]["investigation"]]
    assert any("had no target metric" in issue for issue in result.withheld)


def test_other_missing_fields_are_still_a_format_failure():
    reply = valid_reply()
    del reply["hypotheses"][0]["uncertainty"]
    provider, _ = provider_with(refused(reply))

    with pytest.raises(AIResponseError, match="required format"):
        service.generate_diagnosis(evidence_for(), provider)


def test_refusal_without_a_reply_is_a_format_failure():
    provider, _ = provider_with(httpx.Response(400, json={"error": {"code": "json_validate_failed"}}))

    with pytest.raises(AIResponseError, match="required format"):
        service.generate_diagnosis(evidence_for(), provider)


def test_refused_reply_that_is_not_json_is_a_format_failure():
    body = {"error": {"code": "json_validate_failed", "failed_generation": '{"hypotheses": ['}}
    provider, _ = provider_with(httpx.Response(400, json=body))

    with pytest.raises(AIResponseError, match="required format"):
        service.generate_diagnosis(evidence_for(), provider)


@pytest.mark.parametrize(
    "fixture, scenario",
    [("groq_failed_landing_page_issue.json", 2), ("groq_failed_aov_decline.json", 4)],
)
def test_live_refusals_recover_nothing_that_was_guessed(fixture, scenario):
    partial = (Path(__file__).parent / "fixtures" / fixture).read_text("utf-8")
    body = {"error": {"code": "json_validate_failed", "failed_generation": partial}}
    provider, _ = provider_with(httpx.Response(400, json=body))

    try:
        result = service.generate_diagnosis(evidence_for(scenario), provider)
    except AIResponseError as error:
        assert any("had no target metric" in issue for issue in error.issues)
    else:
        assert all(r.target_metric for r in result.diagnosis.recommendations)
        assert any("had no target metric" in issue for issue in result.withheld)


# Live GPT-OSS 120B replies whose useful checks were mislabelled as uploaded data


def replay(fixture, scenario):
    text = (Path(__file__).parent / "fixtures" / fixture).read_text("utf-8")
    provider, _ = provider_with(completion(text))
    return service.generate_diagnosis(evidence_for(scenario), provider)


@pytest.mark.parametrize(
    "fixture, scenario, metric, investigation, disclosed",
    [
        ("groq_120b_landing_page_issue.json", 2, "link_clicks_to_landing_page_views",
         "Review landing page performance metrics for the current period.", "Site performance data."),
        ("groq_120b_checkout_friction.json", 3, "checkouts_initiated_to_purchases",
         "Review checkout funnel logs for drop‑off reasons.", "abandonment reasons"),
    ],
)
def test_mislabelled_site_checks_are_relabelled_and_shown(fixture, scenario, metric, investigation, disclosed):
    result = replay(fixture, scenario)
    shown = {r.investigation: r for r in result.diagnosis.recommendations}

    assert investigation in shown
    assert shown[investigation].check_source == "external_data"
    assert shown[investigation].target_metric == metric
    assert any(f'Relabelled "{investigation}" as an external check.' == note for note in result.notes)
    # Either the app adds the data need, or the model had already listed it.
    assert any(disclosed in need.information for need in result.diagnosis.additional_information)


@pytest.mark.parametrize(
    "fixture, scenario",
    [("groq_120b_landing_page_issue.json", 2), ("groq_120b_checkout_friction.json", 3)],
)
def test_relabelled_replies_still_withhold_what_fails(fixture, scenario):
    result = replay(fixture, scenario)
    targets = {r.target_metric for r in result.diagnosis.recommendations}

    # The off-path hypothesis and its recommendation are still withheld after relabelling.
    assert targets <= evidence_for(scenario).path_nodes
    assert any("not on a diagnostic path" in issue for issue in result.withheld)
    assert len(result.diagnosis.hypotheses) == 1


@pytest.mark.parametrize(
    "fixture, scenario",
    [("groq_120b_landing_page_issue.json", 2), ("groq_120b_checkout_friction.json", 3)],
)
def test_explanation_about_both_hypotheses_is_withheld_when_one_is(fixture, scenario):
    result = replay(fixture, scenario)

    assert result.diagnosis.confidence_explanation == ""
    assert "The confidence explanation refers to hypotheses that were withheld." in result.withheld


def test_relabelling_does_not_excuse_a_causal_claim():
    reply = valid_reply()
    reply["recommendations"][0].update(
        investigation="Review page load times, which rose because of a redesign.",
        what_to_check="Page load time for each landing page.",
    )
    provider, _ = provider_with(completion(json.dumps(reply)))

    result = service.generate_diagnosis(evidence_for(), provider)

    assert all("page load" not in r.investigation for r in result.diagnosis.recommendations)
    assert any("states a cause as fact" in issue for issue in result.withheld)


# Names restricted to the current evidence


def sent_schema(scenario):
    provider, fake = provider_with(completion(json.dumps(valid_reply())))
    try:
        service.generate_diagnosis(evidence_for(scenario), provider)
    except AIResponseError:
        pass  # The reply may not suit this scenario. Only the request matters here.
    return json.loads(fake.requests[0].content)["response_format"]["json_schema"]["schema"]


def enums(schema):
    hypothesis = schema["properties"]["hypotheses"]["items"]["properties"]["diagnostic_node"]
    recommendation = schema["properties"]["recommendations"]["items"]["properties"]["target_metric"]
    return hypothesis["enum"], recommendation["enum"]


def test_schema_allows_only_this_evidence_by_internal_name():
    evidence = evidence_for(2)  # Landing page issue
    nodes, targets = enums(sent_schema(2))

    assert nodes == sorted(evidence.path_nodes)
    assert "link_clicks_to_landing_page_views" in nodes and "cpm" not in nodes
    assert targets == sorted(evidence.metrics)
    assert not any("→" in name or " " in name for name in nodes + targets)


@pytest.mark.parametrize("invented", ["landing_page_load_time", "creative_frequency", "promotion_active"])
def test_invented_metric_names_are_not_allowed(invented):
    nodes, targets = enums(sent_schema(2))

    assert invented not in nodes and invented not in targets


@pytest.mark.parametrize("invented", ["landing_page_load_time", "creative_frequency"])
def test_invented_target_is_still_withheld_if_a_model_ignores_the_schema(invented):
    evidence = evidence_for(2)
    reply = valid_reply(evidence)
    lpv = evidence.metric_fact_id("link_clicks_to_landing_page_views")
    reply["hypotheses"][0].update(diagnostic_node="link_clicks_to_landing_page_views", evidence_ids=[lpv],
                                  unverified_business_cause="Slower page loading may have contributed to fewer landing page views.")
    reply["recommendations"] = [dict(reply["recommendations"][1], target_metric=invented, evidence_ids=[lpv],
                                     investigation="Check the average page load time for the landing pages.")]
    provider, _ = provider_with(completion(json.dumps(reply)))

    result = service.generate_diagnosis(evidence, provider)

    assert result.diagnosis.recommendations == []
    assert any(f"not in the evidence: {invented}" in issue for issue in result.withheld)


def test_page_load_check_targets_the_diagnosed_metric():
    evidence = evidence_for(2)
    reply = valid_reply(evidence)
    lpv = evidence.metric_fact_id("link_clicks_to_landing_page_views")
    reply["hypotheses"][0].update(diagnostic_node="link_clicks_to_landing_page_views", evidence_ids=[lpv],
                                  unverified_business_cause="Slower page loading may have contributed to fewer landing page views.")
    reply["recommendations"] = [dict(reply["recommendations"][1], target_metric="link_clicks_to_landing_page_views",
                                     evidence_ids=[lpv], investigation="Check the average page load time for the landing pages.",
                                     what_to_check="Average page load time in each period from site analytics.")]
    provider, _ = provider_with(completion(json.dumps(reply)))

    result = service.generate_diagnosis(evidence, provider)

    assert [r.target_metric for r in result.diagnosis.recommendations] == ["link_clicks_to_landing_page_views"]
    assert result.diagnosis.recommendations[0].check_source == "external_data"


def test_no_eligible_names_sends_nothing():
    from funnel_magnet.diagnose import diagnose_totals
    from funnel_magnet.metrics import Totals
    from funnel_magnet.ai.evidence import build_evidence

    diagnosis = diagnose_totals(Totals(0, 0, 0, 0, 0), Totals(100, 10000, 200, 10, 500))
    evidence = build_evidence(diagnosis, "EUR")
    provider, fake = provider_with(completion(json.dumps(valid_reply())))

    assert restricted_schema(evidence) is None
    with pytest.raises(AIRequestError, match="needs at least one diagnostic path"):
        service.generate_diagnosis(evidence, provider)
    assert fake.requests == []


def test_gemini_receives_the_same_restriction():
    captured = {}

    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop before any network use")

    class Client:
        models = Models()

    evidence = evidence_for(2)
    with pytest.raises(AIRequestError):
        service.generate_diagnosis(evidence, GeminiProvider("test-key", "gemini-test", Client()))

    assert captured["config"].response_json_schema == restricted_schema(evidence)
