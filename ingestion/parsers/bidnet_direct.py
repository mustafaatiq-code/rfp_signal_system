"""
Generic adapter for BidNet Direct Georgia Purchasing Group portals.

BidNet Direct is a JS-rendered SPA used by 4 ARC metro Atlanta counties:
  - Fulton County:   https://www.bidnetdirect.com/georgia/fultoncounty
  - Cherokee County: https://www.bidnetdirect.com/georgia/cherokeecounty
  - Clayton County:  https://www.bidnetdirect.com/georgia/claytoncounty
  - Douglas County:  https://www.bidnetdirect.com/georgia/douglascounty

Fetch strategy: fetch_dynamic (Playwright headless Chromium). Returns [] if
Playwright is not installed or the page is bot-gated.

All four portals are general county purchasing sites — filtered for
transportation-relevant titles (road, signal, sidewalk, bridge, etc.).
One adapter, four county URLs — county name is passed at call time.

Solicitation number format observed: [YY][TYPE][SEQ][COUNTY]-[CODE]
e.g. "26ITB042126C-MH". Parsed with a flexible regex; gracefully skips
records where the number cannot be extracted.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

# County slug → (display name, URL)
COUNTY_PORTALS: dict[str, tuple[str, str]] = {
    "fulton": (
        "Fulton County",
        "https://www.bidnetdirect.com/georgia/fultoncounty",
    ),
    "cherokee": (
        "Cherokee County",
        "https://www.bidnetdirect.com/georgia/cherokeecounty",
    ),
    "clayton": (
        "Clayton County",
        "https://www.bidnetdirect.com/georgia/claytoncounty",
    ),
    "douglas": (
        "Douglas County",
        "https://www.bidnetdirect.com/georgia/douglascounty",
    ),
    "hcwa": (
        "Henry County Water Authority",
        "https://www.bidnetdirect.com/georgia/henrycountywaterauthority",
    ),
}

# Solicitation number formats observed on BidNet Direct:
#   "26-24"        (Clayton/Douglas style: YY-seq)
#   "26ITB042-MH"  (Fulton/Cherokee style: YYTYPE SEQ-CODE)
_SOL_NO_RE = re.compile(
    r"^(\d{2}-\d+|"
    r"\d{2}(?:ITB|RFP|RFQ|RFI|IFB)\d{3,8}[A-Z]*(?:-[A-Z]{2})?)$",
    re.IGNORECASE,
)
# Date on its own line (MM/DD/YYYY)
_DATE_LINE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})$")
# Lines that carry a section label we use as anchors
_CLOSING_LABEL_RE = re.compile(r"^Clos(?:ing|ed)$", re.IGNORECASE)
_PUBLISHED_LABEL_RE = re.compile(r"^Published$", re.IGNORECASE)

_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", " drive", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", " trail", "multiuse trail",
    "bridge", "culvert", "drainage", "stormwater",
    "traffic", "signal", "traffic sign", "street sign",
    "pavement", "road resurfacing", "pavement resurfacing", "asphalt",
    "striping", "pavement marking", "milling", "overlay",
    "transportation engineering", "transportation planning",
    "transportation infrastructure", "transportation a&e",
    "transit center", "bus rapid", "brt",
    "roundabout", "access management", "guardrail",
    "grading", "earthwork", "right-of-way",
    "transportation a&e", "transportation cei",
    "bridge inspection", "pavement inspection", "roadway inspection",
    "safety improvement", "corridor safety",
]

# Lines that are noise and should be skipped when looking for a title
_NOISE_RE = re.compile(
    r"(?:published|closing|closed|register|login|sign in|solicitation|"
    r"open bids|georgia purchasing|bidnet|loading|view details|filter|"
    r"sort by|results|\d+ results)",
    re.IGNORECASE,
)


def _is_transport(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _TRANSPORT_KEYWORDS)


def _to_iso(date_str: str) -> Optional[str]:
    try:
        from dateutil import parser as dp
        return dp.parse(date_str).date().isoformat()
    except Exception:
        return None


_STRUCTURAL_LINES = frozenset({
    "georgia", "calendar", "clock", "published", "closing", "closed",
    "awarded", "open solicitations", "closed solicitations",
    "awarded solicitations", "register", "login",
})


def parse_rendered_html(html: str, agency: str, portal_url: str) -> List[dict]:
    """Parse BidNet Direct rendered page HTML into record dicts.

    The rendered DOM puts each field on its own line:
        {sol_no}          e.g. "26-24"
        {title}           e.g. "Road Resurfacing Annual Contract"
        Georgia
        Calendar
        Published
        {published_date}  e.g. "06/23/2026"
        Clock
        Closing
        {closing_date}    e.g. "07/28/2026"

    Strategy: scan for a line that is exactly "Closing", then read the date
    from the next line, then walk back for title and solicitation number.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("BidNet Direct (%s): parse error: %s", agency, exc)
        return []

    text = soup.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    records: List[dict] = []
    seen: set = set()

    for i, line in enumerate(lines):
        # Anchor: line is exactly "Closing" and the next line is MM/DD/YYYY
        if not _CLOSING_LABEL_RE.match(line):
            continue
        if i + 1 >= len(lines):
            continue
        dm = _DATE_LINE_RE.match(lines[i + 1])
        if not dm:
            continue
        closing_iso = _to_iso(dm.group(1))

        # Walk back up to 15 lines for sol number and title
        sol_no: Optional[str] = None
        title: Optional[str] = None
        for j in range(i - 1, max(i - 16, -1), -1):
            prev = lines[j]
            prev_l = prev.lower()

            # Skip structural / navigation noise
            if prev_l in _STRUCTURAL_LINES:
                continue
            if _NOISE_RE.search(prev):
                continue
            # Skip bare dates
            if _DATE_LINE_RE.match(prev):
                continue

            # Solicitation number
            if not sol_no and _SOL_NO_RE.match(prev):
                sol_no = prev.upper()
                continue

            # Candidate title: at least 8 chars, not a sol number
            if not title and len(prev) >= 8 and not _SOL_NO_RE.match(prev):
                title = prev

            if sol_no and title:
                break

        if not title or not _is_transport(title):
            continue

        sid_key = sol_no or f"{agency}-{closing_iso}-{len(records)}"
        sid = f"BIDNET-{sid_key}"
        if sid in seen:
            continue
        seen.add(sid)

        status_parts: list[str] = []
        if closing_iso:
            status_parts.append(f"Due date: {closing_iso}")
        if sol_no:
            status_parts.append(f"Solicitation: {sol_no}")
        status_parts.append("State: Georgia")

        year = int(closing_iso[:4]) if closing_iso else date.today().year

        records.append({
            "agency": agency,
            "title": title,
            "solicitation_id": sid,
            "year": year,
            "bucket": "1 - Active RFP",
            "status_line": " | ".join(status_parts),
            "source_url": portal_url,
        })

    logger.info("BidNet Direct (%s): %d transportation solicitations parsed",
                agency, len(records))
    return records


def fetch_and_parse_county(county_slug: str) -> List[dict]:
    """Fetch and parse one BidNet Direct county portal."""
    if county_slug not in COUNTY_PORTALS:
        logger.warning("BidNet Direct: unknown county slug %r", county_slug)
        return []

    agency, url = COUNTY_PORTALS[county_slug]
    from ingestion.fetcher import fetch_dynamic

    result = fetch_dynamic(url, settle_ms=6000)
    if result.fetched_via == "blocked":
        logger.info(
            "BidNet Direct (%s): bot-gated (%s) — returning []",
            agency, result.error,
        )
        return []
    if not result.html:
        logger.warning(
            "BidNet Direct (%s): empty response (error: %s)", agency, result.error
        )
        return []

    return parse_rendered_html(result.html, agency, url)


def fetch_and_parse() -> List[dict]:
    """Fetch all four BidNet Direct county portals and return combined records."""
    all_records: List[dict] = []
    for slug in COUNTY_PORTALS:
        try:
            records = fetch_and_parse_county(slug)
            all_records.extend(records)
        except Exception as exc:  # noqa: BLE001
            agency = COUNTY_PORTALS[slug][0]
            logger.warning("BidNet Direct (%s): unexpected error: %s", agency, exc)
    return all_records
