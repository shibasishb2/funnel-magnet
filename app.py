"""Streamlit entry point for the Funnel Magnet diagnostic prototype."""

import io
from datetime import date

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from funnel_magnet import __version__, playbook
from funnel_magnet.ai import prioritise, service
from funnel_magnet.ai.evidence import build_evidence
from funnel_magnet.ai.providers import AIError
from funnel_magnet.diagnose import DEFAULT_MATERIALITY_THRESHOLD, run_diagnosis
from funnel_magnet.periods import MONTHLY, WEEKLY, list_complete_periods
from funnel_magnet.sources.meta import ACCOUNT_LEVEL_CAMPAIGN, load_meta_csv
from funnel_magnet.ui import charts, executive, render, report, view
from funnel_magnet.version import code_version

SAMPLE_SOURCE = "Sample scenario"
UPLOAD_SOURCE = "Upload CSV"
ALL_CAMPAIGNS = "All campaigns"
GRANULARITIES = {"Weekly": WEEKLY, "Monthly": MONTHLY}
PRIVACY_NOTICE = (
    "**Demo data only.** Upload synthetic or anonymized exports, never client data or personal "
    "information. The file is read in this app's memory and isn't saved. AI prioritisation sends "
    "Groq only totals and playbook text, never rows, campaign names or file names."
)
# The full decision-support notice is in the report. The page keeps one short line.
PAGE_NOTICE = "Decision support: it shows what changed and what to check. Possible causes are not confirmed."
SESSION_LIMIT_MESSAGE = (
    "This session has used its {limit} AI analyses. The diagnosis below is complete without AI."
)


# The cache is shared by every visitor, so keep it small on a public server. It is keyed
# by the running code too, because an update can arrive without a restart, and an
# upload rejected by older code must then be read again.
@st.cache_data(show_spinner=False, max_entries=20)
def load_data(raw, blank_actions_as_zero, running_code):
    return load_meta_csv(io.BytesIO(raw), blank_actions_as_zero=blank_actions_as_zero)


def select_source():
    """Return (name, raw bytes, synthetic), or None until a file has been uploaded."""
    st.sidebar.header("Data")
    source = st.sidebar.radio("Data source", [SAMPLE_SOURCE, UPLOAD_SOURCE])

    if source == SAMPLE_SOURCE:
        samples = view.load_samples()
        sample = st.sidebar.selectbox("Scenario", samples, format_func=lambda item: item.name)
        st.sidebar.caption(f"Synthetic data. {sample.description}")
        return sample.name, sample.path.read_bytes(), True

    st.sidebar.warning(PRIVACY_NOTICE)
    uploaded = st.sidebar.file_uploader("Meta Ads daily export", type=["csv"])
    return (uploaded.name, uploaded.getvalue(), False) if uploaded else None


def show_load_errors(result):
    st.error("This file can't be analysed yet. Fix the items below and try again.")
    for issue in result.errors:
        st.error(view.humanize(issue.message))
    if any(issue.code == "missing_values" for issue in result.errors):
        st.info(
            "If blank cells in this export mean zero, tick **Blank Meta action cells mean zero** "
            "under Advanced in the sidebar."
        )
    for issue in result.warnings:
        st.warning(view.humanize(issue.message))


def period_text(period):
    return f"{period.label} ({period.start:%d %b} to {period.end:%d %b %Y})"


