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
from pathlib import Path

from ingestion.parsers import henry_opengov, gdot_solicitation, sam_gov, fdot_pda, gpr, marta, boarddocs, arc_news
from nlp.tagging import tag_records
from scoring.engine import score_all
from storage.db import upsert_opportunities, fetch_all

BASE = Path(__file__).resolve().parent

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
        # Georgia Procurement Registry — LOCAL GOVERNMENTS only (counties,
        # cities, school boards). State agencies use GA@WORK. Returns 403
        # (IP-restricted or auth required). Degrades gracefully.
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

    tagged = tag_records(all_records)
    scored = score_all(tagged)

    n_passed = sum(1 for o in scored if o.passed_gate)
    n_flagged = sum(1 for o in scored if o.flagged_for_review)
    print(f"[scoring] {n_passed}/{len(scored)} passed relevance gate, "
          f"{n_flagged} flagged for review (>= 0.50)")

    upsert_opportunities(scored)
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
