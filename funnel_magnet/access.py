"""Prototype access control: one shared tester password, kept in Streamlit Secrets.

This keeps a demonstration to the people given the password. It is not user
authentication: there are no accounts, and anyone with the password has full access.
"""

import hmac
import os

PASSWORD_SETTING = "FM_ACCESS_PASSWORD"
# A short shared password is easy to guess, so one below this length doesn't count.
MIN_PASSWORD_LENGTH = 12
# Failed attempts allowed in one browser session. A new session starts again.
MAX_ATTEMPTS = 5

NOT_CONFIGURED_MESSAGE = (
    "Tester access isn't configured for this deployment yet, so the app is closed. "
    "The administrator needs to add a tester password to the app's secrets."
)


def configured_password(secrets=None):
    """The tester password, or None when none of at least the minimum length is set."""
    value = (secrets or {}).get(PASSWORD_SETTING) or os.environ.get(PASSWORD_SETTING)
    value = str(value) if value else ""
    return value if len(value) >= MIN_PASSWORD_LENGTH else None


def required(secrets=None, shared_deployment=False):
    """Sign-in is needed when a password is set, and always in a shared deployment."""
    return shared_deployment or bool((secrets or {}).get(PASSWORD_SETTING) or os.environ.get(PASSWORD_SETTING))


def password_matches(candidate, expected):
    """Compare in constant time, so the time taken doesn't reveal how much matched."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