def select_comparison(data, source_name, box, advanced):
    """Return the chosen settings, or None when there are too few periods to compare.

    The essential choices go in the sidebar's Comparison section and the threshold in Advanced.
    """
    box.header("Comparison")
    granularity = GRANULARITIES[
        box.radio("Period", list(GRANULARITIES), horizontal=True)
    ]
    periods = list_complete_periods(data, granularity)
    if len(periods) < 2:
        st.error(
            f"A {granularity} comparison needs two complete periods, but this data has "
            f"{len(periods)}. Upload a longer date range or choose another period."
        )
        return None

    # Keys change with the options, so a new file or period type resets to the latest two.
    prior = box.selectbox(
        "Prior period",
        periods,
        index=len(periods) - 2,
        format_func=period_text,
        key=f"prior-{source_name}-{granularity}",
    )
    current = box.selectbox(
        "Current period",
        periods,
        index=len(periods) - 1,
        format_func=period_text,
        key=f"current-{source_name}-{granularity}",
    )
    # An account-level export has only the synthetic label, so there is nothing to choose.
    campaigns = sorted(set(data["campaign"]) - {ACCOUNT_LEVEL_CAMPAIGN})
    campaign = ALL_CAMPAIGNS
    if campaigns:
        campaign = box.selectbox(
            "Campaign",
            [ALL_CAMPAIGNS, *campaigns],
            key=f"campaign-{source_name}",
        )
    threshold = advanced.number_input(
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
        "and the bars sum exactly to the ROAS change. In the table, share of movement is each KPI's "
        "part of all ROAS movement in both directions added together, not a share of the net change."
    )
    # Shown directly, because this sits inside the Detailed analysis tabs.
    st.caption("Contribution table")
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


@st.cache_resource
def usage_limiter():
    """One limiter for every visitor, because the AI allowance is shared."""
    return service.UsageLimiter()


def request_ai_priorities(marketing, evidence, settings, key):
    """Call the model once, keep any result for this exact diagnosis, and return messages."""
    runs = st.session_state.get("ai_runs", 0)
    if runs >= service.MAX_RUNS_PER_SESSION:
        return [("warning", SESSION_LIMIT_MESSAGE.format(limit=service.MAX_RUNS_PER_SESSION))]
    blocked = usage_limiter().acquire()
    if blocked:
        return [("warning", blocked)]

    st.session_state["ai_runs"] = runs + 1
    try:
        with st.spinner("Prioritising with AI..."):
            result = prioritise.prioritise(marketing, evidence, service.create_provider(settings))
    except AIError as error:
        messages = [("error", f"{error} The diagnosis below is complete without AI.")]
        issues = getattr(error, "issues", [])
        return messages + ([("details", issues)] if issues else [])
    st.session_state.setdefault("ai_results", {})[key] = result
    return []


def show_ai_priorities(marketing, evidence, assessable):
    """Offer AI prioritisation, and return the diagnosis with it when there is a result."""
    settings = service.load_settings(read_secrets())
    unavailable = settings.unavailable_reason
    if not assessable:
        unavailable = "AI prioritisation needs a ROAS comparison, which isn't possible for these periods."
    elif not marketing.causes:
        unavailable = "There are no possible causes to prioritise for this comparison."
    if unavailable:
        # A button that can never work would look broken, so show only the reason.
        st.caption(unavailable)
        return marketing

    key = f"{settings.provider}:{settings.model}:{evidence.fingerprint()}:{code_version()}"
    feedback = st.session_state.pop("ai_feedback", None)
    if feedback and feedback[0] == key:
        for kind, text in feedback[1]:
            if kind == "details":
                # The reasons are for whoever maintains the checks, so they stay folded away.
                with st.expander("Technical details"):
                    for issue in text:
                        st.caption(issue)
            else:
                getattr(st, kind)(text)

    left = service.MAX_RUNS_PER_SESSION - st.session_state.get("ai_runs", 0)
    if key in st.session_state.get("ai_results", {}):
        return st.session_state["ai_results"][key]
    if left <= 0:
        # The button would do nothing more, so show only the reason.
        st.caption(SESSION_LIMIT_MESSAGE.format(limit=service.MAX_RUNS_PER_SESSION))
        return marketing
    if st.button("Prioritise with AI", type="primary", key="prioritise-ai"):
        # Draw the page again with the outcome, so the count and button are never stale.
        st.session_state["ai_feedback"] = (key, request_ai_priorities(marketing, evidence, settings, key))
        st.rerun()
    note = f" {settings.usage_note}" if settings.usage_note else ""
    st.caption(
        "AI reorders and explains the playbook's checks and causes. It can't add findings, and only "
        f"totals and playbook text are sent.{note} AI analyses left in this session: {left} of "
        f"{service.MAX_RUNS_PER_SESSION}."
    )
    return marketing


