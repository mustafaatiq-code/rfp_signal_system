"""
Adapter for Cobb County Department of Transportation bid portal.

URL: https://www.cobbcounty.gov/transportation/current-transportation-bids-rfps
Type: Static HTML — publicly accessible, no authentication required.

The page is transportation-specific (no keyword filtering needed). It lists:
  * Current Construction Bids — active IFBs open for response
  * Current Requests For Proposal/Qualifications — active RFPs/RFQs
  * Upcoming Bids/RFPs — pre-solicitation pipeline signals

Each project carries a project number in the format B####[A] (e.g. B2713,
B2452A). This pattern is used as the parsing anchor.

Relevance for GMG: sidewalk/pedestrian projects, intersection improvements,
transit center design, SS4A corridor safety studies, and the A&E engineering
prequalification list map directly to CEI, Traffic Ops, A&E, and Program Mgmt.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

AGENCY = "Cobb County Transportation"
PORTAL_URL = (
    "https://www.cobbcounty.gov/transportation/current-transportation-bids-rfps"
)

# Project number: B2713, B2452A, B2437, etc.
_PROJECT_NO_RE = re.compile(r"\bB\d{4}[A-Z]?\b")
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
# Match the date that immediately follows a "Bid Date" / "Due Date" label
_BID_DATE_LABEL_RE = re.compile(
    r"\b(?:bid date|due date|closing)[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
# Lines that are PDF link text, section labels, or other non-title noise
_NOISE_LINE_RE = re.compile(
    r"(?:addendum|legal ad|special provisions|section \d+|"
    r"specifications|planholder|advertise date|bid date|due date|"
    r"project no|pre-bid|pre bid|"
    r"request for proposal|request for qualif|rfp|rfq|ifb|"
    r"gdot pi no|pi no:|gdot project|activity:|current construction|"
    r"current request|upcoming bid|upcoming rfp|"
    r"transportation bids|bids and rfp)",
    re.IGNORECASE,
)


def _to_iso(date_str: str) -> Optional[str]:
    """Convert M/D/YYYY → YYYY-MM-DD, return None on failure."""
    try:
        from dateutil import parser as dp
        return dp.parse(date_str).date().isoformat()
    except Exception:
        return None


def _extract_bid_date(lines: list[str]) -> Optional[str]:
    """Find the date after a 'bid date' / 'due date' label; fall back to any date."""
    for line in lines:
        m = _BID_DATE_LABEL_RE.search(line)
        if m:
            return _to_iso(m.group(1))
    # Fall back to any date found in the context lines
    for line in lines:
        m = _DATE_RE.search(line)
        if m:
            return _to_iso(m.group(1))
    return None


def parse_html(html: str) -> List[dict]:
    """Parse Cobb County Transportation bids page HTML into record dicts."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("Cobb: HTML parse error: %s", exc)
        return []

    # Extract structured text lines (preserves element boundaries)
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    records: List[dict] = []
    current_bucket = "1 - Active RFP"
    seen_ids: set = set()

    for i, line in enumerate(lines):
        tl = line.lower()

        # Track section context to assign bucket
        if re.search(r"upcoming|future bid|future rfp", tl):
            current_bucket = "2 - Predicted"
        elif re.search(r"current (construction|request|rfp|rfq|bid)", tl):
            current_bucket = "1 - Active RFP"

        # Project anchor: B-number
        m = _PROJECT_NO_RE.search(line)
        if not m:
            continue

        project_no = m.group(0)
        sid = f"COBB-{project_no}"
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        # Title: walk back through preceding lines, skipping noise (addenda,
        # PDF links, section labels, date rows) to find the project name.
        title_candidate = line[: m.start()].strip().rstrip("–-|:, ")
        if not title_candidate or _NOISE_LINE_RE.search(title_candidate) or len(title_candidate) < 6:
            title_candidate = ""
            for j in range(i - 1, max(i - 15, -1), -1):
                cand = lines[j].strip()
                if cand and not _NOISE_LINE_RE.search(cand) and len(cand) >= 8:
                    title_candidate = cand
                    break

        title = title_candidate or f"Cobb County Project {project_no}"

        # Dates: look in this line and the next 6 lines
        context_lines = lines[i: min(i + 7, len(lines))]
        bid_date = _extract_bid_date(context_lines)

        status_parts = [f"Project No: {project_no}"]
        if bid_date:
            status_parts.insert(0, f"Due date: {bid_date}")
        status_parts.append("State: Georgia")

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": sid,
            "year": int(bid_date[:4]) if bid_date else date.today().year,
            "bucket": current_bucket,
            "status_line": " | ".join(status_parts),
            "source_url": PORTAL_URL,
        })

    logger.info("Cobb Transportation: %d projects parsed", len(records))
    return records


def fetch_and_parse() -> List[dict]:
    """Fetch and parse Cobb County Transportation bid portal."""
    from ingestion.fetcher import fetch_static

    result = fetch_static(PORTAL_URL)
    if not result.html:
        logger.warning(
            "Cobb Transportation: empty response (error: %s)", result.error
        )
        return []

    return parse_html(result.html)
