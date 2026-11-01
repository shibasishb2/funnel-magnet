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
    """The provider replied, but the reply can't be used."""

    def __init__(self, message, issues=()):
        super().__init__(message)
        self.issues = list(issues)


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
            response_json_schema=schema.model_json_schema(),
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
