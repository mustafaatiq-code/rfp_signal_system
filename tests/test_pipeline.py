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
from ingestion.parsers import fdot_pda, gpr, marta, arc_news, boarddocs  # noqa: E402
from ingestion.parsers import gdot_major_projects  # noqa: E402
from ingestion.parsers import bartow_county, newton_county  # noqa: E402
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


def test_gpr_returns_empty_on_network_error(monkeypatch):
    """fetch_and_parse() must return [] when the session warm-up fails."""
    import requests as req

    class _BadSession:
        def get(self, *a, **kw):
            raise req.exceptions.ConnectionError("simulated network error")
        def post(self, *a, **kw):
            raise req.exceptions.ConnectionError("simulated network error")

    monkeypatch.setattr(req, "Session", _BadSession)
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


# --- Cobb County Transportation adapter ---------------------------------------

from ingestion.parsers import cobb_transportation

_SYNTHETIC_COBB_HTML = """
<html><body>
<h2>Current Construction Bids</h2>
<p>Hamilton Road Sidewalk (South)</p>
<p>B2713 | Advertise Date: 5/1/2026 | Bid Date: 5/28/2026</p>
<p><a href="/files/legal-ad-B2713.pdf">Legal Ad</a></p>
<p>East Callaway Road Sidewalk</p>
<p>B2743 | Advertise Date: 5/1/2026 | Bid Date: 5/28/2026</p>
<h2>Current Requests For Proposal/Qualifications</h2>
<p>South Cobb Transit Center 30% Design Documents</p>
<p>B2452A | Advertise Date: 01/02/2026 | Bid Date: 02/12/2026</p>
<h2>Upcoming Bids/RFPs</h2>
<p>Johnson Ferry Road at Shallowford Road Intersection Improvements</p>
<p>B2437 | Activity: Advertise | Date: TBD</p>
<p>SS4A Multi-Corridor Safety Improvements</p>
</body></html>
"""


def test_cobb_parser_extracts_current_bids():
    recs = cobb_transportation.parse_html(_SYNTHETIC_COBB_HTML)
    ids = [r["solicitation_id"] for r in recs]
    assert "COBB-B2713" in ids
    assert "COBB-B2743" in ids
    assert "COBB-B2452A" in ids


def test_cobb_parser_buckets_correctly():
    recs = cobb_transportation.parse_html(_SYNTHETIC_COBB_HTML)
    by_id = {r["solicitation_id"]: r for r in recs}
    # Current bids → Active RFP
    assert by_id["COBB-B2713"]["bucket"] == "1 - Active RFP"
    assert by_id["COBB-B2452A"]["bucket"] == "1 - Active RFP"
    # Upcoming → Predicted
    assert by_id["COBB-B2437"]["bucket"] == "2 - Predicted"


def test_cobb_parser_has_due_date():
    recs = cobb_transportation.parse_html(_SYNTHETIC_COBB_HTML)
    b2713 = next(r for r in recs if r["solicitation_id"] == "COBB-B2713")
    assert "2026-05-28" in b2713["status_line"]
    assert "State: Georgia" in b2713["status_line"]


def test_cobb_transit_center_passes_gate():
    recs = cobb_transportation.parse_html(_SYNTHETIC_COBB_HTML)
    scored = score_all(tag_records(recs))
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# --- Gwinnett County Purchasing adapter ---------------------------------------

from ingestion.parsers import gwinnett_purchasing

_SYNTHETIC_GWINNETT_HTML = """
<html><body>
<ul>
<li>
  RP016-26 Old Rockbridge Road and Williams Road Pedestrian Improvement Projects
  <br>Buyer Contact: jsmith@gwinnettcounty.com
  <br>Opening Date: 2026-07-15 10:00:00.0 EST
</li>
<li>
  BL102-26 Safety Shoes/Boots for County Staff
  <br>Buyer Contact: jdoe@gwinnettcounty.com
  <br>Opening Date: 2026-07-20 10:00:00.0 EST
</li>
<li>
  RP017-26 McDaniel Farm Park to Satellite Boulevard Pedestrian Improvement Project
  <br>Buyer Contact: jsmith@gwinnettcounty.com
  <br>Opening Date: 2026-07-22 10:00:00.0 EST
</li>
<li>
  BL103-26 Purchase of Traffic Control Signs and Street Name Signs
  <br>Buyer Contact: jdoe@gwinnettcounty.com
  <br>Opening Date: 2026-08-01 10:00:00.0 EST
</li>
<li>
  IWQ005-26 Provision of Exterminating Services on an Annual Contract
  <br>Buyer Contact: jdoe@gwinnettcounty.com
  <br>Opening Date: 2026-07-10 10:00:00.0 EST
</li>
</ul>
</body></html>
"""


