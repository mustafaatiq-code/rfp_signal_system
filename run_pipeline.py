"""
End-to-end pipeline: Ingestion -> NLP tagging -> Scoring -> Storage.

Currently wired to the real Fulton County Schools data fetched and saved in
data/raw/. To add a new source: write a parser in ingestion/parsers/ that
returns a list of record dicts (agency, title, year, bucket, source_url,
solicitation_id, status_line), then add it to SOURCES below.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

# Load .env from project root so SAM_GOV_API_KEY etc. survive session restarts
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from ingestion.parsers import henry_opengov, gdot_solicitation, gdot_major_projects, sam_gov, fdot_pda, gpr, marta, boarddocs, arc_news, cobb_transportation, gwinnett_purchasing, fayette_purchasing, bidnet_direct, bartow_county, newton_county
from nlp.tagging import tag_records
from scoring.engine import score_all
from storage.db import upsert_opportunities, purge_expired, fetch_all

BASE = Path(__file__).resolve().parent

# Match "Due date: YYYY-MM-DD" (county parsers) or "Month D, YYYY" (MARTA/Fulton)
_ISO_DUE_RE = re.compile(r"due date[:\s]+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_MONTH_DUE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)


def _drop_expired(records: list[dict], today: date | None = None) -> list[dict]:
    """Remove Active RFP records that are stale.

    Stale means either:
    - parseable due date that has already passed, OR
    - no parseable due date but year < current year (almost certainly closed)
    """
    today = today or date.today()
    current_year = today.year
    kept = []
    dropped = 0
    for rec in records:
        if rec.get("bucket") != "1 - Active RFP":
            kept.append(rec)
            continue
        status = str(rec.get("status_line", ""))
        due: date | None = None
        m = _ISO_DUE_RE.search(status)
        if m:
            try:
                due = date.fromisoformat(m.group(1))
            except ValueError:
                pass
        if due is None:
            m2 = _MONTH_DUE_RE.search(status)
            if m2:
                try:
                    from dateutil import parser as dp
                    due = dp.parse(m2.group(0)).date()
                except Exception:
                    pass
        if due is not None and due < today:
            dropped += 1
        elif due is None and rec.get("year", current_year) < current_year:
            dropped += 1
        else:
            kept.append(rec)
    if dropped:
        print(f"[filter] dropped {dropped} stale records (expired or year < {current_year})")
    return kept

SOURCES = [
    {
        "name": "henry_opengov",
        # Live only: Henry's bids live on OpenGov (JS + Cloudflare Turnstile).
        # fetch_and_parse() degrades gracefully and returns [] when bot-gated,
        # so this source is a no-op until reached via a permitted path (see the
        # module docstring / README). There is no cached listing fixture — the
        # saved henry md is only a shell page note.
        "live_parser": henry_opengov.fetch_and_parse,
    },
    {
        "name": "gdot_solicitation",
        # GDOT's Professional Services portal — requires Microsoft Identity
        # (prequalified-consultant) login. fetch_and_parse() returns [] with a
        # log explaining the auth path. Federal-aid GDOT projects flow in via
        # the sam_gov source below.
        "live_parser": gdot_solicitation.fetch_and_parse,
    },
    {
        "name": "gdot_major_projects",
        # GDOT Major Projects page — publicly accessible, no auth required.
        # Lists active major GDOT construction/design projects (CEI, A&E signals).
        # Each record links to its ArcGIS Hub project page for full details.
        "live_parser": gdot_major_projects.fetch_and_parse,
    },
    {
        "name": "sam_gov",
        # SAM.gov federal transportation opportunities (GA + FL).
        # Requires SAM_GOV_API_KEY env var (free, 10 req/day).
        # Uses 2 keywords x 2 states = 4 requests per run.
        "live_parser": sam_gov.fetch_and_parse,
    },
    {
        "name": "fdot_pda",
        # FDOT Procurement Development Application — requires FDOT vendor
        # authentication (redirects to /Error/Forbidden without session).
        # Degrades gracefully. Federal-aid FDOT projects flow via sam_gov.
        "live_parser": fdot_pda.fetch_and_parse,
    },
    {
        "name": "gpr",
        # Georgia Procurement Registry — public bid portal for all Georgia local
        # governments (counties, cities, school boards). Requires Chrome User-Agent
        # to bypass browser-detection middleware; no login needed. Fetches
        # Construction/PublicWorks + DesignProfessional categories and filters
        # locally by transportation keywords. Typically 60+ transport bids.
        "live_parser": gpr.fetch_and_parse,
    },
    {
        "name": "marta",
        # MARTA public bid portal — current RFPs/IFBs + anticipated pipeline.
        # No authentication required. CEI, A&E, Planning, Traffic Ops relevant.
        "live_parser": marta.fetch_and_parse,
    },
    {
        "name": "arc_news",
        # Atlanta Regional Commission (MPO) — transportation news RSS feed.
        # TIP amendments, corridor studies, SPLOST votes, grant awards.
        # No authentication required. Coverage: 10-county metro Atlanta.
        "live_parser": lambda: arc_news.fetch_and_parse(days_back=365),
    },
    {
        "name": "boarddocs",
        # BoardDocs — GA school board / county commission meeting agendas.
        # SPLOST/E-SPLOST/T-SPLOST votes, CIP approvals, bond resolutions.
        # Returns [] when IP-blocked (degrades gracefully like GPR/GDOT).
        "live_parser": boarddocs.fetch_and_parse,
    },
    {
        "name": "cobb_transportation",
        # Cobb County Dept of Transportation — dedicated transportation bids
        # page. All records are transportation-specific (no filtering needed).
        # Sidewalk/pedestrian projects, intersection improvements, transit
        # center design, SS4A corridor safety, A&E prequalification lists.
        "live_parser": cobb_transportation.fetch_and_parse,
    },
    {
        "name": "gwinnett_purchasing",
        # Gwinnett County Purchasing — general county portal, filtered for
        # transportation titles (road/pedestrian/traffic/signal projects).
        # Publicly accessible static HTML, no authentication required.
        "live_parser": gwinnett_purchasing.fetch_and_parse,
    },
    {
        "name": "fayette_purchasing",
        # Fayette County Purchasing — static HTML table, publicly accessible.
        # Filtered for transportation titles. Has active traffic signal bids.
        "live_parser": fayette_purchasing.fetch_and_parse,
    },
    {
        "name": "bidnet_direct",
        # BidNet Direct Georgia Purchasing Group — JS-rendered SPA (Playwright).
        # Covers 4 ARC counties: Fulton, Cherokee, Clayton, Douglas.
        # Returns [] per county if Playwright unavailable or bot-gated.
        "live_parser": bidnet_direct.fetch_and_parse,
    },
    {
        "name": "bartow_county",
        # Bartow County Projects for Bid — static HTML listing PDF-linked bids.
        # No due dates in HTML (inside PDF); records bucketed as "2 - Predicted".
        # Stale filter auto-drops 2024-numbered bids; new 2026 bids picked up
        # when county posts them. Bartow has active Transit dept and MPO.
        "live_parser": bartow_county.fetch_and_parse,
    },
    {
        "name": "newton_county",
        # Newton County Bid Postings — CivicEngage platform.
        # Returns empty list when no bids are posted; pick up future bids.
        "live_parser": newton_county.fetch_and_parse,
    },
]


def run(live: bool = False) -> list[dict]:
    all_records: list[dict] = []
    for src in SOURCES:
        name = src["name"]
        try:
            if live and "live_parser" in src:
                records = src["live_parser"]()
                mode = "live"
            elif "parser" in src and "path" in src:
                records = src["parser"](str(src["path"]))
                mode = "cache"
            else:
                avail = "live" if live else "cached"
                print(f"[ingestion] {name}: skipped (no {avail} source available)")
                continue
        except Exception as exc:  # one bad source must not kill the whole run
            print(f"[ingestion] {name}: ERROR {exc!r} — skipped")
            continue
        print(f"[ingestion] {name} ({mode}): {len(records)} records")
        all_records.extend(records)

    all_records = _drop_expired(all_records)
    tagged = tag_records(all_records)
    scored = score_all(tagged)

    n_passed = sum(1 for o in scored if o.passed_gate)
    n_flagged = sum(1 for o in scored if o.flagged_for_review)
    print(f"[scoring] {n_passed}/{len(scored)} passed relevance gate, "
          f"{n_flagged} flagged for review (>= 0.50)")

    upsert_opportunities(scored)
    removed = purge_expired()
    if removed:
        print(f"[storage] purged {removed} expired rows from DB")
    rows = fetch_all()
    print(f"[storage] {len(rows)} rows in opportunities.sqlite3")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RFP signal pipeline")
    ap.add_argument("--live", action="store_true",
                    help="fetch sources live over the network (production path); "
                         "default reads the cached pages in data/raw/")
    args = ap.parse_args()
    result = run(live=args.live)
    print(json.dumps(result, indent=2))
