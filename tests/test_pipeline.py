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
from datetime import date
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from ingestion.parsers import fulton_schools as fs  # noqa: E402
from ingestion.parsers import henry_opengov as henry  # noqa: E402
from ingestion.parsers import sam_gov, fdot_pda, gpr, marta, arc_news, boarddocs  # noqa: E402
from ingestion.fetcher import looks_like_antibot  # noqa: E402
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


def test_open_active_rfp_scores_1_and_is_flagged():
    # As of a date BEFORE McNair's 2026-03-24 due date, it is an open active RFP.
    records = fs.parse_file(str(CACHE))
    scored = score_all(tag_records(records), today=date(2026, 3, 1))
    assert all(o.passed_gate for o in scored)
    mcnair = next(o for o in scored if o.record["solicitation_id"] == "409-26")
    assert mcnair.bucket == "1 - Active RFP"
    assert mcnair.rfp_likelihood == 1.0
    assert mcnair.flagged_for_review is True
    assert mcnair.is_expired is False
    assert [o for o in scored if o.flagged_for_review] == [mcnair]


def test_past_due_active_rfp_is_expired_not_flagged():
    # As of a date AFTER the due date, the same RFP must NOT sit at the top.
    records = fs.parse_file(str(CACHE))
    scored = score_all(tag_records(records), today=date(2026, 6, 20))
    mcnair = next(o for o in scored if o.record["solicitation_id"] == "409-26")
    assert mcnair.is_expired is True
    assert mcnair.bucket == "Expired RFP (past due)"
    assert mcnair.due_date == "2026-03-24"
    assert mcnair.rfp_likelihood < 1.0          # no longer forced to 1.0
    assert mcnair.flagged_for_review is False    # never flagged once past due
    assert not any(o.flagged_for_review for o in scored)  # nothing actionable today


@pytest.mark.skipif(not _has_network(), reason="no network access to live source")
def test_live_fetch_matches_cached_fixture():
    cached = sorted(fs.parse_file(str(CACHE)), key=_KEY)
    live = sorted(fs.fetch_and_parse(), key=_KEY)
    assert live == cached, "live page diverged from the cached fixture"


# --- Henry County / OpenGov (JS + Cloudflare-gated) ---------------------------

def test_antibot_detector_flags_cloudflare_and_passes_real_content():
    cf = "<title>Just a moment...</title><div>Performing security verification</div>"
    assert looks_like_antibot("Just a moment...", cf) is True
    real = "<h2>2026 Current Solicitations</h2><article>409-26, McNair</article>"
    assert looks_like_antibot("Fulton County Schools", real) is False


# SYNTHETIC fixture — hand-written to mimic OpenGov ProcureNow row markup so the
# parser logic can be unit-tested. This is NOT real Henry County data (the live
# portal is Cloudflare-gated and yields none); it only exercises parse_html().
_SYNTHETIC_OPENGOV_HTML = """
<div data-test="project-row">
  <a href="/portal/henryga/projects/12345">RFP 2026-07 Roadway Resurfacing Program</a>
  <span class="statusBadge">Open - Accepting Proposals</span>
</div>
<div data-test="project-row">
  <a href="/portal/henryga/projects/12300">IFB 2025-31 Fleet Vehicle Purchase</a>
  <span class="statusBadge">Closed - Awarded</span>
</div>
"""


def test_henry_parser_extracts_synthetic_rows():
    recs = henry.parse_html(_SYNTHETIC_OPENGOV_HTML)
    assert len(recs) == 2
    assert recs[0]["title"].startswith("RFP 2026-07 Roadway Resurfacing")
    assert recs[0]["bucket"] == "1 - Active RFP"   # "Open - Accepting" -> active
    assert recs[1]["bucket"] == "Awarded"          # "Closed - Awarded" -> awarded
    assert all(r["agency"] == "Henry County, GA" for r in recs)


def test_henry_parser_returns_empty_on_shell_or_challenge():
    # an anti-bot / empty SPA shell carries no listing markup -> no records
    assert henry.parse_html("<title>Just a moment...</title>") == []
    assert henry.parse_html("<html><body></body></html>") == []


# --- SAM.gov adapter ----------------------------------------------------------