def show_report_button(report_input):
    st.download_button(
        "Download Diagnostic Report",
        data=report.build_report(report_input),
        file_name=report.file_name(report_input.diagnosis),
        mime="text/html",
        on_click="ignore",
        key="download-report",
    )


def show_dashboard(result, diagnosis, source_name, settings, synthetic=False):
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

    # One diagnosis object feeds both the page and the report, so they can't disagree.
    real_campaigns = set(result.data["campaign"]) - {ACCOUNT_LEVEL_CAMPAIGN}
    periods = list_complete_periods(result.data, settings["granularity"])
    marketing = playbook.build(
        diagnosis,
        currency,
        several_campaigns=not single_campaign and len(real_campaigns) > 1,
        earlier_periods=any(period.end < settings["prior_period"].start for period in periods),
    )

    # Level 1 is drawn after the AI control runs, so it shows any AI prioritisation.
    st.subheader("Executive diagnosis")
    summary_slot = st.container()
    marketing = show_ai_priorities(marketing, evidence, diagnosis.assessable)
    if marketing.ai:
        st.html(render.ai_status_html(marketing.ai))
    with summary_slot:
        warnings = [issue.message for issue in result.warnings]
        summary = executive.build(diagnosis, marketing, warnings, source_name, synthetic, currency)
        st.html(render.executive_html(summary, prior_label, current_label))

    with st.expander("Detailed analysis"):
        numbers, playbook_tab, quality = st.tabs(["Numbers", "Playbook", "Data quality"])
        with numbers:
            st.subheader("Marketing diagnosis")
            st.html(render.confirmed_findings_html(marketing))

            st.subheader("KPI overview")
            st.html(render.kpis_html(view.kpi_cards(diagnosis, currency), prior_label, current_label))

            st.subheader("Diagnostic decision tree")
            st.caption(
                "Dark outlines show the path the diagnostic followed. Grey boxes were calculated but not "
                f"followed. Changes of {view.format_threshold(diagnosis.threshold)} or more are material."
            )
            st.html(render.tree_html(view.build_tree(diagnosis, currency)))

            show_contributions(diagnosis)
        with playbook_tab:
            st.subheader("Possible causes, checks and actions")
            st.html(render.possible_causes_html(marketing))
            st.html(render.investigations_html(marketing))
            st.html(render.actions_html(marketing))
        with quality:
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
                marketing=marketing,
                synthetic=synthetic,
            )
        )


def main():
    st.set_page_config(page_title="Funnel Magnet", layout="wide")
    st.html(render.style_tag())
    st.html(render.header_html())
    st.info(PAGE_NOTICE)

    selected = select_source()
    # Sidebar order: Data, Comparison, then the rarely needed settings under Advanced.
    comparison = st.sidebar.container()
    advanced = st.sidebar.expander("Advanced")
    blanks_are_zero = False
    if selected is None or not selected[2]:
        # Only an uploaded export can have blank action cells.
        blanks_are_zero = advanced.checkbox(
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
        source_name, raw, synthetic = selected
        result = load_data(raw, blanks_are_zero, code_version())
        if not result.ok:
            show_load_errors(result)
        else:
            settings = select_comparison(result.data, source_name, comparison, advanced)
            if settings is not None:
                try:
                    diagnosis = run_diagnosis(result.data, **settings)
                except ValueError as error:
                    st.error(str(error))
                else:
                    show_dashboard(result, diagnosis, source_name, settings, synthetic)

    st.caption(f"ESCP MBA prototype, version {__version__}. Use synthetic or anonymized data only.")


main()
