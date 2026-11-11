"""Model providers. Add a class with generate() to support another one."""

from dataclasses import dataclass
from typing import Protocol

REQUEST_TIMEOUT_MS = 60_000
MAX_OUTPUT_TOKENS = 8192
TEMPERATURE = 0.2


class AIError(Exception):
    """A failure with a message that is safe to show to users."""


class AIRequestError(AIError):
    """The provider could not complete the request."""


class AIResponseError(AIError):
    """The provider replied, but the reply can't be used.

    partial_text holds a reply the provider refused for its format, when it gives one,
    so the app can check whether anything in it is safely recoverable.
    """

    def __init__(self, message, issues=(), partial_text=None):
        super().__init__(message)
        self.issues = list(issues)
        self.partial_text = partial_text


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    name: str
    model: str

    def generate(self, system_prompt, user_prompt, schema) -> ProviderResponse:
        """Return the model's JSON reply, or raise AIRequestError or AIResponseError."""


def json_schema(schema):
    """Accept a Pydantic model or a ready JSON schema, such as one restricted to the evidence."""
    return schema if isinstance(schema, dict) else schema.model_json_schema()


class GeminiProvider:
    name = "Gemini"

    def __init__(self, api_key, model, client=None):
        self.model = model
        self._api_key = api_key
        self._client = client

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                api_key=self._api_key, http_options={"timeout": REQUEST_TIMEOUT_MS}
            )
        return self._client

    def generate(self, system_prompt, user_prompt, schema):
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=json_schema(schema),
            temperature=TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        # Provider error text is never shown, so a key or request detail can't leak.
        try:
            response = self._get_client().models.generate_content(
                model=self.model, contents=user_prompt, config=config
            )
        except errors.APIError as error:
            raise AIRequestError(_message_for_status(error.code)) from None
        except Exception:
            raise AIRequestError("The AI service could not be reached. Please try again.") from None

        if not response.text:
            raise AIResponseError("The AI service returned an empty response.")
        usage = response.usage_metadata
        return ProviderResponse(
            response.text,
            usage.prompt_token_count if usage else None,
            usage.candidates_token_count if usage else None,
        )


def _message_for_status(code):
    if code == 429:
        return "The AI usage limit has been reached. Please try again later."
    if code in (401, 403):
        return "The AI service rejected the API key. Check that it is valid and allowed to use this model."
    if code == 404:
        return "The configured AI model is not available. Check the model name."
    if code == 400:
        return "The AI service rejected the request. Check the API key and model settings."
    return "The AI service is having a problem. Please try again later."


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq counts the requested completion tokens against the per-minute limit, so this
# stays well under the free plan's 8,000 tokens a minute with a 3,500-token prompt.
GROQ_MAX_COMPLETION_TOKENS = 3000
GROQ_REASONING_EFFORT = "low"


def inline_refs(schema, definitions=None):
    """Replace each "#/$defs/..." reference with its definition.

    Groq applied required fields at the top level but not inside referenced
    definitions, so a recommendation could leave out target_metric. Inlining puts
    every rule where the generator applies it.
    """
    if definitions is None:
        definitions = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    if isinstance(schema, list):
        return [inline_refs(item, definitions) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return inline_refs(definitions[schema["$ref"].rsplit("/", 1)[-1]], definitions)
    return {key: inline_refs(value, definitions) for key, value in schema.items() if key != "$defs"}


def strict_schema(schema):
    """Adapt a Pydantic JSON schema to strict structured output.

    Strict mode needs every property required and no extra properties. Titles are
    dropped because they add tokens and say nothing the field names don't.
    """
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    adapted = {key: strict_schema(value) for key, value in schema.items() if key != "title"}
    # Under "properties" the keys are field names, so a field called "title" stays.
    if isinstance(schema.get("properties"), dict):
        adapted["properties"] = {name: strict_schema(value) for name, value in schema["properties"].items()}
    if adapted.get("type") == "object":
        adapted["additionalProperties"] = False
        adapted["required"] = list(adapted.get("properties", {}))
    return adapted


class GroqProvider:
    name = "Groq"
    # Per-feature settings. AI prioritisation keeps these defaults; short answers lower
    # the completion budget so each request uses less of the shared allowance.
    max_completion_tokens = GROQ_MAX_COMPLETION_TOKENS
    schema_name = "ai_diagnosis"

    def __init__(self, api_key, model, client=None):
        self.model = model
        self._api_key = api_key
        self._client = client

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=REQUEST_TIMEOUT_MS / 1000)
        return self._client

    def generate(self, system_prompt, user_prompt, schema):
        import httpx

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": self.schema_name,
                    "strict": True,
                    "schema": strict_schema(inline_refs(json_schema(schema))),
                },
            },
            "temperature": TEMPERATURE,
            "max_completion_tokens": self.max_completion_tokens,
            "reasoning_effort": GROQ_REASONING_EFFORT,
            "include_reasoning": False,
        }
        # Provider error text is never shown, so a key or request detail can't leak.
        try:
            response = self._get_client().post(
                GROQ_URL, json=body, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.TimeoutException:
            raise AIRequestError("The AI service did not respond in time. Please try again.") from None
        except httpx.HTTPError:
            raise AIRequestError("The AI service could not be reached. Please try again.") from None

        if response.status_code != 200:
            raise _groq_error(response)

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AIResponseError("The AI service returned a response that could not be read.") from None
        if choice.get("finish_reason") == "length":
            raise AIResponseError("The AI response was cut off before it finished.")
        if not text.strip():
            raise AIResponseError("The AI service returned an empty response.")
        usage = data.get("usage") or {}
        return ProviderResponse(text, usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _groq_error(response):
    code = response.status_code
    if code == 429:
        wait = response.headers.get("retry-after")
        if wait and wait.replace(".", "", 1).isdigit():
            return AIRequestError(
                f"The AI usage limit has been reached. Please try again in about {round(float(wait))} seconds."
            )
        return AIRequestError(_message_for_status(code))
    if code == 413:
        return AIRequestError("The request is larger than the AI service's current token limit.")
    if code == 400 and "json_validate_failed" in response.text:
        try:
            partial = response.json()["error"].get("failed_generation")
        except (ValueError, KeyError, TypeError, AttributeError):
            partial = None
        return AIResponseError(
            "The AI response did not match the required format.",
            partial_text=partial if isinstance(partial, str) else None,
        )
    if code >= 500:
        return AIRequestError("The AI service is having a problem. Please try again later.")
    return AIRequestError(_message_for_status(code))
