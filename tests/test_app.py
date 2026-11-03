"""Run the real Streamlit app in a simulated session."""

import json
from html import escape
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from funnel_magnet.ai import service
from funnel_magnet.ai.providers import AIRequestError, ProviderResponse
from funnel_magnet import version
from funnel_magnet.sources import meta
from funnel_magnet.ui import report, view

SAMPLE_NAMES = [sample.name for sample in view.load_samples()]

HEADER = "Day,Campaign name,Amount spent (EUR),Impressions,Link clicks,Purchases,Purchases conversion value"


def start():
    return AppTest.from_file("../app.py", default_timeout=30).run()


def widget(collection, label):
    return next(item for item in collection if item.label == label)


def page_text(app):
    parts = [item.value for item in app.markdown]
    parts += [item.value for item in app.caption]
    parts += [item.value for item in app.warning]
    parts += [item.value for item in app.error]
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
    for name in ("FM_SHARED_DEPLOYMENT", "FM_AI_ENABLED", "AI_PROVIDER", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(service, "create_provider", blocked)
    # The usage limit is shared by every session, so each test starts with a fresh, roomy one.
    st.cache_resource.clear()
    real_limiter = service.UsageLimiter
    monkeypatch.setattr(service, "UsageLimiter", lambda: real_limiter(per_minute=1000, per_day=1000))


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


AI_MARKER = "Auction competition may have raised delivery costs."


def good_reply():
    """A valid prioritisation for the default view: CPM inflation, latest two weeks."""
    return json.dumps(
        {
            "cause_priorities": [
                {"cause_id": "auction_competition", "rationale": AI_MARKER},
                {"cause_id": "budget_bid_changes", "rationale": "A budget or bid change could also explain it."},
            ],
            "check_priorities": [
                {"check_id": "account_change_history_missing", "rationale": "Ignored, because it isn't listed."},
                {"check_id": "account_change_log", "rationale": "It could quickly rule self-made changes in or out."},
                {"check_id": "campaign_split", "rationale": "It shows whether the rise is account-wide."},
            ],
            "relationship": "",
            "action_guidance": [
                {"cause_id": "auction_competition", "guidance": "If benchmarks show a market-wide rise, review bids and timing."}
            ],
            "extra_checks": [],
        }
    )


def generate_button(app):
    return widget(app.button, "Prioritise with AI")


def test_app_opens_on_a_sample_without_errors():
    app = start()

    assert not app.exception
    assert "Funnel Magnet" in page_text(app)
    assert [item.value for item in app.subheader] == [
        "Executive diagnosis",
        "Marketing diagnosis",
        "KPI overview",
        "Diagnostic decision tree",
        "ROAS contribution",
        "Possible causes, checks and actions",
        "Data quality",
    ]
    # Level 1 sits on the page, and everything else is in one collapsed expander.
    assert [e.label for e in app.expander] == ["Detailed analysis: KPIs, decision tree, contributions, full playbook and data quality"]
    detail = app.expander[0]
    assert [item.value for item in detail.subheader][0] == "Marketing diagnosis"
    assert any("fm-executive" in item.body for item in app.main.get("html")) and not any(
        "fm-executive" in item.body for item in detail.get("html"))


def test_without_a_key_the_button_is_off_and_the_dashboard_works(monkeypatch):
    load_settings = service.load_settings
    enabled = {"FM_AI_ENABLED": "true"}
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings(enabled))

    app = start()

    assert not [button for button in app.button if button.label == "Prioritise with AI"]
    assert any("no API key is configured" in item.value for item in app.caption)
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
    assert "AI prioritisation" in text and "Fake fake-model" in text
    assert AI_MARKER in text
    assert "If benchmarks show a market-wide rise" in text
    # The playbook's own top three moves to the AI's order.
    assert text.index("Account change history.") < text.index("Compare each campaign separately.")

    # With a result shown, there is nothing left to click for this comparison.
    app.run()
    assert not any(button.label == "Prioritise with AI" for button in app.button)
    assert provider.calls == 1
    assert AI_MARKER in page_text(app)


