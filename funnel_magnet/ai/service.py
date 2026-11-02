"""Settings and the end-to-end AI diagnosis request."""

import os
from dataclasses import dataclass

from pydantic import ValidationError

from funnel_magnet.ai.normalize import normalize_diagnosis
from funnel_magnet.ai.prompt import SYSTEM_PROMPT, build_user_prompt
from funnel_magnet.ai.providers import AIError, AIResponseError, GeminiProvider
from funnel_magnet.ai.schema import AIDiagnosis
from funnel_magnet.ai.validate import keep_valid_sections, validate_diagnosis

DEFAULT_MODEL = "gemini-3.1-flash-lite"
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

    @property
    def unavailable_reason(self):
        # Off unless switched on, so adding a key alone can never start live calls.
        if not self.enabled:
            return AI_OFF_MESSAGE
        if not self.api_key:
            return NO_KEY_MESSAGE
        if self.shared_deployment and self.tier != PAID_TIER:
            return SHARED_FREE_TIER_MESSAGE
        return None


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

    return AISettings(
        api_key=read("GEMINI_API_KEY"),
        model=read("GEMINI_MODEL", DEFAULT_MODEL),
        tier=read("GEMINI_TIER", FREE_TIER).lower(),
        shared_deployment=read("FM_SHARED_DEPLOYMENT", "false").lower() == "true",
        enabled=read("FM_AI_ENABLED", "false").lower() == "true",
    )


def create_provider(settings):
    return GeminiProvider(settings.api_key, settings.model)


def generate_diagnosis(evidence, provider):
    """Ask the model to interpret the evidence. Only sections that pass are returned.

    The result lists what was withheld. A response with no valid hypothesis or
    recommendation is rejected.
    """
    response = provider.generate(SYSTEM_PROMPT, build_user_prompt(evidence), AIDiagnosis)

    try:
        diagnosis = AIDiagnosis.model_validate_json(response.text)
    except ValidationError:
        raise AIResponseError("The AI response did not match the required format.") from None

    diagnosis, notes = normalize_diagnosis(diagnosis, evidence)
    issues = validate_diagnosis(diagnosis, evidence)
    verified = keep_valid_sections(diagnosis, evidence)
    if issues and not (verified.hypotheses or verified.recommendations):
        raise AIResponseError("The AI response was rejected because it failed the evidence checks.", issues)

    return AIResult(
        verified,
        provider.name,
        provider.model,
        response.input_tokens,
        response.output_tokens,
        notes,
        issues,
    )


__all__ = ["AIError", "AIResult", "AISettings", "create_provider", "generate_diagnosis", "load_settings"]
