"""The tester sign-in gate, in the real app and on its own."""

import hmac

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from funnel_magnet import access
from funnel_magnet.ai import service

PASSWORD = "test-only-tester-pass"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("FM_ACCESS_PASSWORD", "FM_SHARED_DEPLOYMENT", "FM_AI_ENABLED", "AI_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def blocked(settings):
        raise AssertionError("A test tried to create a real provider.")

    monkeypatch.setattr(service, "create_provider", blocked)
    st.cache_resource.clear()


def app_with(**secrets):
    app = AppTest.from_file("../app.py", default_timeout=30)
    for name, value in secrets.items():
        app.secrets[name] = value
    return app.run()


def text(app):
    parts = [item.value for item in app.markdown] + [item.value for item in app.caption]
    parts += [item.value for item in app.error] + [item.value for item in app.warning] + [item.value for item in app.info]
    parts += [item.body for item in app.get("html")]
    return "\n".join(str(part) for part in parts)


def labels(collection):
    return [item.label for item in collection]


def shows_the_app(app):
    return "Data source" in labels(app.radio)


def sign_in(app, candidate):
    next(item for item in app.text_input if item.label == "Tester password").input(candidate)
    next(item for item in app.button if item.label == "Sign in").click()
    return app.run()


def test_without_a_password_or_shared_deployment_the_local_app_is_open():
    app = app_with()

    assert shows_the_app(app)
    assert "Sign out" not in labels(app.button)


def test_a_shared_deployment_without_a_password_stays_closed():
    for secrets in ({"FM_SHARED_DEPLOYMENT": "true"}, {"FM_SHARED_DEPLOYMENT": "true", "FM_ACCESS_PASSWORD": "short"}):
        app = app_with(**secrets)

        assert not app.exception
        assert not shows_the_app(app)
        assert access.NOT_CONFIGURED_MESSAGE in text(app)
        assert not app.text_input


def test_signed_out_visitors_see_only_the_sign_in_form():
    app = app_with(FM_SHARED_DEPLOYMENT="true", FM_ACCESS_PASSWORD=PASSWORD)

    assert not shows_the_app(app)
    assert labels(app.text_input) == ["Tester password"]
    assert app.text_input[0].proto.type == app.text_input[0].proto.PASSWORD
    assert "prototype access control, not production user authentication" in text(app)
    # No diagnosis, upload, report or AI control is reachable.
    assert not app.get("file_uploader")
    assert "Confirmed findings" not in text(app)
    assert not any(label in labels(app.button) for label in ("Prioritise with AI", "Sign out"))


def test_a_wrong_password_is_refused_and_never_shown():
    app = sign_in(app_with(FM_ACCESS_PASSWORD=PASSWORD), "wrong-password-guess")

    assert not shows_the_app(app)
    assert "That password isn't right." in text(app)
    assert PASSWORD not in text(app)
    assert "wrong-password-guess" not in text(app)


def test_repeated_wrong_passwords_lock_the_session():
    app = app_with(FM_ACCESS_PASSWORD=PASSWORD)
    for _ in range(access.MAX_ATTEMPTS):
        app = sign_in(app, "wrong-password-guess")
    app.run()

    assert "Too many incorrect attempts" in text(app)
    assert not app.text_input
    assert not shows_the_app(app)


def test_the_right_password_opens_the_app_and_sign_out_closes_it():
    app = sign_in(app_with(FM_SHARED_DEPLOYMENT="true", FM_ACCESS_PASSWORD=PASSWORD), PASSWORD)

    assert not app.exception
    assert shows_the_app(app)
    assert "Confirmed findings" in text(app)
    assert PASSWORD not in text(app)

    next(item for item in app.button if item.label == "Sign out").click()
    app.run()

    assert not shows_the_app(app)
    assert labels(app.text_input) == ["Tester password"]


def test_the_privacy_notice_comes_before_the_upload():
    app = sign_in(app_with(FM_ACCESS_PASSWORD=PASSWORD), PASSWORD)
    next(item for item in app.radio if item.label == "Data source").set_value("Upload CSV")
    app.run()

    assert "Demo data only." in "\n".join(item.value for item in app.sidebar.warning)
    assert "never rows, campaign names or file names" in text(app)


def test_signed_in_testers_get_groq_in_a_shared_deployment():
    app = sign_in(app_with(FM_SHARED_DEPLOYMENT="true", FM_ACCESS_PASSWORD=PASSWORD, FM_AI_ENABLED="true",
                           AI_PROVIDER="groq", GROQ_API_KEY="test-only-key"), PASSWORD)

    assert "Prioritise with AI" in labels(app.button)
    assert "AI analyses left in this session: 3 of 3" in text(app)
    assert "test-only-key" not in text(app)


def test_password_comparison_is_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(access.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))

    assert access.password_matches(PASSWORD, PASSWORD)
    assert not access.password_matches("other", PASSWORD)
    assert not access.password_matches("", PASSWORD)
    assert len(calls) == 2


def test_short_passwords_do_not_count():
    assert access.configured_password({"FM_ACCESS_PASSWORD": "x" * (access.MIN_PASSWORD_LENGTH - 1)}) is None
    assert access.configured_password({"FM_ACCESS_PASSWORD": PASSWORD}) == PASSWORD


def test_no_password_is_written_in_the_source():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in [root / "app.py", *(root / "funnel_magnet").rglob("*.py")]:
        assert "FM_ACCESS_PASSWORD =" not in path.read_text("utf-8").replace('PASSWORD_SETTING = "FM_ACCESS_PASSWORD"', "")
