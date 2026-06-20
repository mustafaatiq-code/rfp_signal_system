# RFP Signal Detection & Opportunity Scoring System

Prototype for GMG-3's Georgia Tech OMSA Applied Analytics Practicum project
(sponsor: Gude Management Group). Implements the 4-layer architecture from the
midterm deck: Ingestion -> NLP/Parsing -> Scoring -> Output.

## Important note on data access

This prototype was built inside a sandboxed dev environment whose outbound
network is restricted to an allowlist (package registries only — no direct
access to county/state government sites). Two consequences:

1. `ingestion/fetcher.py` contains the **production fetcher** — plain
   `requests`/`BeautifulSoup4` for static pages and a `Playwright` fallback for
   JS-rendered portals (GPR, BidNet, OpenGov, BoardDocs). This is the code that
   should run on a machine with normal internet access (your laptop, a cron
   server, etc.) — it has not been executed end-to-end here because this sandbox
   cannot reach those hosts.
2. To validate the rest of the pipeline with **real** (not fabricated) data,
   I fetched two live public procurement pages through an available tool and
   saved the verbatim results to `data/raw/`:
   - `fulton_schools_solicitations_20260620.md` — real, current Fulton County
     Schools capital-program solicitation listings (one open RFP, several
     awarded/cancelled).
   - `henrycounty_purchasing_20260620.md` — confirms Henry County's bid portal
     is JS-rendered (OpenGov), consistent with the deck's Playwright plan.

   `ingestion/parsers/fulton_schools.py` parses the real saved Fulton page into
   structured records, and the rest of the pipeline (NLP tagging, scoring,
   SQLite storage, Streamlit dashboard) runs on those real records end-to-end.

Bottom line: the pipeline is real and runs end-to-end on real data; only the
live continuous scraping of JS-heavy/login-gated portals needs to happen on a
machine with unrestricted internet access using the fetcher already provided.

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
python run_pipeline.py          # ingest -> parse -> tag -> score -> store
streamlit run output/dashboard.py
```
