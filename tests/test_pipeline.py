"""
Pipeline regression tests.

Two guarantees, both tied to "managing the sandbox caveat":

1. Offline guarantee — the full pipeline (parse -> tag -> score) runs on the
   cached Fulton fixture with no network and produces the expected scored
   result. This is what makes the prototype reproducible anywhere.

2. Live-equivalence guarantee — the production fetch+parse path
   (fetch_and_parse) returns records identical to the cached fixture. This is
   what proves the cached "fetched-via-tool" data is faithful to the live page
   and that the production fetcher actually works end-to-end. It is skipped
   automatically when the host has no internet access, so CI without network
   still passes.

Run: python -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from ingestion.parsers import fulton_schools as fs  # noqa: E402
from nlp.tagging import tag_records  # noqa: E402
from scoring.engine import score_all  # noqa: E402

CACHE = BASE / "data" / "raw" / "fulton_schools_solicitations_20260620.md"
_KEY = lambda r: (r["year"], r["solicitation_id"])


def _has_network() -> bool:
    import requests
    try:
        requests.head(fs.SOURCE_URL, timeout=8)
        return True
    except Exception:
        return False


def test_cached_parse_yields_expected_records():
    records = fs.parse_file(str(CACHE))
    assert len(records) == 5
    by_id = {r["solicitation_id"]: r for r in records}
    # the one open RFP
    assert by_id["409-26"]["title"].startswith("McNair Middle School")
    assert by_id["409-26"]["bucket"] == "1 - Active RFP"
    # statuses classified correctly
    assert by_id["403-25"]["bucket"] == "Cancelled"
    assert by_id["416-25"]["bucket"] == "Awarded"


def test_pipeline_scoring_on_cached_records():
    records = fs.parse_file(str(CACHE))
    scored = score_all(tag_records(records))
    assert all(o.passed_gate for o in scored)          # all 5 in-geography A&E/renovation work
    flagged = [o for o in scored if o.flagged_for_review]
    assert len(flagged) == 1                            # only the active RFP clears 0.50
    active = next(o for o in scored if o.record["solicitation_id"] == "409-26")
    assert active.rfp_likelihood == 1.0                # Active RFP forced to 1.0 per deck


@pytest.mark.skipif(not _has_network(), reason="no network access to live source")
def test_live_fetch_matches_cached_fixture():
    cached = sorted(fs.parse_file(str(CACHE)), key=_KEY)
    live = sorted(fs.fetch_and_parse(), key=_KEY)
    assert live == cached, "live page diverged from the cached fixture"
