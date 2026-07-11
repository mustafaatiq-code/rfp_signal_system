# -*- coding: utf-8 -*-
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
    (["construction engineering", "cei", "construction inspection", "construction management at risk", "cmar", "construction manager at risk"], "CEI / Inspection"),
    (["design-build", "design build", "progressive design"], "Design-Build"),
    (["a&e", "architecture", "engineering services", "design services", "professional services", "rfq", "prequalif"], "A&E / Engineering"),
    (["lmig", "local maintenance", "local road assistance", "lra", "resurfacing", "milling", "overlay", "asphalt", "pavement"], "Road Resurfacing"),
    (["road widening", "widening", "lane addition", "road expansion"], "Road Widening"),
    (["road reconstruction", "street reconstruction", "reconstruction"], "Road Reconstruction"),
    (["bridge replacement", "bridge construction"], "Bridge Replacement"),
    (["bridge repair", "bridge rehabilitation", "bridge inspection"], "Bridge Repair"),
    (["bridge", "culvert"], "Bridge / Culvert"),
    (["sidewalk", "ped ramp", "ada ramp", "accessibility"], "Sidewalk / ADA"),
    (["pedestrian", "crosswalk", "multiuse trail", "multi-use trail", "shared-use path", "greenway", "bike"], "Pedestrian / Bike"),
    (["traffic signal", "sigops", "signal operations", "traffic operations"], "Traffic Signals"),
    (["traffic safety", "safe streets", "ss4a"], "Traffic Safety"),
    (["traffic study", "traffic analysis", "traffic engineering"], "Traffic Study"),
    (["striping", "pavement marking", "road marking"], "Pavement Marking"),
    (["stormwater", "drainage", "outfall", "retention pond", "detention"], "Stormwater"),
    (["intersection improvement", "roundabout", "access management", "interchange"], "Intersection"),
    (["corridor study", "corridor plan", "corridor improvement"], "Corridor Study"),
    (["transportation plan", "long range", "comprehensive plan", "mpo", "tip amendment"], "Transportation Plan"),
    (["transit", "bus", "rail", "commuter", "brt", "bus rapid"], "Transit / Rail"),
    (["gdot-major", "arcgis hub", "cei solicitation status unverified"], "GDOT Project"),
    (["water main", "waterline", "water system", "sewer", "wastewater", "wrf", "wwtp"], "Water / Sewer"),
    (["splost", "tsplost", "e-splost", "cip", "capital improvement"], "SPLOST / Capital"),
    (["right-of-way", "row support", "roe"], "Right-of-Way"),
    (["guardrail", "traffic sign", "street sign", "signal sign"], "Safety Hardware"),
    (["grading", "earthwork", "site preparation"], "Grading / Earthwork"),
]


def _work_type(title: str, status_line: str = "") -> str:
    text = (title + " " + status_line).lower()
    for keywords, label in _WORK_TYPE_RULES:
        if any(kw in text for kw in keywords):
            return label
    return "Transportation (General)"

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

# ── Session state ──────────────────────────────────────────────────────────────
if "selected_id" not in st.session_state:
    st.session_state.selected_id = None

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

# ── Detail view (fires before metrics/filters so back button is at top) ────────
if st.session_state.selected_id is not None:
    if st.button("< Back to Opportunity List", type="primary", use_container_width=True):
        st.session_state.selected_id = None
        st.rerun()

    row = df[df["solicitation_id"].astype(str) == str(st.session_state.selected_id)]
    if not row.empty:
        r = row.iloc[0]

        st.divider()
        st.subheader(r["title"])

        ns = r.get("next_step", "")
        if "URGENT" in ns:
            st.error(ns)
        elif "🟠" in ns:
            st.warning(ns)
        elif "🟡" in ns:
            st.info(ns)
        else:
            st.write(ns)

        st.divider()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Agency", r.get("agency", "—"))
        c2.metric("Work Type", r.get("work_type", "—"))
        c3.metric("Due Date", str(r.get("due_date") or "Not specified"))
        score = r.get("rfp_likelihood")
        c4.metric("RFP Score", f"{score:.2f}" if score is not None else "N/A")

        st.divider()

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Bucket**")
            st.write(r.get("bucket", "—"))
            st.markdown("**Service Types**")
            st.write(r.get("service_types") or "—")
            st.markdown("**Signal Types**")
            st.write(r.get("signal_types") or "—")
        with col_b:
            st.markdown("**Solicitation ID**")
            st.write(r.get("solicitation_id", "—"))
            st.markdown("**Relevance Gate**")
            gate = r.get("gate_reason", "")
            st.write("✅ PASS" if r.get("passed_gate") else f"❌ {gate}")
            st.markdown("**Year**")
            st.write(str(r.get("year", "—")))

        st.divider()
        st.markdown("**Status / Notes**")
        st.write(r.get("status_line", "—"))

        url = r.get("source_url", "")
        if url:
            st.link_button("🔗 Open Source / Bid Portal", url, type="primary")

        st.stop()