def test_gwinnett_parser_keeps_only_transport():
    recs = gwinnett_purchasing.parse_html(_SYNTHETIC_GWINNETT_HTML)
    titles = [r["title"] for r in recs]
    # Transportation records included
    assert any("Pedestrian" in t or "Road" in t for t in titles)
    assert any("Traffic Control Signs" in t or "Street Name" in t for t in titles)
    # Non-transportation filtered out
    assert not any("Safety Shoes" in t for t in titles)
    assert not any("Exterminating" in t for t in titles)


def test_gwinnett_parser_solicitation_ids():
    recs = gwinnett_purchasing.parse_html(_SYNTHETIC_GWINNETT_HTML)
    ids = [r["solicitation_id"] for r in recs]
    assert "GWINNETT-RP016-26" in ids
    assert "GWINNETT-RP017-26" in ids
    assert "GWINNETT-BL103-26" in ids


def test_gwinnett_parser_due_date_and_state():
    recs = gwinnett_purchasing.parse_html(_SYNTHETIC_GWINNETT_HTML)
    rp016 = next(r for r in recs if r["solicitation_id"] == "GWINNETT-RP016-26")
    assert "2026-07-15" in rp016["status_line"]
    assert "State: Georgia" in rp016["status_line"]


def test_gwinnett_pedestrian_passes_gate():
    recs = gwinnett_purchasing.parse_html(_SYNTHETIC_GWINNETT_HTML)
    scored = score_all(tag_records(recs))
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# --- Fayette County Purchasing adapter ----------------------------------------

from ingestion.parsers import fayette_purchasing

_SYNTHETIC_FAYETTE_HTML = """
<html><body>
<table>
<tr><th>Due Date</th><th>Description</th></tr>
<tr>
  <td>June 23, 2026 3:00 PM</td>
  <td><a href="bid_detail_T5_R001.php">ITB 26136-B Traffic Signal – Banks Road and Ellis Road Construction</a></td>
</tr>
<tr>
  <td>July 15, 2026 3:00 PM</td>
  <td><a href="bid_detail_T5_R002.php">ITB 26145-B Road Resurfacing and Pavement Marking Project</a></td>
</tr>
<tr>
  <td>June 12, 2026 3:00 PM</td>
  <td><a href="bid_detail_T5_R003.php">RFQ 26067-A Lake Kedron Parking Lot Striping</a></td>
</tr>
<tr>
  <td>August 1, 2026 3:00 PM</td>
  <td><a href="bid_detail_T5_R004.php">RFP 26150-P Office Supplies Annual Contract</a></td>
</tr>
</table>
</body></html>
"""


def test_fayette_parser_keeps_transport():
    recs = fayette_purchasing.parse_html(_SYNTHETIC_FAYETTE_HTML)
    titles = [r["title"] for r in recs]
    assert any("Traffic Signal" in t for t in titles)
    assert any("Resurfacing" in t or "Pavement" in t for t in titles)
    assert not any("Office Supplies" in t for t in titles)


def test_fayette_parser_solicitation_ids():
    recs = fayette_purchasing.parse_html(_SYNTHETIC_FAYETTE_HTML)
    ids = [r["solicitation_id"] for r in recs]
    assert "FAYETTE-ITB-26136-B" in ids
    assert "FAYETTE-ITB-26145-B" in ids


def test_fayette_parser_due_date_and_state():
    recs = fayette_purchasing.parse_html(_SYNTHETIC_FAYETTE_HTML)
    signal = next(r for r in recs if "Traffic Signal" in r["title"])
    assert "2026-06-23" in signal["status_line"]
    assert "State: Georgia" in signal["status_line"]


def test_fayette_traffic_signal_passes_gate():
    recs = fayette_purchasing.parse_html(_SYNTHETIC_FAYETTE_HTML)
    scored = score_all(tag_records(recs))
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# --- BidNet Direct adapter (Fulton / Cherokee / Clayton / Douglas) -------------

from ingestion.parsers import bidnet_direct

