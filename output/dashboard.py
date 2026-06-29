"""
Layer 4 (Output) — Streamlit "Opportunity Signal Radar" dashboard.
Reads scored opportunities from SQLite and renders a ranked, filterable
review queue with a per-row Next Step column so GMG always knows what
action to take.

Run: streamlit run output/dashboard.py
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from storage.db import fetch_all  # noqa: E402

st.set_page_config(page_title="GMG Opportunity Signal Radar", layout="wide")

# ── Sidebar: action guide ─────────────────────────────────────────────────────
with st.sidebar:
    st.header("Action Guide")
    st.markdown("""
**1 - Active RFP** 🔴
An open solicitation with a future due date.
➜ *Download documents, run bid/no-bid review, assemble team, submit proposal.*

**2 - Predicted** 🟡
Early signal — project is live but no RFP yet.
➜ *Add to watchlist, prepare qualifications, contact agency to signal interest.*

**Expired / Closed** ⚫
Due date passed or bid awarded.
➜ *Note who won; follow up for re-solicitation or sub opportunities.*

**Below relevance gate** ⚪
System scored as not relevant to GMG services.
➜ *Skip unless you see a missed keyword — flag it to the team.*

---
**Score legend**
| Score | Meaning |
|---|---|
| 1.0 | Confirmed active RFP |
| 0.5–0.9 | Strong early signal |
| 0.2–0.5 | Weak signal |
| None | Failed relevance gate |

---
*Refresh data: run `python run_pipeline.py --live` then reload this page.*
""")

# ── Main ──────────────────────────────────────────────────────────────────────
st.title("Opportunity Signal Radar")
st.caption("RFP Signal Detection & Opportunity Scoring System — GMG-3 Practicum")

rows = fetch_all()
if not rows:
    st.warning("No data yet. Run `python run_pipeline.py` first.")
    st.stop()

df = pd.DataFrame(rows)
df["service_types"] = df["service_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))
df["signal_types"] = df["signal_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))

# ── Compute "Next Step" for every row ─────────────────────────────────────────
today = date.today()

def next_step(row) -> str:
    bucket = str(row.get("bucket", ""))
    passed = row.get("passed_gate", 0)
    due_raw = row.get("due_date")

    # Parse due date
    due: date | None = None
    if due_raw:
        try:
            due = datetime.strptime(str(due_raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    if "Active RFP" in bucket:
        if due:
            days = (due - today).days
            if days <= 0:
                return "⚫ Expired — track re-solicitation"
            elif days <= 7:
                return f"🔴 URGENT — submit in {days}d (due {due})"
            elif days <= 21:
                return f"🟠 Bid due in {days}d ({due}) — prepare proposal"
            else:
                return f"🟡 Bid due {due} — schedule team review"
        return "🔴 Active RFP — download docs, run bid/no-bid"

    if "Predicted" in bucket:
        if not passed:
            return "⚪ Below relevance gate — skip"
        return "🟡 Predicted — monitor for RFP release, prep qualifications"

    if "Expired" in bucket or "Closed" in bucket or "Cancelled" in bucket:
        return "⚫ Closed — note winner, watch for re-bid"

    if not passed:
        return "⚪ Below relevance gate — skip"

    return "🟡 Review — verify relevance manually"

df["next_step"] = df.apply(next_step, axis=1)

# ── Metrics ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total signals", len(df))
c2.metric("Passed gate", int(df["passed_gate"].sum()))
c3.metric("Flagged for review", int(df["flagged_for_review"].sum()))
urgent = df["next_step"].str.contains("URGENT", na=False).sum()
c4.metric("Urgent (≤7 days)", int(urgent), delta_color="inverse")

st.divider()

# ── Filters ───────────────────────────────────────────────────────────────────
fc1, fc2, fc3 = st.columns(3)
with fc1:
    show_flagged = st.checkbox("Flagged for review only", value=False)
with fc2:
    bucket_opts = ["All"] + sorted(df["bucket"].dropna().unique().tolist())
    bucket_filter = st.selectbox("Bucket", bucket_opts)
with fc3:
    agency_opts = ["All"] + sorted(df["agency"].dropna().unique().tolist())
    agency_filter = st.selectbox("Agency", agency_opts)

view = df.copy()
if show_flagged:
    view = view[view["flagged_for_review"] == 1]
if bucket_filter != "All":
    view = view[view["bucket"] == bucket_filter]
if agency_filter != "All":
    view = view[view["agency"] == agency_filter]

# ── Table ─────────────────────────────────────────────────────────────────────
st.subheader(f"Ranked Opportunity List ({len(view)} records)")

display_cols = [
    "next_step", "agency", "title", "bucket", "due_date",
    "rfp_likelihood", "service_types", "signal_types",
    "gate_reason", "source_url",
]
display_cols = [c for c in display_cols if c in view.columns]

st.dataframe(
    view[display_cols].sort_values("rfp_likelihood", ascending=False, na_position="last"),
    use_container_width=True,
    hide_index=True,
    column_config={
        "next_step": st.column_config.TextColumn("Next Step", width="large"),
        "rfp_likelihood": st.column_config.NumberColumn("Score", format="%.2f"),
        "source_url": st.column_config.LinkColumn("Source URL"),
        "due_date": st.column_config.TextColumn("Due Date"),
    },
)
