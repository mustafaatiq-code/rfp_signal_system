# RFP Signal Detection & Opportunity Scoring System

Prototype for GMG-3's Georgia Tech OMSA Applied Analytics Practicum project
(sponsor: Gude Management Group). Implements the 4-layer architecture from the
midterm deck: Ingestion -> NLP/Parsing -> Scoring -> Output.

## Data access — status

This prototype was first built inside a sandboxed dev environment whose outbound
network was restricted to an allowlist (package registries only — no direct
access to county/state government sites). It has since been validated on a
machine with normal internet access. Current status:

1. **Static sources (e.g. Fulton County Schools) — live and validated.**
   `ingestion/fetcher.py` is the production fetcher (plain `requests` for static
   pages). `ingestion/parsers/fulton_schools.py` now has two equivalent paths:
   `parse()` reads the cached markdown in `data/raw/`, and `parse_html()` parses
   the **live** Finalsite-CMS HTML. On 2026-06-20 the live path was run
   end-to-end against `fultonschools.org` and produced records **identical** to
   the cached file (verified in `tests/test_pipeline.py`). Run it with
   `python run_pipeline.py --live`.
2. **JS-rendered portals — Playwright installed; behaviour now depends on the
   host.** `fetch_dynamic()` (Playwright/headless Chromium) is installed and
   verified working. It hardens two things learned from live testing: it waits
   on `domcontentloaded` + a settle delay (SPAs never reach `networkidle`), and
   it **detects anti-bot interstitials** and returns `fetched_via="blocked"`
   rather than an opaque timeout.
   - **Non-bot-walled SPAs (many OpenGov instances, BoardDocs, GPR):** will
     render and parse normally once a per-source parser is added.
   - **Henry County specifically (`procurement.opengov.com/portal/henryga`):**
     served behind **Cloudflare Turnstile** ("Just a moment… performing security
     verification"). Headless automation is challenged and never reaches the
     listings; the backing API host returns the same wall. We do **not** try to
     defeat the bot check (brittle, against OpenGov's terms). `henry_opengov`
     therefore degrades gracefully — it logs the block and contributes 0 records
     — and ships a **structure-based parser** (unit-tested on a synthetic OpenGov
     fixture) that is ready the moment the data is reachable through a permitted
     path: OpenGov's official API access, the portal's RSS/email bid
     subscription, or an aggregator (BidNet Direct / GPR).

Bottom line: the pipeline runs end-to-end on real data, the production fetch
path is exercised **live** for static sources, and the dynamic path is installed
and correctly reports bot-gated sources. The remaining Henry County gap is an
**access** problem (Cloudflare), not a code gap — resolved by a permitted data
feed rather than more scraping.

### Cached vs. live data files
The two `data/raw/*.md` files are kept as provenance and as an offline fixture
(so the pipeline and its test run with no network). They are byte-faithful to
the live pages as fetched on 2026-06-20.

## Layout

```
ingestion/          fetcher.py (production fetch), parsers/ (per-source parsers)
nlp/                signal/service-type tagging
scoring/            relevance gate + RFP likelihood score
storage/            SQLite persistence
output/             Streamlit dashboard
data/raw/           real fetched source pages (provenance kept)
data/db/            SQLite database file
tests/              pipeline test
```

## Running

```bash
pip install -r requirements.txt
python run_pipeline.py          # cached fixtures: ingest -> parse -> tag -> score -> store
python run_pipeline.py --live   # production path: fetch live over the network, then the same
streamlit run output/dashboard.py
python -m pytest tests/         # asserts the live fetch matches the cached fixture
```
