# RFP Signal System

**Georgia Tech OMSA Applied Analytics Practicum — Gude Management Group (GMG)**

A predictive analytics pipeline that finds, scores, and prioritizes transportation engineering RFPs in Georgia and Florida — giving GMG's proposals team an early signal before competitors see the posting.

---

## What It Does

The system runs a daily pipeline across multiple public data sources, applies NLP-based tagging, scores each opportunity by RFP likelihood, and surfaces the highest-priority items in a live Streamlit dashboard.

```
Data Sources → Ingestion → NLP Tagging → Scoring → SQLite DB → Dashboard
```

**Target service lines:** CEI · Traffic Operations · Transportation Planning · Program Management · A&E Design

**Target geography:** Georgia (primary) · Florida (secondary)

---

## Pipeline Architecture

### Layer 1 — Ingestion (`ingestion/`)

| Source | Adapter | Status | Signal Type |
|---|---|---|---|
| SAM.gov federal opportunities | `sam_gov.py` | Live (API key required) | Active RFP / Pre-solicitation |
| MARTA bid portal | `marta.py` | Live | Active RFP + Anticipated |
| ARC transportation news | `arc_news.py` | Live (RSS) | Planning Study / TIP Amendment |
| BoardDocs (GA school boards) | `boarddocs.py` | Ready (IP-gated) | SPLOST / CIP signals |
| GDOT Professional Services | `gdot_solicitation.py` | Auth-gated | Active RFP |
| FDOT Procurement App | `fdot_pda.py` | Auth-gated | Active RFP |
| Georgia Procurement Registry | `gpr.py` | IP-gated | Active RFP |
| Henry County (OpenGov) | `henry_opengov.py` | Cloudflare-gated | Active RFP |

All adapters degrade gracefully — a blocked source returns `[]` and logs a reason without stopping the pipeline.

### Layer 2 — NLP Tagging (`nlp/tagging.py`)

Keyword-rule classification for:
- **Service types:** CEI, Planning, Traffic Ops, Program Mgmt, A&E
- **Signal types:** SPLOST/TSPLOST, Bond Issuance, Capital Budget, TIP Amendment, Legislation, Planning Study, Political Meetings, Active RFP
- **Entity extraction:** dates and dollar amounts (spaCy if installed, regex fallback)

### Layer 3 — Scoring (`scoring/engine.py`)

**Step 1 — Relevance Gate (binary PASS/FAIL)**
- Must match at least one service type (CEI / Planning / Traffic Ops / Program Mgmt / A&E)
- Must be in Georgia or Florida geography
- Must be above $30,000 minimum budget (when detectable)

**Step 2 — RFP Likelihood Score (0.0–1.0)**
```
score = 0.35 × signal_count_norm
      + 0.30 × recency_score
      + 0.20 × source_weight
      + 0.15 × pipeline_stage_score
```
Active RFPs with a future due date always score 1.0.

**Step 3 — Review Flag**
Score ≥ 0.50 → flagged for the proposals team's review queue.

### Layer 4 — Storage & Dashboard

- **SQLite:** `data/db/opportunities.sqlite3`
- **Dashboard:** Streamlit (`output/dashboard.py`) — sortable table of flagged opportunities

---

## Quick Start

### Requirements

```bash
pip install requests playwright python-dateutil streamlit
playwright install chromium
```

Optional (improves entity extraction):
```bash
pip install spacy && python -m spacy download en_core_web_sm
```

### SAM.gov API Key