def test_changing_the_comparison_hides_the_old_diagnosis(monkeypatch):
    app = start_with_key(monkeypatch, CountingProvider(good_reply()))
    generate_button(app).click()
    app.run()

    assert AI_MARKER in page_text(app)

    widget(app.radio, "Period").set_value("Monthly")
    app.run()

    assert AI_MARKER not in page_text(app)


def test_failed_request_keeps_the_dashboard_working(monkeypatch):
    provider = CountingProvider(error=AIRequestError("The AI service could not be reached."))
    app = start_with_key(monkeypatch, provider)

    generate_button(app).click()
    app.run()

    assert not app.exception
    assert any("could not be reached" in item.value and "complete without AI" in item.value for item in app.error)
    assert "Data quality" in [item.value for item in app.subheader]
    assert "B. Possible causes" in page_text(app) and "Priority 1" in page_text(app)


def test_reply_with_nothing_usable_is_not_displayed(monkeypatch):
    reply = json.loads(good_reply())
    reply["cause_priorities"] = [{"cause_id": "invented_cause", "rationale": AI_MARKER}]
    reply["check_priorities"] = []
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    assert any("didn't prioritise any playbook cause" in item.value for item in app.error)
    assert AI_MARKER not in page_text(app)
    assert any("invented_cause" in item.value for item in app.caption)
    assert "AI prioritisation" not in page_text(app)


def test_partly_valid_reply_flags_what_was_withheld(monkeypatch):
    reply = json.loads(good_reply())
    reply["cause_priorities"][0]["rationale"] = "Auction competition caused the rise in CPM."
    app = start_with_key(monkeypatch, CountingProvider(json.dumps(reply)))

    generate_button(app).click()
    app.run()

    text = page_text(app)
    assert not app.exception and not app.error
    # A short summary on the page, with the technical reasons folded away.
    assert "pass the accuracy checks and was left out" in text
    assert "<summary>Technical details</summary>" in text and "states a cause as fact" in text
    assert "Auction competition caused the rise" not in text
    assert "It could quickly rule self-made changes in or out." in text


def test_a_key_alone_does_not_switch_ai_on(monkeypatch):
    provider = CountingProvider(good_reply())
    monkeypatch.setattr(service, "create_provider", lambda settings: provider)
    app = AppTest.from_file("../app.py", default_timeout=30)
    app.secrets["GEMINI_API_KEY"] = "test-only"
    app.run()

    assert not [button for button in app.button if button.label == "Prioritise with AI"]
    assert any("switched off" in item.value for item in app.caption)
    assert provider.calls == 0


def test_public_defaults_keep_every_core_feature_working(monkeypatch):
    load_settings = service.load_settings
    monkeypatch.setattr(service, "load_settings", lambda secrets=None: load_settings({}))

    app = start()

    assert not app.exception
    assert not [button for button in app.button if button.label == "Prioritise with AI"]
    assert not [item for item in app.expander if item.label == "Evidence available to the AI"]
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


def test_groq_can_be_chosen_as_the_provider(monkeypatch):
    provider = CountingProvider(good_reply())
    monkeypatch.setattr(service, "create_provider", lambda settings: provider)
    app = AppTest.from_file("../app.py", default_timeout=30)
    app.secrets["FM_AI_ENABLED"] = "true"
    app.secrets["AI_PROVIDER"] = "groq"
    app.secrets["GROQ_API_KEY"] = "gsk_test_only"
    app.run()

    assert any("Groq free plan" in item.value for item in app.caption)
    generate_button(app).click()
    app.run()

    assert provider.calls == 1
    assert AI_MARKER in page_text(app)
    assert "gsk_test_only" not in page_text(app)


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
    assert AI_MARKER in built[-1]
    assert "5. AI prioritisation" in built[-1] and "Fake fake-model" in built[-1]
    assert "If benchmarks show a market-wide rise" in built[-1]


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

    assert AI_MARKER in built[-1]
    assert not any("secret-sentinel-value" in item for item in built)


