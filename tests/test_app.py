"""Run the real Streamlit app in a simulated session."""

import pytest
from streamlit.testing.v1 import AppTest

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


def test_app_opens_on_a_sample_without_errors():
    app = start()

    assert not app.exception
    assert "Funnel Magnet" in page_text(app)
    assert [item.value for item in app.subheader] == [
        "KPI overview",
        "Diagnostic decision tree",
        "Findings",
        "ROAS contribution",
        "Data quality",
    ]


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