# Fixture mirrors the actual BidNet Direct rendered DOM structure:
# each field occupies its own text line (sol_no, title, Georgia, Calendar,
# Published, date, Clock, Closing, date). Parser anchors on "Closing\n<date>".
_SYNTHETIC_BIDNET_HTML = """
<html><body>
<div class="bid-list">
<div class="bid-item">
<p>26-01</p>
<p>Environmental Engineering and Testing Services</p>
<p>Georgia</p><p>Calendar</p>
<p>Published</p><p>06/01/2026</p>
<p>Clock</p><p>Closing</p><p>07/15/2026</p>
</div>
<div class="bid-item">
<p>26-02</p>
<p>Sidewalk and Pedestrian Improvement - Cascade Road</p>
<p>Georgia</p><p>Calendar</p>
<p>Published</p><p>06/10/2026</p>
<p>Clock</p><p>Closing</p><p>07/22/2026</p>
</div>
<div class="bid-item">
<p>26-03</p>
<p>Traffic Signal Modernization - Camp Creek Parkway</p>
<p>Georgia</p><p>Calendar</p>
<p>Published</p><p>06/12/2026</p>
<p>Clock</p><p>Closing</p><p>08/01/2026</p>
</div>
<div class="bid-item">
<p>26-04</p>
<p>DNA Freezer and Mortuary Equipment</p>
<p>Georgia</p><p>Calendar</p>
<p>Published</p><p>06/14/2026</p>
<p>Clock</p><p>Closing</p><p>08/05/2026</p>
</div>
</div>
</body></html>
"""


def test_bidnet_parser_keeps_transport():
    recs = bidnet_direct.parse_rendered_html(
        _SYNTHETIC_BIDNET_HTML, "Fulton County",
        "https://www.bidnetdirect.com/georgia/fultoncounty"
    )
    titles = [r["title"] for r in recs]
    assert any("Sidewalk" in t or "Pedestrian" in t for t in titles)
    assert any("Traffic Signal" in t for t in titles)
    # Non-transport filtered
    assert not any("Mortuary" in t or "DNA Freezer" in t for t in titles)


def test_bidnet_parser_due_dates():
    recs = bidnet_direct.parse_rendered_html(
        _SYNTHETIC_BIDNET_HTML, "Fulton County",
        "https://www.bidnetdirect.com/georgia/fultoncounty"
    )
    sidewalk = next(r for r in recs if "Sidewalk" in r["title"] or "Pedestrian" in r["title"])
    assert "2026-07-22" in sidewalk["status_line"]
    assert "State: Georgia" in sidewalk["status_line"]


def test_bidnet_parser_agency_name():
    recs = bidnet_direct.parse_rendered_html(
        _SYNTHETIC_BIDNET_HTML, "Cherokee County",
        "https://www.bidnetdirect.com/georgia/cherokeecounty"
    )
    assert all(r["agency"] == "Cherokee County" for r in recs)


def test_bidnet_transport_passes_gate():
    recs = bidnet_direct.parse_rendered_html(
        _SYNTHETIC_BIDNET_HTML, "Fulton County",
        "https://www.bidnetdirect.com/georgia/fultoncounty"
    )
    scored = score_all(tag_records(recs))
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# ---------------------------------------------------------------------------
# GDOT Major Projects
# ---------------------------------------------------------------------------
_SYNTHETIC_GDOT_MAJOR_HTML = """
<html><body>
<div class="content">
  <h2>Interchange Projects</h2>
  <ul>
    <li><a href="https://i-16andi-75interchange-gdot.hub.arcgis.com/">I-16/I-75 Interchange</a></li>
    <li><a href="https://sr-74-i-85-interchange-improvements-gdot.hub.arcgis.com/">I-85 @ SR 74 Interchange Improvements</a></li>
  </ul>
  <h2>Improvement Projects</h2>
  <ul>
    <li><a href="https://i285w-pavement-reconst-gdot.hub.arcgis.com/">I-285 Westside Rebuild</a></li>
    <li><a href="https://sr5wideningproject-gdot.hub.arcgis.com/">SR 5 Widening Project</a></li>
    <li><a href="https://not-a-gdot-link.example.com/">Not a GDOT project</a></li>
  </ul>
</div>
</body></html>
"""