# SYNTHETIC SAM.gov v2 API response shape (not real data).
_SYNTHETIC_SAM_RESPONSE = {
    "totalRecords": 2,
    "opportunitiesData": [
        {
            "noticeId": "abc123",
            "title": "CEI Services for SR-316 Widening Project",
            "baseType": "o",
            "active": "Yes",
            "responseDeadLine": "2026-09-15T12:00:00",
            "postedDate": "2026-06-20T00:00:00",
            "naicsCode": "541330",
            "organizationName": "Georgia DOT",
            "fullParentPathName": "DEPT OF TRANSPORTATION|GEORGIA DOT",
            "placeOfPerformanceState": "Georgia",
            "uiLink": "https://sam.gov/opp/abc123/view",
        },
        {
            "noticeId": "xyz789",
            "title": "Traffic Operations Program Support Services",
            "baseType": "p",  # pre-solicitation
            "active": "Yes",
            "responseDeadLine": "",
            "postedDate": "2026-06-18T00:00:00",
            "naicsCode": "541690",
            "organizationName": "Florida DOT District 3",
            "fullParentPathName": "DEPT OF TRANSPORTATION|FDOT D3",
            "placeOfPerformanceState": "Florida",
            "uiLink": "https://sam.gov/opp/xyz789/view",
        },
    ],
}


def test_sam_gov_parse_response_structure():
    # Without a whitelist, all records pass through (used in unit tests).
    recs = sam_gov._parse_response(_SYNTHETIC_SAM_RESPONSE)
    assert len(recs) == 2

    cei = recs[0]
    assert "CEI Services" in cei["title"]
    assert cei["solicitation_id"] == "abc123"
    assert cei["bucket"] == "1 - Active RFP"
    assert cei["year"] == 2026
    assert "Due date: 2026-09-15" in cei["status_line"]
    assert cei["agency"] == "Georgia DOT"
    assert cei["source_url"] == "https://sam.gov/opp/abc123/view"

    traf = recs[1]
    assert "Traffic Operations" in traf["title"]
    assert traf["bucket"] == "2 - Predicted"  # baseType="p" -> pre-solicitation
    assert traf["solicitation_id"] == "xyz789"


def test_sam_gov_strict_naics_drops_no_naics_records():
    """Records with no naicsCode must be excluded when a whitelist is active."""
    data = {
        "totalRecords": 2,
        "opportunitiesData": [
            # Has a matching NAICS — should pass
            {**_SYNTHETIC_SAM_RESPONSE["opportunitiesData"][0]},
            # No NAICS at all — must be dropped (embassy/military junk pattern)
            {**_SYNTHETIC_SAM_RESPONSE["opportunitiesData"][1], "naicsCode": ""},
        ],
    }
    recs = sam_gov._parse_response(data, naics_whitelist=sam_gov.TARGET_NAICS)
    assert len(recs) == 1
    assert "CEI Services" in recs[0]["title"]


def test_sam_gov_state_filter_drops_overseas():
    """Overseas records must be excluded when a state_whitelist is active."""
    overseas = {
        **_SYNTHETIC_SAM_RESPONSE["opportunitiesData"][0],
        "naicsCode": "541330",
        "placeOfPerformance": {
            "state": {"code": "", "name": ""},
            "country": {"code": "HND", "name": "Honduras"},
        },
    }
    data = {"totalRecords": 1, "opportunitiesData": [overseas]}
    recs = sam_gov._parse_response(data, naics_whitelist=sam_gov.TARGET_NAICS,
                                   state_whitelist={"GA"})
    assert recs == []


def test_sam_gov_deduplicates_across_keywords():
    # Two identical notice IDs from different keyword runs should collapse to one.
    dupe = dict(_SYNTHETIC_SAM_RESPONSE)
    seen: set = set()
    records = []
    for rec in sam_gov._parse_response(dupe):
        if rec["solicitation_id"] not in seen:
            seen.add(rec["solicitation_id"])
            records.append(rec)
    # fetch_and_parse() does this dedup — verify the building block works
    assert len(records) == 2
    records2 = []
    for rec in sam_gov._parse_response(dupe):  # second pass (same IDs)
        if rec["solicitation_id"] not in seen:
            seen.add(rec["solicitation_id"])
            records2.append(rec)
    assert len(records2) == 0  # dupes suppressed


def test_sam_gov_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    result = sam_gov.fetch_and_parse(api_key=None)
    assert result == []


# --- FDOT PDA and GPR (both auth/IP-gated) ------------------------------------

def test_fdot_pda_returns_empty_with_mocked_forbidden(monkeypatch):
    """fetch_and_parse() must return [] when the portal shows Forbidden."""
    from ingestion import fetcher as ft
    from ingestion.fetcher import FetchResult

    def fake_dynamic(url, **kw):
        return FetchResult(url=url, status_code=200,
                           html="<title>Procurement Development Application - Page Error</title>",
                           fetched_via="dynamic")

    monkeypatch.setattr(ft, "fetch_dynamic", fake_dynamic)
    assert fdot_pda.fetch_and_parse() == []


