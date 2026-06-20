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
2. **JS-rendered portals (Henry County / OpenGov, GPR, BidNet, BoardDocs) —
   fetcher ready, one install step remaining.** These render listings
   client-side, so `fetch_static()` returns only a shell. `fetch_dynamic()`
   (Playwright/headless Chromium) handles them but needs a browser install on
   the host: `pip install playwright && playwright install chromium`. Until then
   the code degrades gracefully (`fetch_dynamic` returns a clean error rather
   than crashing). `henrycounty_purchasing_20260620.md` in `data/raw/` documents
   that this portal is JS-rendered, consistent with the deck's Playwright plan.

Bottom line: the pipeline runs end-to-end on real data **and** the production
fetch path is now exercised live for static sources. The only remaining gap is
installing Playwright's browser on the production host to enable the JS-portal
sources.

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
