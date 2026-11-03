"""Streamlit entry point for the Funnel Magnet diagnostic prototype."""

import io
from datetime import date

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from funnel_magnet import __version__
from funnel_magnet.ai import service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import AIError
from funnel_magnet.diagnose import DEFAULT_MATERIALITY_THRESHOLD, run_diagnosis
from funnel_magnet.periods import MONTHLY, WEEKLY, list_complete_periods
from funnel_magnet.sources.meta import ACCOUNT_LEVEL_CAMPAIGN, load_meta_csv
from funnel_magnet.ui import charts, render, report, view
from funnel_magnet.version import code_version

SAMPLE_SOURCE = "Sample scenario"
UPLOAD_SOURCE = "Upload CSV"
ALL_CAMPAIGNS = "All campaigns"
GRANULARITIES = {"Weekly": WEEKLY, "Monthly": MONTHLY}


# The cache is shared by every visitor, so keep it small on a public server. It is keyed
# by the running code too, because an update can arrive without a restart, and an
# upload rejected by older code must then be read again.
@st.cache_data(show_spinner=False, max_entries=20)
def load_data(raw, blank_actions_as_zero, running_code):
    return load_meta_csv(io.BytesIO(raw), blank_actions_as_zero=blank_actions_as_zero)


def select_source():
    """Return (name, raw bytes), or None until a file has been uploaded."""
    st.sidebar.header("Data")
    source = st.sidebar.radio("Data source", [SAMPLE_SOURCE, UPLOAD_SOURCE])

    if source == SAMPLE_SOURCE:
        samples = view.load_samples()
        sample = st.sidebar.selectbox("Scenario", samples, format_func=lambda item: item.name)
        st.sidebar.caption(f"Synthetic data. {sample.description}")
        return sample.name, sample.path.read_bytes()

    uploaded = st.sidebar.file_uploader("Meta Ads daily export", type=["csv"])
    return (uploaded.name, uploaded.getvalue()) if uploaded else None


def show_load_errors(result):
    st.error("This file can't be analysed yet. Fix the items below and try again.")
    for issue in result.errors:
        st.error(view.humanize(issue.message))
    if any(issue.code == "missing_values" for issue in result.errors):
        st.info(
            "If blank cells in this export mean zero, tick **Blank Meta action cells mean zero** "
            "in the sidebar."
        )
    for issue in result.warnings:
        st.warning(view.humanize(issue.message))


def period_text(period):
    return f"{period.label} ({period.start:%d %b} to {period.end:%d %b %Y})"


def select_comparison(data, source_name):
    """Return the chosen settings, or None when there are too few periods to compare."""
    st.sidebar.header("Comparison")
    granularity = GRANULARITIES[
        st.sidebar.radio("Period", list(GRANULARITIES), horizontal=True)
    ]
    periods = list_complete_periods(data, granularity)
    if len(periods) < 2:
        st.error(
            f"A {granularity} comparison needs two complete periods, but this data has "
            f"{len(periods)}. Upload a longer date range or choose another period."
        )
        return None

    # Keys change with the options, so a new file or period type resets to the latest two.
    prior = st.sidebar.selectbox(
        "Prior period",
        periods,
        index=len(periods) - 2,
        format_func=period_text,
        key=f"prior-{source_name}-{granularity}",
    )
    current = st.sidebar.selectbox(
        "Current period",
        periods,
        index=len(periods) - 1,
        format_func=period_text,
        key=f"current-{source_name}-{granularity}",
    )
    # An account-level export has only the synthetic label, so there is nothing to split.
    campaigns = sorted(set(data["campaign"]) - {ACCOUNT_LEVEL_CAMPAIGN})
    campaign = st.sidebar.selectbox(
        "Campaign",
        [ALL_CAMPAIGNS, *campaigns],
        key=f"campaign-{source_name}",
    )
    threshold = st.sidebar.number_input(
        "Materiality threshold (%)",
        min_value=0.0,
        max_value=99.0,
        value=DEFAULT_MATERIALITY_THRESHOLD * 100,
        step=0.5,
        help="A change of at least this size counts as material.",
    )
    return {
        "granularity": granularity,
        "prior_period": prior,
        "current_period": current,
        "campaign": None if campaign == ALL_CAMPAIGNS else campaign,
        "threshold": threshold / 100,
    }


def show_contributions(diagnosis):
    st.subheader("ROAS contribution")
    steps = view.waterfall_steps(diagnosis)
    if steps is None:
        reason = view.humanize(diagnosis.drivers_undefined_reason or "ROAS could not be compared.")
        st.info(f"The contribution chart isn't available for this comparison. {reason}")
        return

    st.plotly_chart(
        charts.contribution_waterfall(steps),
        config={"displayModeBar": False},
        theme=None,
    )
    st.caption(
        "Green bars add to ROAS and red bars subtract from it. Each label shows ROAS points, "
        "and the bars sum exactly to the ROAS change."
    )
    with st.expander("Contribution table"):
        st.dataframe(pd.DataFrame(view.driver_table(diagnosis)), hide_index=True)


def show_data_quality(load_warnings, diagnosis):
    st.subheader("Data quality")
    messages = [issue.message for issue in load_warnings] + diagnosis.warnings
    groups = view.group_warnings(messages)
    if not groups:
        st.success("No data quality warnings for this comparison.")
    for group, items in groups.items():
        st.warning(f"**{group}**\n\n" + "\n".join(f"- {item}" for item in items))


def read_secrets():
    """Return Streamlit secrets, or nothing when no secrets file exists."""
    try:
        return dict(st.secrets)
    except (StreamlitSecretNotFoundError, FileNotFoundError):
        return {}


