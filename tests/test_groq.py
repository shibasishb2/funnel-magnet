"""Groq provider tests. Every response is mocked, so no key or network is used."""

import json

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
from test_ai import SampleReply, valid_reply

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
    return provider.generate("system", "user", SampleReply), fake


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
    schema = strict_schema(SampleReply.model_json_schema())
    objects = [node for node in walk(schema) if node.get("type") == "object"]

    assert len(objects) == 2
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
        provider.generate("system", "user", SampleReply)
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
        "GEMINI_TIER", "FM_SHARED_DEPLOYMENT", "FM_AI_ENABLED", "FM_ACCESS_PASSWORD",
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


def test_groq_is_allowed_in_a_shared_deployment_only_behind_sign_in(clean_environment):
    shared = {**ENABLED_GROQ, "GROQ_API_KEY": FAKE_KEY, "FM_SHARED_DEPLOYMENT": "true"}

    assert service.load_settings({**shared, "FM_ACCESS_PASSWORD": "long-enough-test-pass"}).unavailable_reason is None
    # A password too short to count leaves the gate shut.
    assert service.load_settings({**shared, "FM_ACCESS_PASSWORD": "short"}).unavailable_reason == service.SHARED_GROQ_MESSAGE


def test_free_tier_limits_are_three_per_session_one_a_minute_and_25_a_day():
    assert service.MAX_RUNS_PER_SESSION == 3
    assert (service.AI_CALLS_PER_MINUTE, service.AI_CALLS_PER_DAY) == (1, 25)
    assert service.DEFAULT_MODELS[service.GROQ] == "openai/gpt-oss-120b"


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
    assert "target_metric" in schema["properties"]["items"]["items"]["required"]


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


# Live GPT-OSS 120B replies whose useful checks were mislabelled as uploaded data


# Names restricted to the current evidence
