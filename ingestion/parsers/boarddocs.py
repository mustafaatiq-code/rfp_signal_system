"""
Adapter for BoardDocs — platform used by GA school boards and county commissions
to publish meeting agendas, minutes, and resolutions.

Portal: https://go.boarddocs.com/{state}/{org}/Board.nsf/

Target agencies (GA transportation-relevant):
  * Savannah-Chatham County Schools (sccs) — E-SPLOST votes
  * Gwinnett County Public Schools (gcps) — E-SPLOST / CIP
  * Henry County BOE (hcbe) — E-SPLOST / CIP
  * DeKalb County Schools (dekalbschools) — E-SPLOST
  * Cobb County Schools (cobbboe) — E-SPLOST

NOTE: BoardDocs returns empty shell pages (HTTP 200, 152 bytes) when accessed
from non-permitted IPs / without a browser session cookie. This is the same
pattern as GPR and GDOT — the adapter degrades gracefully and returns [] with
a log entry. It can be run from a permitted environment (e.g., a laptop browser
session, or triggered via BoardDocs email subscriptions).

Transportation signal value:
  School board SPLOST/E-SPLOST votes are early signals that construction
  projects will follow — typically 12-24 months before an RFP is issued.
  County commission T-SPLOST votes signal transportation project funding.
  Meeting resolutions mentioning CEI, design, program management indicate
  active procurement planning.

Page structure (when accessible):
  /Public?open → meeting list table (date, meeting name, agenda link)
  /goto?open&id={id} → individual agenda item text

Signal classification:
  "splost" "esplost" "tsplost" → SPLOST
  "cip" "capital improvement" → Capital Budget
  "bond" → Bond Issuance
  "transportation" "roadway" "bridge" "cei" → Planning Study / Active RFP
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# Known Georgia agencies on BoardDocs — org code : display name
# Codes validated against go.boarddocs.com URL pattern (state=ga)
GA_AGENCIES: dict[str, str] = {
    "sccs":           "Savannah-Chatham County Schools",
    "gcps":           "Gwinnett County Public Schools",
    "hcbe":           "Henry County Board of Education",
    "dekalbschools":  "DeKalb County Schools",
    "cobbboe":        "Cobb County Schools",
    "cherokeecounty": "Cherokee County Schools",
    "claytonco":      "Clayton County Schools",
    "fayette":        "Fayette County Schools",
}

_BASE_URL = "https://go.boarddocs.com/ga/{org}/Board.nsf/Public?open"
_ITEM_URL  = "https://go.boarddocs.com/ga/{org}/Board.nsf/goto?open&id={item_id}"

# Keywords that signal transportation procurement activity in meeting minutes
_TRANSPORT_SIGNAL_WORDS = [
    "splost", "esplost", "tsplost",
    "capital improvement", "cip",
    "bond", "bond referendum",
    "transportation", "roadway", "highway", "bridge",
    "cei ", "construction inspection",
    "planning study", "corridor study",
    "contract", "rfp", "solicitation", "procurement",
    "traffic", "signal", "pedestrian", "bicycle",
    "paving", "resurfacing", "stormwater", "drainage",
]

# Map meeting keyword → signal type for tagging
_SIGNAL_MAP = {
    "splost": "SPLOST",
    "esplost": "SPLOST",
    "tsplost": "SPLOST",
    "bond": "Bond Issuance",
    "capital improvement": "Capital Budget",
    "cip": "Capital Budget",
    "planning study": "Planning Study",
    "corridor study": "Planning Study",
    "rfp": "Active RFP",
    "solicitation": "Active RFP",
}


def _clean(html: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_shell(html: str) -> bool:
    """Detect the empty-shell response BoardDocs returns when IP-blocked."""
    return len(html) < 500 and "<pre" in html.lower()


def _signal_type(text: str) -> str:
    tl = text.lower()
    for kw, sig in _SIGNAL_MAP.items():
        if kw in tl:
            return sig
    return "Political Meetings"


def parse_meeting_list_html(html: str, agency_name: str, org: str) -> List[dict]:
    """Parse the BoardDocs public meeting list page.

    Expected structure (when accessible):
      <table id="bd-meetings-table">
        <tr>
          <td class="bd-meeting-date">06/02/2026</td>
          <td><a ...>Board Meeting</a></td>
          <td>Agenda</td>
        </tr>
      </table>
    """
    text = _clean(html)

    # Find meeting rows: date + name pairs (works across various BoardDocs themes)
    row_re = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{4})\s+"     # date
        r"([A-Z][^.]{10,120}?)"             # meeting name
        r"(?=\s+\d{1,2}/\d{1,2}|\s*$)",    # lookahead: next date or end
        re.IGNORECASE,
    )

    # Limit to recent meetings (90 days back)
    cutoff = date.today() - timedelta(days=90)

    records: List[dict] = []
    for m in row_re.finditer(text):
        raw_date, meeting_name = m.group(1), m.group(2).strip()
        try:
            from dateutil import parser as dp
            meeting_date = dp.parse(raw_date).date()
        except Exception:
            continue
        if meeting_date < cutoff:
            continue

        # Check if this meeting has transportation-relevant content in its name
        has_signal = any(kw in meeting_name.lower() for kw in _TRANSPORT_SIGNAL_WORDS)
        if not has_signal:
            continue

        signal = _signal_type(meeting_name)
        records.append({
            "agency": agency_name,
            "title": f"{meeting_name} [{agency_name}]",
            "solicitation_id": f"BOARDDOCS-{org}-{raw_date.replace('/','')}-{len(records)}",
            "year": meeting_date.year,
            "bucket": "2 - Predicted",
            "status_line": (
                f"Meeting: {raw_date} | {signal} signal | "
                f"Source: go.boarddocs.com/ga/{org} | State: Georgia"
            ),
            "source_url": _BASE_URL.format(org=org),
        })

    return records


def fetch_and_parse(
    agencies: Optional[dict[str, str]] = None,
) -> List[dict]:
    """Fetch BoardDocs public meeting agendas for GA agencies.

    Requires a non-blocked IP. Returns [] gracefully if IP-blocked (the page
    returns a 200-OK empty shell — same pattern as GPR/GDOT).

    To add a new agency: find its org code from the BoardDocs URL
    (go.boarddocs.com/ga/{ORG}/Board.nsf) and add it to GA_AGENCIES.
    """
    from ingestion.fetcher import fetch_dynamic

    target = agencies or GA_AGENCIES
    records: List[dict] = []

    for org, name in target.items():
        url = _BASE_URL.format(org=org)
        try:
            result = fetch_dynamic(url, settle_ms=6000)
            html = result.html or ""

            if _is_shell(html):
                logger.info(
                    "BoardDocs %s (%s): IP-blocked / empty shell — skipping. "
                    "Run from a permitted environment or use BoardDocs email alerts.",
                    name, org,
                )
                continue

            batch = parse_meeting_list_html(html, name, org)
            logger.info("BoardDocs %s: %d transportation-relevant meetings found", name, len(batch))
            records.extend(batch)

        except Exception as exc:  # noqa: BLE001
            logger.warning("BoardDocs %s (%s): fetch failed: %s", name, org, exc)

    return records