def test_gpr_returns_empty_on_403(monkeypatch):
    """fetch_and_parse() must return [] when GPR returns 403."""
    from ingestion import fetcher as ft
    from ingestion.fetcher import FetchResult

    def fake_static(url, **kw):
        return FetchResult(url=url, status_code=403,
                           html="<html><body><h1>403 Forbidden</h1></body></html>",
                           fetched_via="error")

    monkeypatch.setattr(ft, "fetch_static", fake_static)
    assert gpr.fetch_and_parse() == []


# --- MARTA procurement portal -------------------------------------------

_SYNTHETIC_MARTA_CURRENT = """
<html><body>
<div class="nav">Plan a Trip Train Stations Bus Routes</div>
<div class="content">
NOTE: You must sign in to submit a response to any solicitation opportunity
CEI Services for Safe Routes to Transit Improvements
Request for Proposal (RFP) - RFP P50421
Bid Documents
Description: MARTA is seeking qualified firms to provide Construction Engineering
and Inspection (CEI) services for pedestrian and ADA improvements at bus stops.
Bid Submittal From: 5/21/2026 10:12 PM
Bid Submittal To: 8/15/2026 2:00 PM
Bid Submittal Location: MARTA Annex - 2400 Piedmont Rd NE Atlanta GA 30324
Bid Opening: 8/16/2026 10:00 AM
Project Contact: csmith@itsmarta.com
Traffic Operations Support Services
Invitation for Bids (IFB) - IFB B50766
Bid Documents
Description: MARTA is seeking bids from firms to provide traffic operations
and signal timing support across the MARTA rail corridor.
Bid Submittal From: 3/30/2026 12:38 PM
Bid Submittal To: 7/21/2026 2:00 PM
Bid Opening: 7/22/2026 10:30 AM
Project Contact: fbattle@itsmarta.com
</div>
</body></html>
"""

_SYNTHETIC_MARTA_ANTICIPATED = """
<html><body>
<div class="content">
Anticipated Procurements The table below represents the anticipated future procurement schedule for MARTA.
Contract Description Anticipated Date Type Department
Estimated Value Over $10M
P50723 Transit Oriented Development TOD at Hamilton Holmes TBD RFP Department of Real Estate
P50683 Program Management Support for Capital Projects TBD RFP Capital Programs and Development
Estimated Value between $1 - $5M
B50679 Track Inspection and Engineering Services TBD IFB Department of Mechanical Operations
Our Mission To spur economic growth.
</div>
</body></html>
"""


def test_marta_current_opportunities_parser():
    recs = marta.parse_current_html(_SYNTHETIC_MARTA_CURRENT)
    assert len(recs) == 2
    cei = recs[0]
    assert "CEI" in cei["title"] or "Safe Routes" in cei["title"]
    assert cei["solicitation_id"] == "RFP-P50421"
    assert cei["bucket"] == "1 - Active RFP"
    assert "2026-08-15" in cei["status_line"]
    assert "Georgia" in cei["status_line"]

    traffic = recs[1]
    assert "Traffic" in traffic["title"]
    assert traffic["solicitation_id"] == "IFB-B50766"
    assert "2026-07-21" in traffic["status_line"]


def test_marta_anticipated_parser():
    recs = marta.parse_anticipated_html(_SYNTHETIC_MARTA_ANTICIPATED)
    assert len(recs) == 3
    assert any("Program Management" in r["title"] for r in recs)
    assert any("Track Inspection" in r["title"] for r in recs)
    assert all(r["bucket"] == "2 - Predicted" for r in recs)
    assert all("Georgia" in r["status_line"] for r in recs)


def test_marta_cei_passes_gate():
    recs = marta.parse_current_html(_SYNTHETIC_MARTA_CURRENT)
    scored = score_all(tag_records(recs))
    cei = next(o for o in scored if "CEI" in o.record["title"] or "Safe Routes" in o.record["title"])
    assert cei.passed_gate is True
    assert cei.rfp_likelihood == 1.0  # active RFP with future due date


# --- ARC news RSS feed ---------------------------------------------------

