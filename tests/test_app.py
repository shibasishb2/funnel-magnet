"""Run the real Streamlit app in a simulated session."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from funnel_magnet.ai import service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import AIRequestError, ProviderResponse
from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet.ui import view

SAMPLE_NAMES = [sample.name for sample in view.load_samples()]
CAUSAL_WORDS = ("fatigue", "because of", "caused by", "due to", "saturation", "auction pressure")

HEADER = "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value"


def start():
    return AppTest.from_file("../app.py", default_timeout=30).run()


def widget(collection, label):
    return next(item for item in collection if item.label == label)


def page_text(app):
    parts = [item.value for item in app.markdown]
    parts += [item.value for item in app.caption]
    parts += [item.value for item in app.warning]
    parts += [item.body for item in app.get("html")]
    return "\n".join(str(part) for part in parts)


def choose_sample(app, name):
    widget(app.selectbox, "Scenario").select(name)
    return app.run()


def upload(app, text):
    widget(app.radio, "Data source").set_value("Upload CSV")
    app.run()
    widget(app.file_uploader, "Meta Ads daily export").upload("export.csv", text.encode("utf-8"), "text/csv")
    return app.run()


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch):
    """Keep tests independent of any real key, and fail if one tries to use the API."""

    def blocked(settings):
        raise AssertionError("A test tried to create a real provider.")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(service, "create_provider", blocked)


class CountingProvider:
    name = "Fake"
    model = "fake-model"

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, 0

    def generate(self, system_prompt, user_prompt, schema):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResponse(self.text, 10, 20)


def start_with_key(monkeypatch, provider):
    monkeypatch.setattr(service, "create_provider", lambda settings: provider)
    app = AppTest.from_file("../app.py", default_timeout=30)
    app.secrets["GEMINI_API_KEY"] = "test-only"
    return app.run()


def good_reply():
    data = load_meta_csv(view.load_samples()[0].path)
    evidence = build_evidence(run_diagnosis(data.data, WEEKLY), data.currency)
    cpm = evidence.metric_fact_id("cpm")
    return json.dumps(
        {
            "executive_diagnosis": "ROAS declined, and the arithmetic points to CPM.",
            "executive_evidence_ids": ["D1"],
            "hypotheses": [
                {
                    "rank": 1,
                    "diagnostic_node": "cpm",
                    "statement": "Delivery costs may have risen.",
                    "evidence_ids": [cpm],
                    "confidence": "low",
                    "uncertainty": "The data shows the change only.",
                }
            ],
            "recommendations": [
                {
                    "priority": 1,
                    "action_type": "investigate",
                    "action": "Review placement delivery data.",
                    "hypothesis_rank": 1,
                    "target_metric": "cpm",
                    "expected_direction": "decrease",
                    "expected_impact": "Lower costs would support ROAS.",
                    "if_confirmed_next_step": "Move budget to lower-cost delivery.",
                    "if_not_confirmed_next_step": "Compare each campaign.",
                    "evidence_ids": [cpm],
                }
            ],
            "additional_information": [
                {
                    "information": "Placement delivery data.",
                    "reason": "It would show where costs changed.",
                    "evidence_ids": [cpm],
                }
            ],
            "confidence_explanation": "Confidence is low.",
        }
    )


def generate_button(app):
    return widget(app.button, "Generate AI Diagnosis")


def test_app_opens_on_a_sample_without_errors():
    app = start()

    assert not app.exception
    assert "Funnel Magnet" in page_text(app)
    assert [item.value for item in app.subheader] == [
        "KPI overview",
        "Diagnostic decision tree",
        "Findings",
        "ROAS contribution",
        "AI diagnosis",
        "Data quality",
    ]


def test_without_a_key_the_button_is_off_and_the_dashboard_works(monkeypatch):
    load_settings = service.load_settings
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings({}))

    app = start()

    assert generate_button(app).disabled
    assert any("no API key is configured" in item.value for item in app.info)
    assert "KPI overview" in [item.value for item in app.subheader]


def test_model_is_not_called_on_load_or_refresh(monkeypatch):
    provider = CountingProvider(good_reply())
    app = start_with_key(monkeypatch, provider)

    app.run()
    widget(app.radio, "Period").set_value("Monthly")
    app.run()

    assert provider.calls == 0


def test_button_shows_the_validated_diagnosis_once(monkeypatch):
    provider = CountingProvider(good_reply())
    app = start_with_key(monkeypatch, provider)

    generate_button(app).click()
    app.run()

    assert not app.exception
    assert provider.calls == 1
    text = page_text(app)
    assert "AI-generated recommendations" in text
    assert "Delivery costs may have risen." in text

    app.run()
    generate_button(app).click()
    app.run()

    assert provider.calls == 1
    assert "Delivery costs may have risen." in page_text(app)


def test_changing_the_comparison_hides_the_old_diagnosis(monkeypatch):
    app = start_with_key(monkeypatch, CountingProvider(good_reply()))
    generate_button(app).click()
    app.run()

    assert "Delivery costs may have risen." in page_text(app)

    widget(app.radio, "Period").set_value("Monthly")
    app.run()

    assert "Delivery costs may have risen." not in page_text(app)


def test_failed_request_keeps_the_dashboard_working(monkeypatch):
    provider = CountingProvider(error=AIRequestError("The AI service could not be reached."))
    app = start_with_key(monkeypatch, provider)

    generate_button(app).click()
    app.run()

    assert not app.exception
    assert any("could not be reached" in item.value for item in app.error)
    assert "Data quality" in [item.value for item in app.subheader]


def test_rejected_reply_is_not_displayed(monkeypatch):
    reply = json.loads(good_reply())
    reply["hypotheses"][0]["evidence_ids"] = ["M99"]
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    assert any("failed the evidence checks" in item.value for item in app.error)
    assert "Delivery costs may have risen." not in page_text(app)
    assert any("M99" in item.value for item in app.caption)


def test_session_limit_stops_further_calls(monkeypatch):
    provider = CountingProvider(error=AIRequestError("The AI service could not be reached."))
    app = start_with_key(monkeypatch, provider)
    app.session_state["ai_runs"] = service.MAX_RUNS_PER_SESSION

    generate_button(app).click()
    app.run()

    assert provider.calls == 0
    assert any("request limit" in item.value for item in app.warning)


def test_blank_cell_checkbox_starts_unchecked():
    assert widget(start().checkbox, "Blank Meta action cells mean zero").value is False


def test_every_sample_can_be_selected_in_the_interface():
    app = start()

    assert list(widget(app.selectbox, "Scenario").options) == SAMPLE_NAMES


@pytest.mark.parametrize("period", ["Weekly", "Monthly"])
@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_every_sample_renders_in_both_period_modes(name, period):
    app = choose_sample(start(), name)
    widget(app.radio, "Period").set_value(period)
    app.run()

    assert not app.exception
    assert not app.error
    text = page_text(app).lower()
    assert "confirmed observations" in text
    assert not [word for word in CAUSAL_WORDS if word in text]


def test_defaults_to_the_latest_two_complete_periods():
    app = start()
    assert "2026-W35 compared with 2026-W34" in page_text(app)

    widget(app.radio, "Period").set_value("Monthly")
    app.run()

    assert "2026-08 compared with 2026-07" in page_text(app)


def test_specific_periods_can_be_selected():
    app = start()
    widget(app.radio, "Period").set_value("Monthly")
    app.run()
    prior = widget(app.selectbox, "Prior period")
    prior.select(prior.options[0])
    app.run()

    assert "2026-08 compared with 2026-06" in page_text(app)


def test_reversed_periods_show_a_clear_error():
    app = start()
    widget(app.radio, "Period").set_value("Monthly")
    app.run()
    prior = widget(app.selectbox, "Prior period")
    prior.select(prior.options[-1])
    app.run()

    assert not app.exception
    assert "must end before" in app.error[0].value


def test_campaign_can_be_selected():
    app = start()
    campaign = widget(app.selectbox, "Campaign")
    campaign.select(campaign.options[1])
    app.run()

    assert campaign.options[1] in page_text(app)
    assert "several campaigns" not in page_text(app)


def test_threshold_changes_the_diagnosis():
    app = start()
    assert "Main driver" in page_text(app)

    widget(app.number_input, "Materiality threshold (%)").set_value(50.0)
    app.run()

    assert "Main driver" not in page_text(app)


def test_uploaded_file_is_diagnosed():
    days = [f"2026-06-{day:02d},A,100.00,10000,200,5,300.00" for day in range(1, 15)]

    app = upload(start(), "\n".join([HEADER, *days]) + "\n")

    assert not app.exception
    assert "KPI overview" in [item.value for item in app.subheader]


def test_invalid_upload_shows_validation_messages():
    app = upload(start(), "Day,Campaign name\n2026-06-01,A\n")

    assert not app.exception
    assert any("Required columns are missing" in item.value for item in app.error)
    assert not app.subheader


def test_blank_actions_need_confirmation_from_the_checkbox():
    days = [f"2026-06-{day:02d},A,100.00,10000,200,5,300.00" for day in range(1, 14)]
    days.append("2026-06-14,A,100.00,10000,200,,")

    app = upload(start(), "\n".join([HEADER, *days]) + "\n")
    assert any("could mean 0 or missing data" in item.value for item in app.error)

    widget(app.checkbox, "Blank Meta action cells mean zero").check()
    app.run()

    assert not app.error
    assert any("confirmed at upload" in item.value for item in app.warning)


def test_too_few_periods_is_explained():
    days = [f"2026-06-{day:02d},A,100.00,10000,200,5,300.00" for day in range(1, 8)]

    app = upload(start(), "\n".join([HEADER, *days]) + "\n")

    assert not app.exception
    assert "two complete periods" in app.error[0].value


def test_zero_spend_period_renders_without_a_chart():
    days = [f"2026-06-{day:02d},A,100.00,10000,200,5,300.00" for day in range(1, 8)]
    days += [f"2026-06-{day:02d},A,0,0,0,0,0" for day in range(8, 15)]

    app = upload(start(), "\n".join([HEADER, *days]) + "\n")

    assert not app.exception
    assert any("no spend" in item.value for item in app.warning)
    assert any("isn't available" in item.value for item in app.info)


def test_zero_purchases_render_without_a_chart():
    days = [f"2026-06-{day:02d},A,100.00,10000,200,5,300.00" for day in range(1, 8)]
    days += [f"2026-06-{day:02d},A,100.00,10000,200,0,0" for day in range(8, 15)]

    app = upload(start(), "\n".join([HEADER, *days]) + "\n")

    assert not app.exception
    assert any("isn't available" in item.value for item in app.info)


def test_no_upload_prompts_the_user():
    app = start()
    widget(app.radio, "Data source").set_value("Upload CSV")
    app.run()

    assert not app.exception
    assert any("Upload a Meta Ads daily export" in item.value for item in app.info)
