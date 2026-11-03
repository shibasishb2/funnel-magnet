"""Settings and the end-to-end AI diagnosis request."""

import os
from dataclasses import dataclass

from funnel_magnet.ai.providers import AIError, GeminiProvider, GroqProvider

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
MAX_RUNS_PER_SESSION = 3

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
GEMINI_FREE_TIER_NOTE = (
    "Free tier: for local development only. Apps available to users in the EEA, "
    "Switzerland or the UK need paid Gemini use."
)
GROQ_NOTE = (
    "Groq free plan, shared by every visitor of this demonstration. Use synthetic or anonymized data only."
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
        if self.shared_deployment and self.provider == GEMINI and self.tier != PAID_TIER:
            return SHARED_FREE_TIER_MESSAGE
        return None

    @property
    def usage_note(self):
        """A caption about the provider's terms, or None."""
        if self.provider == GROQ:
            return GROQ_NOTE
        return GEMINI_FREE_TIER_NOTE if self.tier != PAID_TIER else None


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


__all__ = ["AIError", "AISettings", "UsageLimiter", "create_provider", "load_settings"]


# Groq's free plan allows about 8,000 tokens a minute and 200,000 a day, shared by every
# visitor. One prioritisation uses up to about 6,000, so these keep the app inside both.
AI_CALLS_PER_MINUTE = 1
AI_CALLS_PER_DAY = 25


class UsageLimiter:
    """Counts AI calls across every visitor of one running app.

    It lives in server memory, so a restart resets it. That is enough for a prototype,
    but it is not a spending guarantee.
    """

    def __init__(self, per_minute=AI_CALLS_PER_MINUTE, per_day=AI_CALLS_PER_DAY, clock=None):
        import threading
        import time

        self.per_minute, self.per_day = per_minute, per_day
        self._clock = clock or time.time
        self._calls = []
        self._lock = threading.Lock()

    def acquire(self):
        """Record a call and return None, or return why it must wait."""
        with self._lock:
            now = self._clock()
            self._calls = [t for t in self._calls if now - t < 86_400]
            if len(self._calls) >= self.per_day:
                return "The shared AI allowance for today has been used. The diagnosis above is complete without it."
            recent = [t for t in self._calls if now - t < 60]
            if len(recent) >= self.per_minute:
                wait = int(60 - (now - min(recent))) + 1
                return f"The shared AI allowance is busy. Please try again in about {wait} seconds."
            self._calls.append(now)
            return None