def request_ai_diagnosis(evidence, settings, key):
    """Call the model once and keep the result for this exact evidence."""
    runs = st.session_state.get("ai_runs", 0)
    if runs >= service.MAX_RUNS_PER_SESSION:
        st.warning("The AI request limit for this session has been reached.")
        return

    st.session_state["ai_runs"] = runs + 1
    try:
        with st.spinner("Generating AI diagnosis..."):
            result = service.generate_diagnosis(evidence, service.create_provider(settings))
    except AIError as error:
        st.error(f"{error} The calculations on this page are not affected.")
        for issue in getattr(error, "issues", []):
            st.caption(f"Rejected: {issue}")
        return
    st.session_state.setdefault("ai_results", {})[key] = result


def show_ai_diagnosis(evidence, assessable):
    st.subheader("AI diagnosis")
    settings = service.load_settings(read_secrets())
    key = f"{settings.model}:{evidence.fingerprint()}"
    results = st.session_state.get("ai_results", {})

    unavailable = settings.unavailable_reason
    if not assessable:
        unavailable = "AI analysis needs a ROAS comparison, which isn't possible for these periods."
    if unavailable:
        st.info(unavailable)

    # Streamlit reruns this script on every change, so only a click may call the model.
    clicked = st.button(
        "Generate AI Diagnosis", type="primary", disabled=bool(unavailable), key="generate-ai"
    )
    st.caption(
        "Sends only the aggregated evidence below. Raw rows and campaign names are never sent, "
        "and nothing changes in your ad account."
    )
    if not settings.unavailable_reason and settings.tier != service.PAID_TIER:
        st.caption(
            "Free tier: for local development only. Apps available to users in the EEA, "
            "Switzerland or the UK need paid Gemini use."
        )

    if clicked and key not in results:
        request_ai_diagnosis(evidence, settings, key)

    result = st.session_state.get("ai_results", {}).get(key)
    if result:
        st.html(render.ai_diagnosis_html(result, evidence))

    with st.expander("Evidence available to the AI"):
        st.json(evidence.payload(), expanded=False)
    return result


def show_report_button(report_input):
    st.download_button(
        "Download Diagnostic Report",
        data=report.build_report(report_input),
        file_name=report.file_name(report_input.diagnosis),
        mime="text/html",
        on_click="ignore",
        key="download-report",
    )


def show_dashboard(result, diagnosis, source_name, settings):
    currency = result.currency
    scope = settings["campaign"] or ALL_CAMPAIGNS
    single_campaign = settings["campaign"] is not None
    prior_label, current_label = diagnosis.prior_label, diagnosis.current_label
    evidence = build_evidence(
        diagnosis,
        currency,
        [issue.message for issue in result.warnings],
        single_campaign,
    )

    st.caption(f"{source_name} · {scope} · {current_label} compared with {prior_label}")
    # The report must include any AI result from this run, so the button is filled in last.
    report_slot = st.container()
    if not diagnosis.assessable:
        st.warning(
            "ROAS can't be compared for these periods because at least one has no spend. "
            "The figures below show what is available."
        )

    st.subheader("Mathematical diagnosis")
    st.html(render.mathematical_diagnosis_html(view.mathematical_diagnosis(diagnosis, currency)))

    st.subheader("KPI overview")
    st.html(render.kpis_html(view.kpi_cards(diagnosis, currency), prior_label, current_label))

    st.subheader("Diagnostic decision tree")
    st.caption(
        "Dark outlines show the path the diagnostic followed. Grey boxes were calculated but not "
        f"followed. Changes of {view.format_threshold(diagnosis.threshold)} or more are material."
    )
    st.html(render.tree_html(view.build_tree(diagnosis, currency)))

    st.subheader("Findings")
    st.html(render.findings_html(view.build_findings(diagnosis, currency)))

    show_contributions(diagnosis)
    ai_result = show_ai_diagnosis(evidence, diagnosis.assessable)
    show_data_quality(result.warnings, diagnosis)

    dates = result.data["date"]
    with report_slot:
        show_report_button(
            report.ReportInput(
                diagnosis=diagnosis,
                evidence=evidence,
                source_name=source_name,
                scope=scope,
                prior_period=period_text(settings["prior_period"]),
                current_period=period_text(settings["current_period"]),
                data_range=f"{dates.min():%d %b %Y} to {dates.max():%d %b %Y}",
                analysis_date=date.today(),
                currency=currency,
                load_warnings=tuple(issue.message for issue in result.warnings),
                ai_result=ai_result,
            )
        )


def main():
    st.set_page_config(page_title="Funnel Magnet", layout="wide")
    st.html(render.style_tag())
    st.html(render.header_html())
    st.info(view.DECISION_SUPPORT_NOTICE)

    selected = select_source()
    st.sidebar.header("Data settings")
    blanks_are_zero = st.sidebar.checkbox(
        "Blank Meta action cells mean zero",
        value=False,
        help=(
            "Tick only if you know this export leaves zero actions blank. Otherwise blanks "
            "stay missing and are never counted as zero."
        ),
    )

    if selected is None:
        st.info("Upload a Meta Ads daily export in the sidebar, or choose a sample scenario.")
    else:
        source_name, raw = selected
        result = load_data(raw, blanks_are_zero, code_version())
        if not result.ok:
            show_load_errors(result)
        else:
            settings = select_comparison(result.data, source_name)
            if settings is not None:
                try:
                    diagnosis = run_diagnosis(result.data, **settings)
                except ValueError as error:
                    st.error(str(error))
                else:
                    show_dashboard(result, diagnosis, source_name, settings)

    st.caption(f"ESCP MBA prototype, version {__version__}. Use synthetic or anonymized data only.")


main()
