# RFP Signal System

**Georgia Tech OMSA Applied Analytics Practicum — Gude Management Group (GMG)**

A predictive analytics pipeline that finds, scores, and prioritizes transportation engineering RFPs across Georgia and Florida — giving GMG's proposals team an early signal before competitors see the posting.

```
Data Sources → Ingestion → NLP Tagging → Scoring → SQLite DB → Dashboard
```

**Target service lines:** CEI · Traffic Operations · Transportation Planning · Program Management · A&E Design  
**Target geography:** Georgia (primary) · Florida (secondary)

---

## Quick Start

### 1. Install dependencies

```bash
pip install requests playwright python-dateutil streamlit beautifulsoup4
playwright install chromium
```

### 2. API key (already saved)

The SAM.gov API key is stored in `.env` at the project root and loaded automatically. No action needed. To regenerate: register free at [sam.gov/profile/details](https://sam.gov/profile/details) (free tier = 10 req/day), then update `.env`:

```
SAM_GOV_API_KEY=your-key-here
```

### 3. Run the pipeline

```bash
python run_pipeline.py --live
```

### 4. Launch the dashboard

Double-click `start_dashboard.bat` **or** run:

```bash
python -m streamlit run output/dashboard.py
```

Dashboard opens at **http://localhost:8501** — keep the terminal window open while using it.

### 5. Run tests

```bash
python -m pytest tests/
```

All 51 tests are offline-capable (no API key or network required).

---

## Active Data Sources

| Source | Adapter | Records (typical) | Coverage |
|---|---|---|---|
| **Georgia Procurement Registry (GPR)** | `gpr.py` | ~63 | All Georgia counties, cities, school boards — open bids |
| **GDOT Major Projects** | `gdot_major_projects.py` | 46 | Active GDOT construction/design projects (CEI/A&E signals) |
| **MARTA bid portal** | `marta.py` | 7 | Current RFPs/IFBs + anticipated pipeline |
| **Cobb County Transportation** | `cobb_transportation.py` | 7 | Dedicated transportation bids (sidewalk, signal, transit) |
| **Fayette County Purchasing** | `fayette_purchasing.py` | ~15 | County bids filtered for transportation keywords |
| **Gwinnett County Purchasing** | `gwinnett_purchasing.py` | 4 | County bids filtered for transportation keywords |
| **SAM.gov (federal)** | `sam_gov.py` | 4 | Federal transportation opportunities in GA + FL |
| **BidNet Direct** | `bidnet_direct.py` | 3 | Cherokee, Douglas, Fulton, Clayton, Henry counties |
| **ARC transportation news** | `arc_news.py` | 5 | TIP amendments, corridor studies, SPLOST votes (early signals) |
| **Bartow County** | `bartow_county.py` | 1 | County project bids (MPO, Transit dept) |
| **Newton County** | `newton_county.py` | 0* | CivicEngage platform — picks up bids when posted |

*Newton County has no open bids at time of last run; adapter activates when new bids are posted.

**Total: ~134 signals in DB, 83 flagged for GMG review (as of June 2026)**

---

## Auth-Gated Sources (not yet active)

These sources require credentials that GMG would need to provide:

| Source | What's needed | Why it matters |
|---|---|---|
| **GDOT Professional Services portal** | GDOT Engineering Consultant Qualification | GDOT CEI/A&E RFQs for prequalified firms — highest-value opportunities |
| **FDOT Procurement App** | FDOT vendor registration | Florida DOT project solicitations |
| **Henry County (OpenGov)** | Cloudflare-protected — no scraping possible | Use OpenGov email subscription instead |
| **BoardDocs** | IP-restricted from some networks | GA school board SPLOST votes; use BoardDocs email subscriptions |

**Recommended next step:** GMG to initiate GDOT Engineering Consultant Qualification to unlock the prequalified-consultant portal.

---

## Pipeline Architecture

### Layer 1 — Ingestion (`ingestion/`)

Each parser returns a list of record dicts:

```python
{
    "agency": str,          # e.g. "Cobb County"
    "title": str,           # opportunity title
    "solicitation_id": str, # unique ID for deduplication
    "year": int,            # calendar year
    "bucket": str,          # "1 - Active RFP" | "2 - Predicted"
    "status_line": str,     # "Due date: 2026-07-15 | ..."
    "source_url": str,      # direct link to the bid
}
```

All adapters degrade gracefully — a blocked or unavailable source returns `[]` and logs a reason without stopping the pipeline.

**Stale record filtering:** Active RFPs whose due date has passed, or records with no parseable due date and year < current year, are automatically dropped before scoring.

### Layer 2 — NLP Tagging (`nlp/tagging.py`)

Keyword-rule classification for:
- **Service types:** CEI, Planning, Traffic Ops, Program Management, A&E
- **Signal types:** SPLOST/TSPLOST, Bond Issuance, Capital Budget, TIP Amendment, Active RFP, Planning Study
- **Entity extraction:** dates and dollar amounts (spaCy if installed, regex fallback)

### Layer 3 — Scoring (`scoring/engine.py`)

**Step 1 — Relevance Gate (binary PASS/FAIL)**
- Must match at least one service type (CEI / Planning / Traffic Ops / Program Mgmt / A&E)
- Must be in Georgia or Florida geography
- Must exceed $30,000 minimum budget (when detectable)

**Step 2 — RFP Likelihood Score (0.0–1.0)**

| Component | Weight | Description |
|---|---|---|
| Signal count | 35% | More signal types = higher score |
| Recency | 30% | Exponential decay — current-year records score highest |
| Source weight | 20% | Active RFP = 1.0 · Planning Study = 0.6 · News = 0.3 |
| Pipeline stage | 15% | Active RFP = 1.0 · Predicted = 0.5 · Awarded = 0.0 |

Active RFPs with a future due date always score **1.0**.

**Step 3 — Review Flag:** Score ≥ 0.50 → flagged for proposals team review.

### Layer 4 — Storage & Dashboard

- **SQLite:** `data/db/opportunities.sqlite3` — upserts on each run, purges expired rows
- **Dashboard:** `output/dashboard.py` — Streamlit app with per-row Next Step and Work Type

---

## Dashboard Features

| Feature | Description |
|---|---|
| **Next Step** | Per-row action: 🔴 URGENT / 🟠 prepare proposal / 🟡 monitor / ⚫ closed |
| **Work Type** | One-liner nature of work (e.g. "Road Resurfacing", "Bridge Replacement") |
| **Filters** | Filter by Bucket, Agency, or Flagged-for-review only |
| **Score** | RFP likelihood 0.0–1.0; Active RFPs always 1.0 |
| **Urgent count** | Top metric: bids with ≤7 days to due date |
| **Sidebar guide** | Explains each bucket and what action GMG should take |
| **Source URL** | Clickable link directly to the bid posting |

---

## Early Signal Indicators

The system detects upstream signals that typically precede an RFP by 6–24 months:

| Signal | Source | Lead Time |
|---|---|---|
| SPLOST / TSPLOST referendum | BoardDocs, ARC News | 12–24 months |
| TIP Amendment adoption | ARC News RSS | 6–18 months |
| Corridor / planning study launch | ARC News, SAM.gov | 12–24 months |
| GDOT active major project | GDOT Major Projects page | Ongoing — CEI may open anytime |
| Federal grant award | ARC News, SAM.gov | 3–12 months |
| Active RFP posted | SAM.gov, MARTA, GPR, county portals | Immediate |
| Anticipated procurement | MARTA portal | 1–6 months |

---

## Adding a New Source

1. Create `ingestion/parsers/your_source.py` with a `fetch_and_parse() -> List[dict]` function
2. Each record must have: `agency`, `title`, `solicitation_id`, `year`, `bucket`, `status_line`, `source_url`
3. Add to `SOURCES` list in `run_pipeline.py`
4. Add tests in `tests/test_pipeline.py`

The pipeline handles NLP tagging, scoring, deduplication, and stale filtering automatically.

---

## Project Structure

```
rfp_signal_system/
├── run_pipeline.py              # Entry point — runs full pipeline
├── .env                         # API keys (SAM_GOV_API_KEY) — auto-loaded
├── start_dashboard.bat          # Double-click to launch dashboard
├── ingestion/
│   ├── fetcher.py               # HTTP + Playwright fetcher with anti-bot detection
│   └── parsers/
│       ├── sam_gov.py           # SAM.gov federal opportunities (GA + FL)
│       ├── marta.py             # MARTA bid portal (current + anticipated)
│       ├── arc_news.py          # ARC transportation news RSS
│       ├── gpr.py               # Georgia Procurement Registry (all GA local gov)
│       ├── cobb_transportation.py  # Cobb County Transportation bids
│       ├── gwinnett_purchasing.py  # Gwinnett County Purchasing
│       ├── fayette_purchasing.py   # Fayette County Purchasing
│       ├── bidnet_direct.py        # BidNet Direct (Cherokee/Douglas/Fulton/Clayton/Henry)
│       ├── gdot_major_projects.py  # GDOT Major Projects (public, no auth)
│       ├── bartow_county.py        # Bartow County project bids
│       ├── newton_county.py        # Newton County (CivicEngage)
│       ├── gdot_solicitation.py    # GDOT Professional Services (auth-gated)
│       ├── fdot_pda.py             # FDOT Procurement App (auth-gated)
│       ├── boarddocs.py            # BoardDocs agendas (IP-gated)
│       └── henry_opengov.py        # Henry County OpenGov (Cloudflare-gated)
├── nlp/
│   └── tagging.py               # Service type + signal type classifier
├── scoring/
│   └── engine.py                # Relevance gate + RFP likelihood scoring
├── storage/
│   └── db.py                    # SQLite upsert, fetch, purge expired
├── output/
│   └── dashboard.py             # Streamlit dashboard
├── tests/
│   └── test_pipeline.py         # 51 regression tests (all offline-capable)
└── data/
    ├── db/opportunities.sqlite3  # Live opportunity database
    └── raw/                      # Cached fixtures for offline testing
```

---

## Practicum Context

**Course:** ISYE/CSE/MGT 6748 — Applied Analytics Practicum (Georgia Tech OMSA, Summer 2026)  
**Partner:** Gude Management Group (GMG) — transportation engineering firm serving GDOT, FDOT, MARTA, and local government clients  
**Team:** GMG-3  
**Engagement end:** July 16, 2026

**Goal:** Build a predictive system that identifies transportation engineering procurement opportunities early enough for GMG to prepare competitive proposals, with a likelihood score that prioritizes the proposals team's time.
