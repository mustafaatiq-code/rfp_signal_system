"""
Layer 4 (Output) — Streamlit "Opportunity Signal Radar" dashboard, matching
the prototype shown in the midterm deck. Reads scored opportunities from
SQLite and renders a ranked, filterable review queue.

Run: streamlit run output/dashboard.py
"""
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from storage.db import fetch_all  # noqa: E402

st.set_page_config(page_title="GMG Opportunity Signal Radar", layout="wide")
st.title("Opportunity Signal Radar")
st.caption("RFP Signal Detection & Opportunity Scoring System — GMG-3 Practicum")

rows = fetch_all()
if not rows:
    st.warning("No data yet. Run `python run_pipeline.py` first.")
    st.stop()

df = pd.DataFrame(rows)
df["service_types"] = df["service_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))
df["signal_types"] = df["signal_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))

col1, col2, col3 = st.columns(3)
col1.metric("Total ingested", len(df))
col2.metric("Passed relevance gate", int(df["passed_gate"].sum()))
col3.metric("Flagged for review (>=50%)", int(df["flagged_for_review"].sum()))

st.divider()

show_flagged_only = st.checkbox("Show only flagged-for-review opportunities", value=False)
view = df[df["flagged_for_review"] == 1] if show_flagged_only else df

cols = ["agency", "title", "year", "bucket", "due_date", "rfp_likelihood",
        "flagged_for_review", "service_types", "signal_types",
        "gate_reason", "source_url"]
cols = [c for c in cols if c in view.columns]  # tolerate pre-migration DBs

st.subheader("Ranked Opportunity List")
st.dataframe(
    view[cols].sort_values("rfp_likelihood", ascending=False, na_position="last"),
    use_container_width=True,
    hide_index=True,
)

st.caption(
    "Bucket 1 = confirmed active RFP with a due date still in the future "
    "(score forced to 1.0). An active RFP whose due date has passed is "
    "reclassified 'Expired RFP (past due)', scored on its merits, and never "
    "flagged. Bucket 2 = predicted from early signals. Records failing the "
    "relevance gate show rfp_likelihood = None and a gate_reason."
)
