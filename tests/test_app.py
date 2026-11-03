"""Run the real Streamlit app in a simulated session."""

import json
from html import escape
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from funnel_magnet.ai import service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import AIRequestError, ProviderResponse
from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import WEEKLY
from funnel_magnet.sources.meta import load_meta_csv
from funnel_magnet import version
from funnel_magnet.sources import meta
from funnel_magnet.ui import report, view

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
    app.secrets["FM_AI_ENABLED"] = "true"
    app.secrets["GEMINI_API_KEY"] = "test-only"
    return app.run()


def good_reply():
    data = load_meta_csv(view.load_samples()[0].path)
    evidence = build_evidence(run_diagnosis(data.data, WEEKLY), data.currency)
    cpm = evidence.metric_fact_id("cpm")
    return json.dumps(
        {
            "hypotheses": [
                {
                    "rank": 1,
                    "diagnostic_node": "cpm",
                    "mathematical_evidence": "CPM rose and accounts for most of the movement.",
                    "unverified_business_cause": "Delivery costs may have risen.",
                    "evidence_ids": [cpm],
                    "confidence": "low",
                    "uncertainty": "The data shows the change only.",
                }
            ],
            "recommendations": [
                {
                    "priority": 1,
                    "hypothesis_rank": 1,
                    "check_source": "uploaded_data",
                    "investigation": "Compare CPM for each campaign separately.",
                    "what_to_check": "See whether the rise appears in every campaign.",
                    "if_supported": "Treat the rise as account-wide.",
                    "if_not_supported": "Review settings in the affected campaign.",
                    "target_metric": "cpm",
                    "expected_direction": "unclear",
                    "expected_impact": "This shows where to focus.",
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
        "Mathematical diagnosis",
        "KPI overview",
        "Diagnostic decision tree",
        "Findings",
        "ROAS contribution",
        "AI diagnosis",
        "Data quality",
    ]


def test_without_a_key_the_button_is_off_and_the_dashboard_works(monkeypatch):
    load_settings = service.load_settings
    enabled = {"FM_AI_ENABLED": "true"}
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings(enabled))

    app = start()

    assert generate_button(app).disabled
    assert any("no API key is configured" in item.value for item in app.info)
    assert "KPI overview" in [item.value for item in app.subheader]


def test_mathematical_diagnosis_appears_without_a_key(monkeypatch):
    load_settings = service.load_settings
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings({}))

    text = page_text(start())

    assert "Calculated by Python" in text
    assert "The largest adverse contributor is CPM" in text


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
    reply["recommendations"] = []
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    assert any("failed the evidence checks" in item.value for item in app.error)
    assert "Delivery costs may have risen." not in page_text(app)
    assert any("M99" in item.value for item in app.caption)


def test_partly_valid_reply_flags_what_was_withheld(monkeypatch):
    reply = json.loads(good_reply())
    reply["hypotheses"][0]["evidence_ids"] = ["M99"]
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    text = page_text(app)
    assert not app.exception and not app.error
    assert "Partly verified" in text and "M99" in text
    assert "Delivery costs may have risen." not in text
    assert "AI-generated recommendations" in text


def test_a_key_alone_does_not_switch_ai_on(monkeypatch):
    provider = CountingProvider(good_reply())
    monkeypatch.setattr(service, "create_provider", lambda settings: provider)
    app = AppTest.from_file("../app.py", default_timeout=30)
    app.secrets["GEMINI_API_KEY"] = "test-only"
    app.run()

    assert generate_button(app).disabled
    assert any("switched off" in item.value for item in app.info)
    assert provider.calls == 0


def test_public_defaults_keep_every_core_feature_working(monkeypatch):
    load_settings = service.load_settings
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings({}))

    app = start()

    assert not app.exception
    assert generate_button(app).disabled
    assert download_button(app)
    assert any(view.DECISION_SUPPORT_NOTICE == item.value for item in app.info)


def test_csv_upload_works_with_ai_off():
    rows = [
        f"2026-06-{day:02d},Campaign A,100,10000,200,10,500"
        for day in range(1, 29)
    ]
    app = upload(start(), "\n".join([HEADER, *rows]) + "\n")

    assert not app.exception
    assert download_button(app)


def test_account_level_inr_export_runs_end_to_end(monkeypatch):
    built = capture_reports(monkeypatch)
    fixture = Path(__file__).parent / "fixtures" / "meta_account_level_inr.csv"
    app = upload(start(), fixture.read_text("utf-8-sig"))

    assert not app.exception and not app.error
    assert widget(app.selectbox, "Campaign").options == ["All campaigns"]
    text = page_text(app)
    assert "₹" in text and "€" not in text
    assert "account-level total" in text
    assert download_button(app)
    assert "₹" in built[-1] and "€" not in built[-1]


