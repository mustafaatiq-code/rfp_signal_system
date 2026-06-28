"""
Adapter for Gwinnett County Purchasing solicitations portal.

URL: https://www.gwinnettcounty.com/government/departments/financial-services/purchasing/solicitations
Type: Static HTML — publicly accessible, no authentication required.

Solicitation types on this page:
  BL  — Invitation to Bid (construction / goods)
  RP  — Request for Proposal
  RFQ — Request for Qualifications
  IWQ — Informal Written Quote (small purchases, typically < threshold)
  PA  — Annual Price Agreement

We filter for transportation-relevant titles only (this is a general county
purchasing portal covering everything from soap to road projects).

Relevance for GMG: pedestrian improvement projects, intersection upgrades,
traffic signal work, road/sidewalk construction map to CEI and Traffic Ops.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

AGENCY = "Gwinnett County"
PORTAL_URL = (
    "https://www.gwinnettcounty.com/government/departments/"
    "financial-services/purchasing/solicitations"
)

# Solicitation number pattern: "RP016-26", "BL102-26", "IWQ003-26", "RFQ001-26"
_SOL_NO_RE = re.compile(
    r"\b([A-Z]{2,3}\d{3,4}-\d{2})\b(?:\s+[A-Z]{3})?",
    re.IGNORECASE,
)
_OPENING_DATE_RE = re.compile(
    r"Opening Date[:\s]+(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)
_DATE_FALLBACK_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Type prefix → pipeline bucket
_TYPE_BUCKET = {
    "BL": "1 - Active RFP",
    "RP": "1 - Active RFP",
    "RFQ": "1 - Active RFP",
    "IWQ": "2 - Predicted",
    "PA": "2 - Predicted",
}

# Title must contain at least one of these to pass the transportation filter.
_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", "drive", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", "trail", "path",
    "bridge", "overpass", "underpass", "culvert",
    "traffic", "signal", "sign", "pavement", "striping", "marking",
    "transportation", "transit", "bus", "rail",
    "drainage", "stormwater",
    "roundabout", "interchange",
    "safety improvement", "access management",
]


def _is_transport(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _TRANSPORT_KEYWORDS)


def _extract_opening_date(lines: list[str]) -> Optional[str]:
    for ln in lines:
        m = _OPENING_DATE_RE.search(ln)
        if m:
            return m.group(1)
    for ln in lines:
        m = _DATE_FALLBACK_RE.search(ln)
        if m:
            return m.group(1)
    return None


def _sol_year(sol_no: str) -> int:
    """Extract year from solicitation number suffix (e.g. RP016-26 → 2026)."""
    m = re.search(r"-(\d{2})$", sol_no)
    if m:
        suffix = int(m.group(1))
        return 2000 + suffix if suffix < 50 else 1900 + suffix
    return date.today().year


def parse_html(html: str) -> List[dict]:
    """Parse Gwinnett County solicitations page HTML into record dicts."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("Gwinnett: HTML parse error: %s", exc)
        return []

    text = soup.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    records: List[dict] = []
    seen_ids: set = set()

    for i, line in enumerate(lines):
        m = _SOL_NO_RE.search(line)
        if not m:
            continue

        sol_no = m.group(1).upper()
        sid = f"GWINNETT-{sol_no}"
        if sid in seen_ids:
            continue

        # Title: text after the solicitation number on the same line
        title = line[m.end():].lstrip(" -–|").strip()
        if not title and i + 1 < len(lines):
            title = lines[i + 1]

        if not title or not _is_transport(title):
            continue

        seen_ids.add(sid)

        # Opening date: look in the next 6 lines
        context = lines[i: min(i + 7, len(lines))]
        opening_date = _extract_opening_date(context)

        # Type prefix determines bucket
        type_prefix = re.match(r"[A-Z]+", sol_no)
        bucket = _TYPE_BUCKET.get(
            type_prefix.group(0) if type_prefix else "", "1 - Active RFP"
        )

        status_parts = [f"Solicitation: {sol_no}"]
        if opening_date:
            status_parts.insert(0, f"Due date: {opening_date}")
        status_parts.append("State: Georgia")

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": sid,
            "year": _sol_year(sol_no),
            "bucket": bucket,
            "status_line": " | ".join(status_parts),
            "source_url": PORTAL_URL,
        })

    logger.info("Gwinnett Purchasing: %d transportation solicitations parsed",
                len(records))
    return records


def fetch_and_parse() -> List[dict]:
    """Fetch and parse Gwinnett County purchasing solicitations page."""
    from ingestion.fetcher import fetch_static

    result = fetch_static(PORTAL_URL)
    if not result.html:
        logger.warning(
            "Gwinnett Purchasing: empty response (error: %s)", result.error
        )
        return []

    return parse_html(result.html)