Register free at [sam.gov/profile/details](https://sam.gov/profile/details) — free tier = 10 requests/day (enough for daily runs).

```bash
export SAM_GOV_API_KEY=your-key-here
```

### Run the Pipeline

```bash
# Live fetch from all sources
python run_pipeline.py --live

# Run tests (all offline-capable, no API key needed)
python -m pytest tests/

# Launch dashboard
python -m streamlit run output/dashboard.py
```

Dashboard opens at **http://localhost:8501**

---

## Source Detail

### SAM.gov
Queries federal transportation opportunities in GA and FL using two keyword searches × two states = 4 API calls per run (within the 10/day free limit). Both `naicsCode` and `placeOfPerformanceState` query params are silently ignored by the SAM.gov v2 API — all filtering is done post-response in Python.

**Target NAICS codes:** 541330, 541380, 541614, 541618, 541690, 237310, 237130

### MARTA
Scrapes the public MARTA bid portal (no login required) via Playwright:
- `CurrentOpportunities.aspx` — active RFPs and IFBs
- `AnticipatedProcurement.aspx` — future procurement pipeline (early signals)

### ARC News
Parses the Atlanta Regional Commission's Transportation & Mobility RSS feed. Filters for high-signal articles: TIP amendments, corridor studies, SPLOST votes, federal grant awards. These indicate projects moving toward procurement 6–24 months out.

### BoardDocs
Targets GA school board and county commission meeting agendas where SPLOST/E-SPLOST/T-SPLOST votes and CIP approvals are recorded. Returns `[]` when accessed from an IP-restricted environment; designed to run from a permitted machine or via BoardDocs email subscriptions.

---

## Project Structure

```
rfp_signal_system/
├── run_pipeline.py              # End-to-end pipeline entry point
├── ingestion/
│   ├── fetcher.py               # fetch_static / fetch_dynamic (Playwright) + anti-bot detection
│   └── parsers/
│       ├── sam_gov.py           # SAM.gov federal opportunities
│       ├── marta.py             # MARTA bid portal (current + anticipated)
│       ├── arc_news.py          # ARC transportation news RSS feed
│       ├── boarddocs.py         # BoardDocs GA school board/commission agendas
│       ├── gdot_solicitation.py # GDOT Professional Services (auth-gated)
│       ├── fdot_pda.py          # FDOT Procurement App (auth-gated)
│       ├── gpr.py               # Georgia Procurement Registry (IP-gated)
│       └── henry_opengov.py     # Henry County OpenGov (Cloudflare-gated)
├── nlp/
│   └── tagging.py               # Service type + signal type keyword classifier
├── scoring/
│   └── engine.py                # Relevance gate + RFP likelihood score
├── storage/
│   └── db.py                    # SQLite upsert and fetch
├── output/
│   └── dashboard.py             # Streamlit dashboard
├── tests/
│   └── test_pipeline.py         # 23 regression tests (all offline-capable)
├── data/
│   ├── db/opportunities.sqlite3 # Live opportunity database
│   └── raw/                     # Cached page fixtures for offline testing
└── scripts/
    └── cleanup_stale_db.py      # Utility: remove stale records from dev runs
```

---

## RFP Likelihood Score Weights

| Component | Weight | Description |
|---|---|---|
| Signal count | 35% | More signal types (SPLOST + bond + planning study) = higher score |
| Recency | 30% | Exponential decay by age — current-year records score highest |
| Source weight | 20% | SPLOST / Active RFP = 1.0 · Planning Study = 0.6 · News = 0.3 |
| Pipeline stage | 15% | Active RFP = 1.0 · Predicted = 0.5 · Awarded/Cancelled = 0.0 |

---

## Early Signal Indicators

The system detects upstream signals that typically precede an RFP by 6–24 months:

| Signal | Source | Lead Time |
|---|---|---|
| SPLOST / TSPLOST referendum | BoardDocs, ARC news | 12–24 months |
| TIP Amendment adoption | ARC news RSS | 6–18 months |
| Corridor / planning study launch | ARC news RSS, SAM.gov | 12–24 months |
| Bond issuance (county/city) | BoardDocs, local news | 12–18 months |
| Federal grant award | ARC news, SAM.gov | 3–12 months |
| Active RFP posted | SAM.gov, MARTA, GPR | Immediate |
| Anticipated procurement listed | MARTA portal | 1–6 months |

---

## Practicum Context

**Course:** ISYE/CSE/MGT 6748 — Applied Analytics Practicum (Georgia Tech OMSA, Summer 2026)

**Partner:** Gude Management Group (GMG) — transportation engineering firm serving GA and FL DOT, MARTA, and local government clients

**Goal:** Build a predictive system that identifies transportation engineering procurement opportunities early enough for GMG to prepare competitive proposals, with a likelihood score that prioritizes the proposals team's time.
