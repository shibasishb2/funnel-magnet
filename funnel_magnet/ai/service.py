"""Settings and the end-to-end AI diagnosis request."""

import json
import os
from dataclasses import dataclass

from pydantic import ValidationError

from funnel_magnet.ai.normalize import normalize_diagnosis, recover_target_metrics
from funnel_magnet.ai.prompt import SYSTEM_PROMPT, build_user_prompt
from funnel_magnet.ai.providers import (
    AIError,
    AIRequestError,
    AIResponseError,
    GeminiProvider,
    GroqProvider,
)
from funnel_magnet.ai.schema import AIDiagnosis, restricted_schema
from funnel_magnet.ai.validate import coverage_issues, keep_valid_sections, validate_diagnosis

GEMINI = "gemini"
GROQ = "groq"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
# The 120B model returned a correctly formatted reply in all live tests, where 20B
# broke the format in about one reply in three.
DEFAULT_MODELS = {GEMINI: DEFAULT_MODEL, GROQ: "openai/gpt-oss-120b"}
KEY_SETTINGS = {GEMINI: "GEMINI_API_KEY", GROQ: "GROQ_API_KEY"}
MODEL_SETTINGS = {GEMINI: "GEMINI_MODEL", GROQ: "GROQ_MODEL"}
FREE_TIER = "free"
PAID_TIER = "paid"
MAX_RUNS_PER_SESSION = 10

AI_OFF_MESSAGE = (
    "AI analysis is switched off in this deployment. The calculations, dashboard and report "
    "all work without it."
)
NO_KEY_MESSAGE = (
    "AI analysis is unavailable because no API key is configured. "
    "The calculations on this page are not affected."
)
UNKNOWN_PROVIDER_MESSAGE = (
    "AI analysis is unavailable because AI_PROVIDER isn't recognised. Use gemini or groq."
)
SHARED_GROQ_MESSAGE = (
    "AI analysis is off because Groq hasn't been approved for a shared deployment yet. "
    "Review its terms and limits first."
)
GEMINI_FREE_TIER_NOTE = (
    "Free tier: for local development only. Apps available to users in the EEA, "
    "Switzerland or the UK need paid Gemini use."
)
GROQ_NOTE = "Groq: for local testing only until its terms and limits are reviewed for sharing."
SHARED_FREE_TIER_MESSAGE = (
    "AI analysis is off because this shared deployment isn't set to the paid tier. "
    "Google's terms require paid use for apps available to users in the EEA, Switzerland or the UK."
)


@dataclass(frozen=True)
class AISettings:
    api_key: str | None
    model: str
    tier: str
    shared_deployment: bool
    enabled: bool = False
    provider: str = GEMINI

    @property
    def unavailable_reason(self):
        # Off unless switched on, so adding a key alone can never start live calls.
        if not self.enabled:
            return AI_OFF_MESSAGE
        if self.provider not in DEFAULT_MODELS:
            return UNKNOWN_PROVIDER_MESSAGE
        if not self.api_key:
            return NO_KEY_MESSAGE
        if self.shared_deployment and self.provider == GROQ:
            return SHARED_GROQ_MESSAGE
        if self.shared_deployment and self.tier != PAID_TIER:
            return SHARED_FREE_TIER_MESSAGE
        return None

    @property
    def usage_note(self):
        """A caption about the provider's terms, or None."""
        if self.provider == GROQ:
            return GROQ_NOTE
        return GEMINI_FREE_TIER_NOTE if self.tier != PAID_TIER else None


@dataclass(frozen=True)
class AIResult:
    diagnosis: AIDiagnosis
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    notes: tuple[str, ...] | list[str] = ()
    withheld: tuple[str, ...] | list[str] = ()


def load_settings(secrets=None):
    """Read settings from Streamlit secrets, then environment variables."""
    secrets = secrets or {}

    def read(name, default=None):
        value = secrets.get(name) or os.environ.get(name)
        return str(value).strip() if value else default

    provider = read("AI_PROVIDER", GEMINI).lower()
    return AISettings(
        api_key=read(KEY_SETTINGS.get(provider, "")),
        model=read(MODEL_SETTINGS.get(provider, ""), DEFAULT_MODELS.get(provider, DEFAULT_MODEL)),
        tier=read("GEMINI_TIER", FREE_TIER).lower(),
        shared_deployment=read("FM_SHARED_DEPLOYMENT", "false").lower() == "true",
        enabled=read("FM_AI_ENABLED", "false").lower() == "true",
        provider=provider,
    )


def create_provider(settings):
    if settings.provider == GROQ:
        return GroqProvider(settings.api_key, settings.model)
    return GeminiProvider(settings.api_key, settings.model)


def generate_diagnosis(evidence, provider):
    """Ask the model to interpret the evidence. Only sections that pass are returned.

    The result lists what was withheld. A response with no valid hypothesis or
    recommendation is rejected.
    """
    schema = restricted_schema(evidence)
    if schema is None:
        # No metric could be named, so any reply would fail. Nothing is sent.
        raise AIRequestError("AI analysis needs at least one diagnostic path and metric, and this comparison has none.")

    input_tokens = output_tokens = None
    try:
        response = provider.generate(SYSTEM_PROMPT, build_user_prompt(evidence), schema)
        text = response.text
        input_tokens, output_tokens = response.input_tokens, response.output_tokens
    except AIResponseError as error:
        if not error.partial_text:
            raise
        # The provider refused the reply for its format. Recover only what the evidence
        # settles, and let the usual checks judge the rest.
        text = error.partial_text

    try:
        reply = json.loads(text)
    except ValueError:
        raise AIResponseError("The AI response did not match the required format.") from None
    reply, recovered, unrecovered = recover_target_metrics(reply, evidence)
    try:
        diagnosis = AIDiagnosis.model_validate(reply)
    except ValidationError:
        raise AIResponseError("The AI response did not match the required format.") from None

    diagnosis, notes = normalize_diagnosis(diagnosis, evidence)
    notes = [*recovered, *notes]
    issues = [*unrecovered, *validate_diagnosis(diagnosis, evidence)]
    verified = keep_valid_sections(diagnosis, evidence)
    if issues and not (verified.hypotheses or verified.recommendations):
        raise AIResponseError("The AI response was rejected because it failed the evidence checks.", issues)
    issues += coverage_issues(verified, evidence)

    return AIResult(verified, provider.name, provider.model, input_tokens, output_tokens, notes, issues)


__all__ = ["AIError", "AIResult", "AISettings", "create_provider", "generate_diagnosis", "load_settings"]