def test_session_limit_stops_further_calls(monkeypatch):
    provider = CountingProvider(error=AIRequestError("The AI service could not be reached."))
    app = start_with_key(monkeypatch, provider)
    assert "AI analyses left in this session: 3 of 3" in page_text(app)

    for left in (2, 1, 0):
        generate_button(app).click()
        app.run()
        assert provider.calls == 3 - left
        # A failed call still counts, and the diagnosis stays complete.
        assert "complete without AI" in page_text(app)
        if left:
            assert f"AI analyses left in this session: {left} of 3" in page_text(app)

    assert service.MAX_RUNS_PER_SESSION == 3
    assert not any(button.label == "Prioritise with AI" for button in app.button)
    assert "This session has used its 3 AI analyses" in page_text(app)


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
    for section in ("a. confirmed findings", "b. possible causes", "c. what to investigate", "d. potential actions"):
        assert section in text
    # Possible causes are named only in hedged playbook wording, never as findings.
    assert not [phrase for phrase in ("because of", "caused by", "due to") if phrase in text]


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
    # Uploaded results are labelled as the user's own file, never as the synthetic demo.
    assert "Your uploaded data: export.csv." in page_text(app)
    assert "Synthetic demonstration data" not in page_text(app)


def test_sample_results_are_labelled_synthetic():
    text = page_text(start())

    assert "Synthetic demonstration data: the “CPM inflation” sample scenario." in text
    assert "Your uploaded data" not in text


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


# One diagnosis on the page and in the report


def page_html(app):
    return "\n".join(str(item.body) for item in app.get("html"))


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_page_and_report_show_the_same_diagnosis(monkeypatch, name):
    built = capture_reports(monkeypatch)
    app = choose_sample(start(), name)
    page, report_html = page_html(app), built[-1]

    assert not app.exception
    for section in ("A. Confirmed findings", "B. Possible causes", "C. What to investigate", "D. Potential actions"):
        assert section in page and section in report_html
    statements = [escape(s) for s in (
        "Seasonal demand may have raised impression costs for this period.",
        "Payment errors may have stopped some buyers from completing their purchase.",
        "Sales may have shifted toward lower-priced products.",
        "People may have seen the same ads too often, so fewer of them clicked.",
    )]
    for statement in statements:
        assert (statement in page) == (statement in report_html)


def test_upload_and_period_checks_follow_the_data(monkeypatch):
    built = capture_reports(monkeypatch)
    app = start()
    # The default view is the latest two weeks of a multi-campaign sample.
    assert "Choose each campaign in the sidebar" in page_html(app)
    assert "Choose earlier periods in the sidebar" in page_html(app)

    campaign = widget(app.selectbox, "Campaign")
    campaign.select(campaign.options[1])
    app.run()
    assert "Export the report broken down by campaign" in page_html(app)
    assert "Export the report broken down by campaign" in built[-1]

    # Comparing the first period with the latest leaves no earlier period in the upload.
    prior = widget(app.selectbox, "Prior period")
    prior.select(prior.options[0])
    current = widget(app.selectbox, "Current period")
    current.select(current.options[-1])
    app.run()
    assert "Export a longer date range" in page_html(app)
    assert "Export a longer date range" in built[-1]


def test_every_sidebar_control_works(monkeypatch):
    capture_reports(monkeypatch)
    app = start()

    widget(app.checkbox, "Blank Meta action cells mean zero").check()
    app.run()
    widget(app.number_input, "Materiality threshold (%)").set_value(10.0)
    app.run()
    for period in ("Monthly", "Weekly"):
        widget(app.radio, "Period").set_value(period)
        app.run()
        for option in widget(app.selectbox, "Campaign").options:
            widget(app.selectbox, "Campaign").select(option)
            app.run()
            assert not app.exception and not app.error
            assert download_button(app)


def test_prior_period_after_the_current_one_is_explained():
    app = start()
    prior = widget(app.selectbox, "Prior period")
    prior.select(prior.options[-1])
    current = widget(app.selectbox, "Current period")
    current.select(current.options[0])
    app.run()

    assert not app.exception
    assert any("prior period must end before" in item.value for item in app.error)