ACCOUNT_EXPORT = Path(__file__).parent / "fixtures" / "meta_account_level_inr.csv"
BOM = bytes([0xEF, 0xBB, 0xBF])
CRLF = bytes([13, 10])


def upload_bytes(app, data, name="export.csv"):
    """Upload exact file bytes through the sidebar widget, as a visitor's browser does."""
    widget(app.radio, "Data source").set_value("Upload CSV")
    app.run()
    widget(app.file_uploader, "Meta Ads daily export").upload(name, data, "text/csv")
    return app.run()


def rejecting_importer(source, blank_actions_as_zero=False):
    """Stands in for the importer before account-level support."""
    result = meta.LoadResult()
    result.add(meta.ERROR, "missing_columns", "Required columns are missing: campaign.")
    return result


def test_exact_export_bytes_load_through_the_upload_widget():
    st.cache_data.clear()
    data = ACCOUNT_EXPORT.read_bytes()
    assert data.startswith(BOM) and CRLF in data

    app = upload_bytes(start(), data)

    assert not app.exception and not app.error
    assert "account-level total" in page_text(app)
    assert widget(app.selectbox, "Campaign").options == ["All campaigns"]


def test_an_upload_rejected_by_older_code_is_read_again_after_an_update(monkeypatch):
    st.cache_data.clear()
    data = ACCOUNT_EXPORT.read_bytes()
    real_importer = meta.load_meta_csv
    monkeypatch.setattr(meta, "load_meta_csv", rejecting_importer)
    monkeypatch.setattr(version, "code_version", lambda: "before-update")

    before = upload_bytes(start(), data)
    assert any("missing: campaign" in item.value for item in before.error)

    # The update replaces the importer while the server, and its cache, keep running.
    monkeypatch.setattr(meta, "load_meta_csv", real_importer)
    same_version = upload_bytes(start(), data)
    assert any("missing: campaign" in item.value for item in same_version.error)

    monkeypatch.setattr(version, "code_version", lambda: "after-update")
    after = upload_bytes(start(), data)
    assert not after.exception and not after.error
    assert "account-level total" in page_text(after)


def test_dashboard_warns_about_recommendations_on_withheld_hypotheses(monkeypatch):
    reply = json.loads(good_reply())
    reply["hypotheses"][0]["evidence_ids"] = ["M99"]
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    text = page_text(app)
    assert "Tests hypothesis 1 (withheld)" in text
    assert "not a validated conclusion" in text


def capture_reports(monkeypatch):
    """Keep every report the app builds, because a download button hides its data."""
    built, real = [], report.build_report

    def build(item):
        built.append(real(item))
        return built[-1]

    monkeypatch.setattr(report, "build_report", build)
    return built


def download_button(app):
    return next(item for item in app.get("download_button") if item.label == "Download Diagnostic Report")


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_every_scenario_offers_a_factual_report_without_a_key(monkeypatch, name):
    built = capture_reports(monkeypatch)
    app = choose_sample(start(), name)

    assert not app.exception
    assert download_button(app)
    assert "contains no AI interpretation" in built[-1]


def test_report_includes_the_ai_result_after_the_click(monkeypatch):
    built = capture_reports(monkeypatch)
    app = start_with_key(monkeypatch, CountingProvider(good_reply()))
    assert "contains no AI interpretation" in built[-1]

    generate_button(app).click()
    app.run()

    assert not app.exception
    assert "Delivery costs may have risen." in built[-1]
    assert "Fake fake-model" in built[-1]


def test_report_shows_the_selected_campaign_and_period(monkeypatch):
    built = capture_reports(monkeypatch)
    app = start()
    campaign = widget(app.selectbox, "Campaign").options[1]
    widget(app.selectbox, "Campaign").select(campaign)
    app.run()

    assert escape(campaign) in built[-1]
    assert "Current period" in built[-1]
    assert not app.exception


def test_failed_ai_request_still_gives_a_factual_report(monkeypatch):
    built = capture_reports(monkeypatch)
    app = start_with_key(monkeypatch, CountingProvider(error=AIRequestError("The AI service could not be reached.")))

    generate_button(app).click()
    app.run()

    assert download_button(app)
    assert "contains no AI interpretation" in built[-1]


def test_report_never_contains_the_key(monkeypatch):
    built = capture_reports(monkeypatch)
    monkeypatch.setattr(service, "create_provider", lambda settings: CountingProvider(good_reply()))
    app = AppTest.from_file("../app.py", default_timeout=30)
    app.secrets["FM_AI_ENABLED"] = "true"
    app.secrets["GEMINI_API_KEY"] = "secret-sentinel-value"
    app.run()
    generate_button(app).click()
    app.run()

    assert "Delivery costs may have risen." in built[-1]
    assert not any("secret-sentinel-value" in item for item in built)


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