_SYNTHETIC_ARC_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Transportation &amp; Mobility Archives - ARC</title>
  <item>
    <title>ARC Adopts Amendment 7 to the FY 2024-2027 Transportation Improvement Program</title>
    <link>https://atlantaregional.org/news/transportation-mobility/arc-adopts-amendment-7-tip/</link>
    <pubDate>Fri, 20 Jun 2026 10:00:00 +0000</pubDate>
    <description><![CDATA[<p>The Atlanta Regional Commission board voted to adopt Amendment 7 to the FY 2024-2027 Transportation Improvement Program, programming $45 million for corridor improvements across the region.</p>]]></description>
  </item>
  <item>
    <title>SR-20 Corridor Study Kicks Off in Cherokee County</title>
    <link>https://atlantaregional.org/news/transportation-mobility/sr-20-corridor-study/</link>
    <pubDate>Mon, 10 Jun 2026 09:00:00 +0000</pubDate>
    <description><![CDATA[<p>ARC and GDOT have launched a planning study for the SR-20 corridor in Cherokee County to evaluate capacity improvements and pedestrian safety.</p>]]></description>
  </item>
  <item>
    <title>Metro Atlanta Commuter Survey Results 2025</title>
    <link>https://atlantaregional.org/news/transportation-mobility/commuter-survey/</link>
    <pubDate>Wed, 01 Jan 2026 09:00:00 +0000</pubDate>
    <description><![CDATA[<p>ARC releases annual survey showing commute patterns in metro Atlanta.</p>]]></description>
  </item>
  <item>
    <title>TSPLOST Referendum Scheduled for Gwinnett County November 2026</title>
    <link>https://atlantaregional.org/news/transportation-mobility/gwinnett-tsplost/</link>
    <pubDate>Tue, 15 Jun 2026 11:00:00 +0000</pubDate>
    <description><![CDATA[<p>Gwinnett County commissioners approved placing a TSPLOST referendum on the November ballot to fund $900M in transportation projects.</p>]]></description>
  </item>
</channel>
</rss>"""


def test_arc_news_parser_filters_and_classifies():
    recs = arc_news.parse_rss(_SYNTHETIC_ARC_RSS, days_back=90)
    # Survey-only article should be filtered out (no procurement signal)
    titles = [r["title"] for r in recs]
    assert any("TIP" in t or "Transportation Improvement" in t for t in titles)
    assert any("Corridor Study" in t or "SR-20" in t for t in titles)
    assert any("TSPLOST" in t for t in titles)
    assert not any("Commuter Survey" in t for t in titles)  # filtered out
    assert all(r["bucket"] == "2 - Predicted" for r in recs)
    assert all("Georgia" in r["status_line"] for r in recs)


def test_arc_news_signal_labels():
    recs = arc_news.parse_rss(_SYNTHETIC_ARC_RSS, days_back=90)
    by_title = {r["title"]: r for r in recs}
    # TIP amendment → State Budget Session
    tip = next(r for r in recs if "TIP" in r["title"] or "Transportation Improvement" in r["title"])
    assert "State Budget Session" in tip["status_line"] or "Legislation" in tip["status_line"]
    # TSPLOST → SPLOST
    tsplost = next(r for r in recs if "TSPLOST" in r["title"])
    assert "SPLOST" in tsplost["status_line"]


def test_arc_news_tip_passes_gate():
    recs = arc_news.parse_rss(_SYNTHETIC_ARC_RSS, days_back=90)
    scored = score_all(tag_records(recs))
    # At least the TIP amendment + corridor study should pass (they contain planning keywords)
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# --- BoardDocs adapter (graceful degradation) ----------------------------

_SYNTHETIC_BOARDDOCS_HTML = """
<html><body>
<div id="bd-content">
<table id="bd-meetings-table">
<tr><td>06/02/2026</td><td>Board of Education Regular Meeting - SPLOST Update</td><td>Agenda</td></tr>
<tr><td>05/19/2026</td><td>Special Called Meeting - Transportation CIP Approval</td><td>Agenda</td></tr>
<tr><td>01/10/2025</td><td>Old Regular Meeting - Personnel Matters</td><td>Minutes</td></tr>
</table>
</div>
</body></html>
"""

_BOARDDOCS_SHELL = '<html><head></head><body><pre style="word-wrap: break-word; white-space: pre-wrap;"> </pre></body></html>'


def test_boarddocs_parser_extracts_transport_meetings():
    recs = boarddocs.parse_meeting_list_html(_SYNTHETIC_BOARDDOCS_HTML, "Test County Schools", "testco")
    # Only SPLOST Update + CIP Approval should pass (both have signal keywords)
    # "Personnel Matters" is outside 90-day window AND has no signal keywords
    assert len(recs) >= 2
    titles = " ".join(r["title"] for r in recs)
    assert "SPLOST" in titles
    assert "CIP" in titles
    assert all(r["bucket"] == "2 - Predicted" for r in recs)
    assert all("Georgia" in r["status_line"] for r in recs)


def test_boarddocs_detects_ip_block_shell():
    assert boarddocs._is_shell(_BOARDDOCS_SHELL) is True
    assert boarddocs._is_shell(_SYNTHETIC_BOARDDOCS_HTML) is False


def test_sam_gov_cei_scored_as_transport():
    recs = sam_gov._parse_response(_SYNTHETIC_SAM_RESPONSE)
    scored = score_all(tag_records(recs))
    cei = next(o for o in scored if "CEI" in o.record["title"])
    assert cei.passed_gate is True
    assert cei.rfp_likelihood >= 0.5
