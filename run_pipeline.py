"""
End-to-end pipeline: Ingestion -> NLP tagging -> Scoring -> Storage.

Currently wired to the real Fulton County Schools data fetched and saved in
data/raw/. To add a new source: write a parser in ingestion/parsers/ that
returns a list of record dicts (agency, title, year, bucket, source_url,
solicitation_id, status_line), then add it to SOURCES below.
"""
from __future__ import annotations

import json
from pathlib import Path

from ingestion.parsers import fulton_schools
from nlp.tagging import tag_records
from scoring.engine import score_all
from storage.db import upsert_opportunities, fetch_all

BASE = Path(__file__).resolve().parent

SOURCES = [
    {
        "name": "fulton_schools",
        "parser": fulton_schools.parse_file,
        "path": BASE / "data" / "raw" / "fulton_schools_solicitations_20260620.md",
    },
    # Henry County (henrycounty_purchasing_20260620.md) is saved in data/raw/ but
    # not yet wired here. Its portal is JS-rendered (OpenGov), so the raw file
    # contains only a shell page with no structured listings. A parser can be added
    # once fetch_dynamic() captures a full render on a machine with open internet.
]


def run() -> list[dict]:
    all_records: list[dict] = []
    for src in SOURCES:
        records = src["parser"](str(src["path"]))
        print(f"[ingestion] {src['name']}: {len(records)} records")
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
    result = run()
    print(json.dumps(result, indent=2))
