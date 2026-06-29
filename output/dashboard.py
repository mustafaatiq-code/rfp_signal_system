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

# Ordered most-specific → most-general so the first match wins
_WORK_TYPE_RULES = [
    # CEI / Construction Engineering
    (["construction engineering", "cei", "construction inspection", "construction management at risk", "cmar", "construction manager at risk"], "Construction Engineering & Inspection (CEI)"),
    # A&E / Design
    (["design-build", "design build", "progressive design"], "Design-Build Services"),
    (["a&e", "architecture", "engineering services", "design services", "professional services", "rfq", "prequalif"], "A&E / Engineering Services"),
    # Road / Pavement
    (["lmig", "local maintenance", "local road assistance", "lra", "resurfacing", "milling", "overlay", "asphalt", "pavement"], "Road Resurfacing / Pavement Work"),
    (["road widening", "widening", "lane addition", "road expansion"], "Road Widening"),
    (["road reconstruction", "street reconstruction", "reconstruction"], "Road Reconstruction"),
    # Bridge
    (["bridge replacement", "bridge construction"], "Bridge Replacement / Construction"),
    (["bridge repair", "bridge rehabilitation", "bridge inspection"], "Bridge Repair / Rehabilitation"),
    (["bridge", "culvert"], "Bridge / Culvert Work"),
    # Pedestrian / Bike
    (["sidewalk", "ped ramp", "ada ramp", "accessibility"], "Sidewalk / ADA Accessibility"),
    (["pedestrian", "crosswalk", "multiuse trail", "multi-use trail", "shared-use path", "greenway", "bike"], "Pedestrian & Bicycle Infrastructure"),
    # Traffic
    (["traffic signal", "sigops", "signal operations", "traffic operations"], "Traffic Signal / Operations"),
    (["traffic safety", "safe streets", "ss4a"], "Traffic Safety / Safe Streets Program"),
    (["traffic study", "traffic analysis", "traffic engineering"], "Traffic Engineering Study"),
    (["striping", "pavement marking", "road marking"], "Pavement Marking / Striping"),
    # Drainage / Stormwater
    (["stormwater", "drainage", "outfall", "retention pond", "detention"], "Stormwater / Drainage Infrastructure"),
    # Intersection / Access
    (["intersection improvement", "roundabout", "access management", "interchange"], "Intersection / Interchange Improvement"),
    # Corridor / Transportation Planning
    (["corridor study", "corridor plan", "corridor improvement"], "Corridor Study / Improvement"),
    (["transportation plan", "long range", "comprehensive plan", "mpo", "tip amendment"], "Transportation Planning"),
    (["transit", "bus", "rail", "commuter", "brt", "bus rapid"], "Transit / Rail Infrastructure"),
    # GDOT Major Projects (catch-all)
    (["gdot-major", "arcgis hub", "cei solicitation status unverified"], "GDOT Active Project — CEI/A&E Opportunity"),
    # Utilities co-located with roads
    (["water main", "waterline", "water system", "sewer", "wastewater", "wrf", "wwtp"], "Water / Sewer (road co-location)"),
    # SPLOST / Capital programs
    (["splost", "tsplost", "e-splost", "cip", "capital improvement"], "SPLOST / Capital Program"),
    # Right-of-way
    (["right-of-way", "row support", "roe"], "Right-of-Way Services"),
    # Misc transportation
    (["guardrail", "traffic sign", "street sign", "signal sign"], "Safety Hardware Installation"),
    (["grading", "earthwork", "site preparation"], "Grading / Earthwork"),
]


def _work_type(title: str, status_line: str = "") -> str:
    text = (title + " " + status_line).lower()
    for keywords, label in _WORK_TYPE_RULES:
        if any(kw in text for kw in keywords):
            return label
    return "General Transportation / Infrastructure"

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
""")

    st.divider()
    if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
        import subprocess, sys
        with st.spinner("Running pipeline — this takes ~60 seconds…"):
            result = subprocess.run(
                [sys.executable, "run_pipeline.py", "--live"],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True, text=True, timeout=300,
            )
        if result.returncode == 0:
            st.success("Pipeline complete — data refreshed!")
        else:
            st.error("Pipeline error — check terminal for details.")
            st.code(result.stderr[-1000:] if result.stderr else "no output")
        st.rerun()

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
df["work_type"] = df.apply(
    lambda r: _work_type(str(r.get("title", "")), str(r.get("status_line", ""))), axis=1
)

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
    "next_step", "work_type", "agency", "title", "bucket", "due_date",
    "rfp_likelihood", "service_types", "signal_types",
    "gate_reason", "source_url",
]
display_cols = [c for c in display_cols if c in view.columns]

st.dataframe(
    view[display_cols].sort_values("rfp_likelihood", ascending=False, na_position="last"),
    use_container_width=True,
    hide_index=True,
    column_config={
        "next_step": st.column_config.TextColumn("Next Step", width="medium"),
        "work_type": st.column_config.TextColumn("Work Type", width="medium"),
        "rfp_likelihood": st.column_config.NumberColumn("Score", format="%.2f"),
        "source_url": st.column_config.LinkColumn("Source URL"),
        "due_date": st.column_config.TextColumn("Due Date"),
    },
)