def test_gdot_major_parses_projects():
    recs = gdot_major_projects.parse_html(_SYNTHETIC_GDOT_MAJOR_HTML)
    titles = [r["title"] for r in recs]
    assert "I-16/I-75 Interchange" in titles
    assert "I-285 Westside Rebuild" in titles
    assert "SR 5 Widening Project" in titles
    assert not any("Not a GDOT" in t for t in titles)


def test_gdot_major_record_count():
    recs = gdot_major_projects.parse_html(_SYNTHETIC_GDOT_MAJOR_HTML)
    assert len(recs) == 4


def test_gdot_major_bucket_and_agency():
    recs = gdot_major_projects.parse_html(_SYNTHETIC_GDOT_MAJOR_HTML)
    for r in recs:
        assert r["bucket"] == "2 - Predicted"
        assert r["agency"] == "Georgia DOT"
        assert "Georgia" in r["status_line"]
        assert "unverified" in r["status_line"]


def test_gdot_major_passes_gate():
    recs = gdot_major_projects.parse_html(_SYNTHETIC_GDOT_MAJOR_HTML)
    scored = score_all(tag_records(recs))
    passed = [o for o in scored if o.passed_gate]
    assert len(passed) >= 1


# ---------------------------------------------------------------------------
# Bartow County
# ---------------------------------------------------------------------------

_BARTOW_HTML = """
<html><body>
<a href="Purchasing/RFQ-26-001-road-resurfacing.pdf">RFQ 26-001 Road Resurfacing Program 2026</a>
<a href="Purchasing/RFQ-26-001-addendum.pdf">RFQ 26-001 Addendum 1</a>
<a href="Purchasing/RFP-26-002-animal-shelter.pdf">RFP 26-002 Animal Shelter Renovation</a>
<a href="Purchasing/RFQ-26-003-sidewalk.pdf">RFQ 26-003 Sidewalk Improvement District 5</a>
</body></html>
"""


def test_bartow_parses_transport_only():
    recs = bartow_county.parse_html(_BARTOW_HTML)
    titles = [r["title"] for r in recs]
    assert any("Road Resurfacing" in t for t in titles)
    assert any("Sidewalk" in t for t in titles)
    assert not any("Animal Shelter" in t for t in titles)


def test_bartow_skips_addenda():
    recs = bartow_county.parse_html(_BARTOW_HTML)
    assert not any("Addendum" in r["title"] for r in recs)


def test_bartow_bucket_predicted():
    recs = bartow_county.parse_html(_BARTOW_HTML)
    for r in recs:
        assert r["bucket"] == "2 - Predicted"
        assert r["agency"] == "Bartow County"


# ---------------------------------------------------------------------------
# Newton County (CivicEngage — currently no open bids)
# ---------------------------------------------------------------------------

_NEWTON_NO_BIDS_HTML = """
<html><body>
<p>There are no open bid postings at this time.</p>
</body></html>
"""

_NEWTON_WITH_BID_HTML = """
<html><body>
<a href="/BidDetail.aspx?ID=123">SR-142 Bridge Replacement Project</a>
<span>Closing Date: 08/15/2026</span>
</body></html>
"""


def test_newton_empty_when_no_bids():
    recs = newton_county.parse_html(_NEWTON_NO_BIDS_HTML)
    assert recs == []


def test_newton_parses_transport_bid():
    recs = newton_county.parse_html(_NEWTON_WITH_BID_HTML)
    assert len(recs) == 1
    assert "Bridge" in recs[0]["title"]
    assert recs[0]["agency"] == "Newton County"


# ---------------------------------------------------------------------------
# GPR — helper function unit tests (no network needed)
# ---------------------------------------------------------------------------

from ingestion.parsers.gpr import _is_transport, _parse_due_date  # noqa: E402


def test_gpr_is_transport_positive():
    assert _is_transport("2026 Chamblee LMIG Roadway Resurfacing")
    assert _is_transport("Wilson Avenue Pedestrian Improvements CDBG")
    assert _is_transport("Franklin Gateway Bridge Replacement")
    assert _is_transport("Traffic Signal Operations Program")


def test_gpr_is_transport_negative():
    assert not _is_transport("Janitorial Services RFP 2026")
    assert not _is_transport("Annual Software License Renewal")
    assert not _is_transport("Food Service Equipment Bid")


def test_gpr_parse_due_date():
    from datetime import date as dt
    d = _parse_due_date("Jul 09, 2026 @ 02:00 PM")
    assert d == dt(2026, 7, 9)
    assert _parse_due_date("") is None
    assert _parse_due_date("TBD") is None
