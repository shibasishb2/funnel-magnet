"""Streamlit entry point for the Funnel Magnet diagnostic prototype."""

import streamlit as st

from funnel_magnet import __version__

st.set_page_config(page_title="Funnel Magnet", layout="wide")

st.title("Funnel Magnet")
st.subheader("AI Marketing Performance Diagnostic Agent")
st.info("Setup complete. Diagnostic features will be added in the next stages.")
st.caption(f"ESCP MBA prototype, version {__version__}. Use synthetic or anonymized data only.")
