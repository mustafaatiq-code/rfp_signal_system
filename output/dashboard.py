# -*- coding: utf-8 -*-
"""
Layer 4 (Output) — Streamlit "Opportunity Signal Radar" dashboard.
Reads scored opportunities from SQLite and renders a ranked, filterable
review queue with a per-row Next Step column so GMG always knows what
action to take.

Run: streamlit run output/dashboard.py
"""
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from storage.db import fetch_all, refresh_expired_buckets  # noqa: E402

# Ordered most-specific → most-general so the first match wins
_WORK_TYPE_RULES = [
    (["construction engineering", "cei", "construction inspection", "construction management at risk", "cmar", "construction manager at risk"], "Construction Engineering & Inspection"),
    (["design-build", "design build", "progressive design"], "Design-Build"),
    (["a&e", "architecture", "engineering services", "design services", "professional services", "rfq", "prequalif"], "Architecture & Engineering"),
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
    (["striping", "restriping", "pavement marking", "road marking", "centerline", "centreline", "edge line", "thermoplastic", "lane marking"], "Pavement Marking"),
    (["street lighting", "roadway lighting", "streetlight", "pedestrian lighting", "highway lighting"], "Street Lighting"),
    (["safe streets", "safe roads", "road safety", "ss4a", "safe routes"], "Road Safety"),
    (["dirt road", "unpaved road", "gravel road", "chip seal", "microsurfacing", "slurry seal", "crack seal"], "Road Paving"),
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
    return "General Transportation"

st.set_page_config(page_title="GMG Opportunity Signal Radar", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
/* Remove Streamlit's default top padding */
.block-container {
    padding-top: 3rem !important;
    padding-bottom: 1rem !important;
}
/* Shrink the top header bar height so it doesn't take much space */
header[data-testid="stHeader"] { min-height: 2rem !important; }
/* Make the collapsed sidebar control always visible and easy to click */
[data-testid="collapsedControl"] { visibility: visible !important; display: flex !important; }
/* Compact bordered cards */
section[data-testid="stVerticalBlockBorderWrapper"] > div:first-child {
    padding-top: 6px !important;
    padding-bottom: 6px !important;
    padding-left: 12px !important;
    padding-right: 12px !important;
}
/* Shrink gap between cards */
section[data-testid="stVerticalBlockBorderWrapper"] {
    margin-bottom: 4px !important;
}
/* Tighter heading spacing */
h2, h3 { margin-top: 0.3rem !important; margin-bottom: 0.3rem !important; }
/* Hide Streamlit's auto-added anchor link icon on headings */
h2 a, h3 a { display: none !important; }
/* Always-visible dark scrollbar on cards and inner containers */
* {
    scrollbar-width: thin;
    scrollbar-color: #666 transparent;
}
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb { background: #666; border-radius: 3px; }
*::-webkit-scrollbar-thumb:hover { background: #444; }
</style>
""", unsafe_allow_html=True)

# ── Filter state (initialized before sidebar so sidebar widgets can use _fk) ──
if "filter_reset" not in st.session_state:
    st.session_state.filter_reset = 0
_fk = st.session_state.filter_reset

_pf_defaults = {"_pf_keyword": "", "_pf_bucket": "All", "_pf_agency": "All",
                "_pf_worktype": "All", "_pf_due": "Any", "_pf_score": 0.0,
                "_pf_hide_exp": True, "_pf_flagged": False, "_pf_show_all": False,
                "_pf_urgent": False}
for k, v in _pf_defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar: action guide + display filters ───────────────────────────────────
with st.sidebar:
    st.markdown("**Action Guide**")

    st.markdown("🔴 **1 - Active RFP**")
    st.caption("Open solicitation with a future due date. Download documents, run bid/no-bid review, assemble team, and submit proposal.")

    st.markdown("🟡 **2 - Predicted**")
    st.caption("Early signal — project is live but no RFP yet. Add to watchlist, prepare qualifications, contact agency to signal interest.")

    st.markdown("⚠️ **Construction contract (indicator)**")
    st.caption("Direct construction bid — not a GMG service. Signals that a CEI, inspection, or A&E design contract will follow. Monitor the agency for the follow-on RFP.")

    st.markdown("⚫ **Expired RFP / Awarded / Cancelled**")
    st.caption("Opportunity closed. Note who won; follow up for re-solicitation or subcontracting opportunities.")

    st.markdown("⚪ **Below relevance gate**")
    st.caption("System scored as not relevant to GMG services. Skip unless you see a missed keyword — flag it to the team.")

    st.divider()
    st.markdown("**Score Legend**")
    st.caption("**1.00** — Confirmed open solicitation")
    st.caption("**0.70–0.99** — Strong early signal")
    st.caption("**0.50–0.69** — Moderate signal, monitor")
    st.caption("**< 0.50** — Weak signal or failed gate")

    st.divider()
    st.markdown("**Scoring Formula**")
    st.caption("Score = weighted sum of four components:")

    st.markdown("**Signal Count** (35%)")
    st.caption("Distinct signal types detected (max 4). 4→1.00 · 3→0.75 · 2→0.50 · 1→0.25 · 0→0.00")

    st.markdown("**Recency** (30%)")
    st.caption("Exponential decay by age. Current year→1.00 · 1 yr→0.55 · 2 yrs→0.30 · 3 yrs→0.17")

    st.markdown("**Source Strength** (20%)")
    st.caption("Highest-weight signal found. SPLOST/RFP→1.00 · Bond/Capital→0.90 · State Budget→0.85 · Legislation→0.70 · Planning→0.60 · Political→0.50 · News→0.30")

    st.markdown("**Pipeline Stage** (15%)")
    st.caption("Active RFP→1.00 · Predicted→0.50 · Unknown→0.30 · Expired/Closed→0.00")

    st.divider()
    st.markdown("**Display Filters**")
    hide_expired = st.checkbox("Hide expired & closed", value=st.session_state["_pf_hide_exp"], key=f"he_{_fk}")
    st.session_state["_pf_hide_exp"] = hide_expired
    show_flagged = st.checkbox("High priority only (score ≥ 0.50)", value=st.session_state["_pf_flagged"], key=f"fl_{_fk}")
    st.session_state["_pf_flagged"] = show_flagged
    show_all = st.checkbox("Show all signals (incl. below-gate)", value=st.session_state["_pf_show_all"], key=f"sa_{_fk}")
    st.session_state["_pf_show_all"] = show_all
    urgent_only = st.checkbox("🚨 Urgent only (≤ 7 days)", value=st.session_state["_pf_urgent"], key=f"urg_{_fk}")
    st.session_state["_pf_urgent"] = urgent_only
    if st.button("Clear all filters", use_container_width=True):
        for k, v in _pf_defaults.items():
            st.session_state[k] = v
        st.session_state.filter_reset += 1
        st.rerun()

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
st.markdown("## Opportunity Signal Radar")
st.caption("RFP Signal Detection & Opportunity Scoring System — GMG-3 Practicum")

try:
    refresh_expired_buckets()
except Exception:
    pass  # DB is read-only on Streamlit Cloud — fall back to in-memory fix below

rows = fetch_all()
if not rows:
    st.warning("No data yet. Run `python run_pipeline.py` first.")
    st.stop()

df = pd.DataFrame(rows)
df["service_types"] = df["service_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))
df["signal_types"] = df["signal_types"].apply(lambda s: ", ".join(json.loads(s or "[]")))

# In-memory bucket correction for read-only deployments (Streamlit Cloud):
# reclassify Active RFPs with past due dates and Awarded/Cancelled records.
_today_str = date.today().isoformat()
def _fix_bucket(row):
    b = row["bucket"]
    if b in ("Awarded", "Cancelled"):
        return "Expired RFP (past due)"
    if b == "1 - Active RFP":
        due = row.get("due_date")
        if due and str(due)[:10] < _today_str:
            return "Expired RFP (past due)"
    return b
df["bucket"] = df.apply(_fix_bucket, axis=1)

df["work_type"] = df.apply(
    lambda r: _work_type(str(r.get("title", "")), str(r.get("status_line", ""))), axis=1
)

# ── Session state ──────────────────────────────────────────────────────────────
if "selected_id" not in st.session_state:
    st.session_state.selected_id = None

# ── Compute "Next Step" for every row ─────────────────────────────────────────
today = date.today()

# Work types that are direct construction contracts (not GMG's professional
# services). These are valuable pipeline signals — a paving or bridge contract
# means CEI/inspection/design work will follow — but GMG cannot bid on them
# directly. The next_step labels reflect this distinction.
_INDICATOR_WORK_TYPES = {
    "Road Resurfacing", "Road Widening", "Road Reconstruction",
    "Bridge Replacement", "Bridge Repair", "Bridge / Culvert",
    "Grading / Earthwork", "Road Paving", "Pavement Marking",
}


def next_step(row) -> str:
    bucket = str(row.get("bucket", ""))
    passed = row.get("passed_gate", 0)
    due_raw = row.get("due_date")
    wt = str(row.get("work_type", ""))

    # Parse due date
    due: date | None = None
    if due_raw:
        try:
            due = datetime.strptime(str(due_raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    # Construction contracts: GMG can't bid directly but they signal follow-on work
    if wt in _INDICATOR_WORK_TYPES and passed:
        if "Active RFP" in bucket and due and (due - today).days > 0:
            return f"⚠️ Construction contract (indicator) — watch for CEI/A&E RFP (closes {due})"
        if "Active RFP" in bucket:
            return "⚠️ Construction contract (indicator) — watch for related CEI/inspection RFP"
        return "⚠️ Construction signal — monitor for follow-on CEI/A&E/inspection RFP"

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
    back_col, _ = st.columns([2, 8])
    with back_col:
        if st.button("< Back to List", type="primary", use_container_width=True):
            st.session_state.selected_id = None
            st.rerun()

    row = df[df["solicitation_id"].astype(str) == str(st.session_state.selected_id)]
    if not row.empty:
        # Convert NaN → None so no widget ever receives a float NaN
        _raw = row.iloc[0]
        r = {k: (None if pd.isna(v) else v) if isinstance(v, float) else v
             for k, v in _raw.items()}
        ns = r.get("next_step") or ""
        score_val = r.get("rfp_likelihood")  # None if NULL/NaN
        bucket_val = str(r.get("bucket") or "")
        url = str(r.get("source_url") or "")

        # ── Compact title + status bar ────────────────────────────────────────
        st.markdown(f"### {r['title']}")
        if "URGENT" in ns:
            st.error(ns)
        elif "🟠" in ns:
            st.warning(ns)
        elif "🟡" in ns:
            st.info(ns)
        else:
            st.caption(ns)

        # ── Two-column fact grid ──────────────────────────────────────────────
        gate = r.get("gate_reason", "")
        gate_str = "PASS" if r.get("passed_gate") else f"FAIL — {gate}"
        status_note = str(r.get("status_line") or "—")
        score_str = f"{score_val:.2f}" if score_val is not None else "N/A"

        left_fields = [
            ("Agency",          str(r.get("agency") or "—")),
            ("Work Type",       str(r.get("work_type") or "—")),
            ("Due Date",        str(r.get("due_date") or "Not specified")),
            ("RFP Score",       score_str),
            ("Bucket",          bucket_val or "—"),
            ("Solicitation ID", str(r.get("solicitation_id") or "—")),
        ]
        right_fields = [
            ("Service Types",  str(r.get("service_types") or "—")),
            ("Signal Types",   str(r.get("signal_types") or "—")),
            ("Relevance Gate", gate_str),
            ("Year",           str(r.get("year") or "—")),
            ("Status / Notes", status_note),
            ("Source URL",     f'<a href="{url}" target="_blank">{url}</a>' if url else "—"),
        ]

        def _rows(pairs):
            return "".join(
                f"<tr>"
                f"<td style='padding:5px 16px 5px 0;color:#888;font-size:0.85rem;"
                f"white-space:nowrap;vertical-align:top'><b>{k}</b></td>"
                f"<td style='padding:5px 0;font-size:0.92rem;vertical-align:top'>{v}</td>"
                f"</tr>"
                for k, v in pairs
            )

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>{_rows(left_fields)}</table>",
                unsafe_allow_html=True)
        with col_r:
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>{_rows(right_fields)}</table>",
                unsafe_allow_html=True)

        # ── Budget threshold check ─────────────────────────────────────────────
        _MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(M|K|million|thousand)?", re.IGNORECASE)
        _budget_text = f"{r.get('title', '')} {r.get('status_line', '')}"
        _amounts: list[float] = []
        for _m in _MONEY_RE.finditer(_budget_text):
            _base = float(_m.group(1).replace(",", ""))
            _unit = (_m.group(2) or "").lower()
            if _unit in ("m", "million"):
                _base *= 1_000_000
            elif _unit in ("k", "thousand"):
                _base *= 1_000
            _amounts.append(_base)
        if _amounts:
            _below = [a for a in _amounts if a < 30_000]
            _amt_str = ", ".join(f"${a:,.0f}" for a in _amounts)
            if _below and all(a < 30_000 for a in _amounts):
                st.warning(
                    f"**Budget notice:** Detected project value(s) {_amt_str} appear below the "
                    f"$30,000 minimum threshold. Confirm full project scope before pursuing — "
                    f"the value shown may reflect a single task order or phase, not total contract value."
                )
            else:
                st.caption(f"Detected budget mention(s): {_amt_str}")

        # ── Score breakdown with per-entry explanation ────────────────────────
        if score_val is not None and score_val < 1.0:
            st.markdown("<p style='margin-top:14px;margin-bottom:4px;font-size:1rem'>"
                        "<b>Score Breakdown</b></p>", unsafe_allow_html=True)
            _SOURCE_W = {
                "SPLOST": 1.0, "Bond Issuance": 0.9, "Capital Budget": 0.9,
                "State Budget Session": 0.85, "Legislation": 0.7,
                "Planning": 0.6, "Political Meetings": 0.5,
                "News / Press": 0.3, "Active RFP": 1.0,
            }
            _PIPE_W = {
                "1 - Active RFP": 1.0, "2 - Predicted": 0.5,
                "Awarded": 0.0, "Cancelled": 0.0,
                "Expired RFP (past due)": 0.0, "Unknown": 0.3,
            }
            raw_signals = [s.strip() for s in str(r.get("signal_types") or "").split(",") if s.strip()]
            n_sig = len(raw_signals)
            signal_norm = round(min(n_sig, 4) / 4, 4)
            try:
                _yr = r.get("year")
                age = max(date.today().year - int(_yr), 0) if _yr and str(_yr) != "nan" else 0
            except (ValueError, TypeError):
                age = 0
            recency = round(math.exp(-0.6 * age), 4)
            raw_sw = max((_SOURCE_W.get(s, 0.3) for s in raw_signals), default=0.2) if raw_signals else 0.2
            if "Predicted" in bucket_val:
                non_rfp = [s for s in raw_signals if s != "Active RFP"]
                source_w = round(max((_SOURCE_W.get(s, 0.3) for s in non_rfp), default=0.6) if non_rfp else 0.6, 4)
                sw_note = (
                    f"'Active RFP' excluded for Predicted bucket; strongest remaining signal: "
                    f"'{max(non_rfp, key=lambda s: _SOURCE_W.get(s,0.3))}' → weight {source_w:.2f}"
                    if non_rfp else
                    "Only 'Active RFP' detected — capped to 0.60 (Planning floor) for Predicted bucket"
                )
            else:
                source_w = round(raw_sw, 4)
                strongest = max(raw_signals, key=lambda s: _SOURCE_W.get(s, 0.3)) if raw_signals else "—"
                sw_note = f"Strongest signal: '{strongest}' → weight {source_w:.2f}"
            pipe_w = round(_PIPE_W.get(bucket_val, 0.3), 4)

            explanations = [
                f"{n_sig} signal type(s) detected out of 4 maximum: {', '.join(raw_signals) or '—'}",
                f"Record from {r.get('year', '?')} — {age} year(s) old (score decays 45% per year)",
                sw_note,
                f"Bucket '{bucket_val}' → pipeline stage weight {pipe_w:.2f}",
            ]
            components = [
                ("Signal Count",    signal_norm, 0.35),
                ("Recency",         recency,     0.30),
                ("Source Strength", source_w,    0.20),
                ("Pipeline Stage",  pipe_w,      0.15),
            ]
            total = sum(v * w for _, v, w in components)
            bar_rows = ""
            for (label, val, wt), why in zip(components, explanations):
                pct = int(val * 100)
                contrib = val * wt
                bar_rows += (
                    f"<tr>"
                    f"<td style='padding:5px 12px 5px 0;font-size:0.88rem;white-space:nowrap;vertical-align:top'>"
                    f"<b>{label}</b></td>"
                    f"<td style='padding:5px 8px;font-size:0.88rem;text-align:right;vertical-align:top'>{val:.2f}</td>"
                    f"<td style='padding:5px 8px;font-size:0.88rem;color:#666;vertical-align:top'>×{wt}</td>"
                    f"<td style='width:140px;padding:5px 8px;vertical-align:middle'>"
                    f"  <div style='background:#e0e0e0;border-radius:4px;height:10px'>"
                    f"    <div style='background:#4CAF50;width:{pct}%;height:10px;border-radius:4px'></div>"
                    f"  </div></td>"
                    f"<td style='padding:5px 8px;font-size:0.88rem;text-align:right;vertical-align:top'>"
                    f"<b>{contrib:.4f}</b></td>"
                    f"<td style='padding:5px 0 5px 12px;font-size:0.8rem;color:#666;vertical-align:top'>{why}</td>"
                    f"</tr>"
                )
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>"
                f"<tr style='border-bottom:1px solid #ddd'>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 12px 3px 0;text-align:left'>Factor</th>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 8px;text-align:right'>Value</th>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 8px'>Weight</th>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 8px'>Bar</th>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 8px;text-align:right'>Contribution</th>"
                f"<th style='font-size:0.8rem;color:#888;padding:3px 0 3px 12px'>Why this value?</th></tr>"
                f"{bar_rows}"
                f"<tr style='border-top:1px solid #ddd'>"
                f"<td colspan='4' style='padding-top:6px;font-size:0.95rem'><b>Final Score</b></td>"
                f"<td style='padding-top:6px;font-size:0.95rem;text-align:right'><b>{total:.2f}</b></td>"
                f"<td></td></tr>"
                f"</table>",
                unsafe_allow_html=True,
            )
        elif score_val == 1.0:
            st.caption("Active RFP — score fixed at 1.00 (confirmed open solicitation).")

        if url:
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
            st.link_button("Open Source / Bid Portal", url, type="primary")

    st.stop()

# ── Metrics ───────────────────────────────────────────────────────────────────
_EXPIRED_BUCKETS = {"Expired RFP (past due)", "Awarded", "Cancelled"}
_df_passed  = df[df["passed_gate"] == 1]
_n_below    = int((df["passed_gate"] == 0).sum())
_n_active   = int((_df_passed["bucket"] == "1 - Active RFP").sum())
_n_pred     = int((_df_passed["bucket"] == "2 - Predicted").sum())
_n_all_exp  = int(df["bucket"].isin(_EXPIRED_BUCKETS).sum())   # across ALL records
_n_remaining = len(df) - _n_all_exp
_n_urgent   = int(_df_passed["next_step"].str.contains("URGENT", na=False).sum())

# Row 1 — funnel: Total → Expired → Remaining
m1, m2, m3 = st.columns(3)
m1.metric("Total Signals", len(df))
m2.metric("⚫ Expired / Closed", _n_all_exp)
m3.metric("Remaining Signals", _n_remaining)

# Row 2 — breakdown of remaining: gate result + actionable counts
st.caption("Breakdown of remaining signals:")
_n_passed_live  = len(_df_passed) - int(_df_passed["bucket"].isin(_EXPIRED_BUCKETS).sum())
_n_below_live   = _n_below - int(((df["passed_gate"] == 0) & df["bucket"].isin(_EXPIRED_BUCKETS)).sum())
b1, b2, b3, b4 = st.columns(4)
b1.metric("🔴 Active RFPs", _n_active)
b2.metric("🟡 Predicted", _n_pred)
b3.metric("✅ Passed Relevance Gate", _n_passed_live)
b4.metric("⚪ Below Relevance Gate", _n_below_live)

# ── Filters (one row — display filters are in the sidebar) ────────────────────
def _sel_idx(opts, saved):
    try:
        return opts.index(saved)
    except ValueError:
        return 0

f1, f2, f3, f4, f5, f6 = st.columns([2.5, 1.5, 1.5, 1.5, 1.5, 1.5])
with f1:
    keyword = st.text_input("Search title", value=st.session_state["_pf_keyword"],
                             placeholder="keyword…", key=f"kw_{_fk}")
    st.session_state["_pf_keyword"] = keyword
with f2:
    bucket_opts = ["All"] + sorted(df["bucket"].dropna().unique().tolist())
    bucket_filter = st.selectbox("Bucket", bucket_opts,
                                  index=_sel_idx(bucket_opts, st.session_state["_pf_bucket"]),
                                  key=f"bkt_{_fk}")
    st.session_state["_pf_bucket"] = bucket_filter
with f3:
    agency_opts = ["All"] + sorted(df["agency"].dropna().unique().tolist())
    agency_filter = st.selectbox("Agency", agency_opts,
                                  index=_sel_idx(agency_opts, st.session_state["_pf_agency"]),
                                  key=f"agc_{_fk}")
    st.session_state["_pf_agency"] = agency_filter
with f4:
    wt_opts = ["All"] + sorted(df["work_type"].dropna().unique().tolist())
    work_type_filter = st.selectbox("Work Type", wt_opts,
                                     index=_sel_idx(wt_opts, st.session_state["_pf_worktype"]),
                                     key=f"wt_{_fk}")
    st.session_state["_pf_worktype"] = work_type_filter
with f5:
    due_window_opts = ["Any", "Due in 7 days", "Due in 30 days", "Due in 90 days", "Overdue / No date"]
    due_window = st.selectbox("Due Date", due_window_opts,
                               index=_sel_idx(due_window_opts, st.session_state["_pf_due"]),
                               key=f"dw_{_fk}")
    st.session_state["_pf_due"] = due_window
with f6:
    min_score = st.slider("Min Score", 0.0, 1.0, st.session_state["_pf_score"], 0.05, key=f"ms_{_fk}")
    st.session_state["_pf_score"] = min_score

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
if urgent_only:
    view = view[view["next_step"].str.contains("URGENT", na=False)]
if hide_expired and bucket_filter not in _EXPIRED_BUCKETS:
    view = view[~view["bucket"].isin(_EXPIRED_BUCKETS)]
if due_window != "Any":
    def _due_date_obj(val):
        try:
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    # Use assign() to avoid pandas 3.0 Copy-on-Write ChainedAssignmentError
    view = view.assign(_due_obj=view["due_date"].apply(_due_date_obj))
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

above_gate = sorted_view[sorted_view["passed_gate"] == 1]
# Below-gate expander shows only non-expired records (expired have their own filter).
_below_all = (
    df[(df["passed_gate"] == 0) & ~df["bucket"].isin(_EXPIRED_BUCKETS)]
    .sort_values("rfp_likelihood", ascending=False, na_position="last")
)
below_gate = sorted_view[sorted_view["passed_gate"] == 0] if show_all else _below_all

# When "show all" is on, merge below-gate rows at the end of the main list
main_list = sorted_view if show_all else above_gate
list_label = f"Ranked Opportunity List  —  {len(main_list)} records"
if show_all and len(below_gate) > 0:
    list_label += f"  ({len(below_gate)} below gate, shown muted)"

hdr_left, hdr_right = st.columns([6, 1])
hdr_left.subheader(list_label)
with hdr_right:
    export_cols = ["title", "agency", "work_type", "bucket", "due_date", "rfp_likelihood", "next_step", "source_url"]
    export_cols = [c for c in export_cols if c in sorted_view.columns]
    csv_bytes = main_list[export_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv_bytes, "opportunities.csv", "text/csv", use_container_width=True)

def _render_row(r, muted: bool = False):
    ns = str(r.get("next_step", ""))
    title = str(r.get("title", "Untitled"))
    agency = str(r.get("agency", "") or "")
    work_type = str(r.get("work_type", "") or "")
    due = str(r.get("due_date", "") or "Not specified")
    score = r.get("rfp_likelihood")
    score_str = f"{score:.2f}" if score is not None and not (isinstance(score, float) and pd.isna(score)) else "N/A"
    text_color = "#999" if muted else "#555"
    val_color = "#bbb" if muted else "#222"

    with st.container(border=True):
        left, right = st.columns([8, 1])
        with left:
            title_md = f"<span style='color:{val_color}'>{title}</span>" if muted else f"**{title}**"
            st.markdown(title_md, unsafe_allow_html=True)
            st.markdown(
                f"<span style='font-size:0.82rem;color:{text_color}'>{ns} &nbsp;|&nbsp; {agency} &nbsp;|&nbsp; {work_type}"
                f" &nbsp;|&nbsp; Due: <b style='color:{val_color};font-size:0.9rem'>{due}</b>"
                f" &nbsp;|&nbsp; Score: <b style='color:{val_color};font-size:0.9rem'>{score_str}</b></span>",
                unsafe_allow_html=True,
            )
        with right:
            if st.button("View", key=f"row_{r['solicitation_id']}", use_container_width=True):
                st.session_state.selected_id = r["solicitation_id"]
                st.rerun()

if show_all:
    # Merged view: gate-passed first (scored), then below-gate (muted, filter-aware)
    _below_show_all = sorted_view[sorted_view["passed_gate"] == 0]
    for _, r in above_gate.iterrows():
        _render_row(r, muted=False)
    if len(_below_show_all) > 0:
        st.divider()
        st.caption(f"⚪ Below relevance gate — {len(_below_show_all)} records (muted)")
        for _, r in _below_show_all.iterrows():
            _render_row(r, muted=True)
else:
    # Default: gate-passed in main list; below-gate in collapsed expander (always full set)
    for _, r in above_gate.iterrows():
        _render_row(r, muted=False)
    if len(_below_all) > 0:
        st.divider()
        with st.expander(f"⚪ Below Relevance Gate — {len(_below_all)} records"):
            st.caption(
                "These records did not match any GMG service type keyword or Georgia geography. "
                "Expired / closed records are excluded — use the filter above to show them."
            )
            for _, r in _below_all.iterrows():
                _render_row(r, muted=True)