# ── Metrics ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total signals", len(df))
c2.metric("Passed gate", int(df["passed_gate"].sum()))
c3.metric("Flagged for review", int(df["flagged_for_review"].sum()))
urgent = df["next_step"].str.contains("URGENT", na=False).sum()
c4.metric("Urgent (≤7 days)", int(urgent), delta_color="inverse")

st.divider()

# ── Filters ───────────────────────────────────────────────────────────────────
with st.expander("Filters", expanded=True):
    row1a, row1b, row1c = st.columns(3)
    with row1a:
        keyword = st.text_input("Search title", placeholder="e.g. resurfacing, bridge, CEI")
    with row1b:
        bucket_opts = ["All"] + sorted(df["bucket"].dropna().unique().tolist())
        bucket_filter = st.selectbox("Bucket", bucket_opts)
    with row1c:
        agency_opts = ["All"] + sorted(df["agency"].dropna().unique().tolist())
        agency_filter = st.selectbox("Agency", agency_opts)

    row2a, row2b, row2c, row2d = st.columns(4)
    with row2a:
        wt_opts = ["All"] + sorted(df["work_type"].dropna().unique().tolist())
        work_type_filter = st.selectbox("Work Type", wt_opts)
    with row2b:
        min_score = st.slider("Min RFP Score", 0.0, 1.0, 0.0, 0.05)
    with row2c:
        due_window_opts = ["Any", "Overdue / No date", "Due in 7 days", "Due in 30 days", "Due in 90 days"]
        due_window = st.selectbox("Due Date Window", due_window_opts)
    with row2d:
        show_flagged = st.checkbox("Flagged for review only", value=False)

view = df.copy()
if keyword.strip():
    kw = keyword.strip().lower()
    view = view[view["title"].str.lower().str.contains(kw, na=False)]
if bucket_filter != "All":
    view = view[view["bucket"] == bucket_filter]
if agency_filter != "All":
    view = view[view["agency"] == agency_filter]
if work_type_filter != "All":
    view = view[view["work_type"] == work_type_filter]
if min_score > 0.0:
    view = view[view["rfp_likelihood"].fillna(0) >= min_score]
if show_flagged:
    view = view[view["flagged_for_review"] == 1]
if due_window != "Any":
    def _due_date_obj(val):
        try:
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    view["_due_obj"] = view["due_date"].apply(_due_date_obj)
    if due_window == "Due in 7 days":
        view = view[view["_due_obj"].apply(lambda d: d is not None and today <= d <= today + pd.Timedelta(days=7))]
    elif due_window == "Due in 30 days":
        view = view[view["_due_obj"].apply(lambda d: d is not None and today <= d <= today + pd.Timedelta(days=30))]
    elif due_window == "Due in 90 days":
        view = view[view["_due_obj"].apply(lambda d: d is not None and today <= d <= today + pd.Timedelta(days=90))]
    elif due_window == "Overdue / No date":
        view = view[view["_due_obj"].apply(lambda d: d is None or d < today)]
    view = view.drop(columns=["_due_obj"])

# ── List view ─────────────────────────────────────────────────────────────────
sorted_view = view.sort_values("rfp_likelihood", ascending=False, na_position="last")

st.subheader(f"Ranked Opportunity List  —  {len(sorted_view)} records")

for _, r in sorted_view.iterrows():
    ns = str(r.get("next_step", ""))
    title = str(r.get("title", "Untitled"))
    agency = str(r.get("agency", "") or "")
    work_type = str(r.get("work_type", "") or "")
    due = str(r.get("due_date", "") or "Not specified")
    score = r.get("rfp_likelihood")
    score_str = f"{score:.2f}" if score is not None else "N/A"

    with st.container(border=True):
        left, right = st.columns([8, 1])
        with left:
            st.markdown(f"**{title}**")
            st.caption(f"{ns}  &nbsp;|&nbsp;  {agency}  &nbsp;|&nbsp;  {work_type}  &nbsp;|&nbsp;  Due: {due}  &nbsp;|&nbsp;  Score: {score_str}")
        with right:
            if st.button("View", key=f"row_{r['solicitation_id']}", use_container_width=True):
                st.session_state.selected_id = r["solicitation_id"]
                st.rerun()
