"""
Adapter for Newton County (GA) Bid Postings page (CivicEngage platform).

https://www.newtoncountyga.gov/Bids.aspx

Standard CivicEngage government portal. Shows open bids in a sortable table.
Returns empty list when no bids are posted; the pipeline handles this gracefully.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Newton County"
PORTAL_URL = "https://www.newtoncountyga.gov/Bids.aspx"

_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", "trail", "multiuse",
    "bridge", "culvert", "drainage", "stormwater",
    "traffic", "signal",
    "pavement", "resurfacing", "asphalt", "striping", "milling", "overlay",
    "transportation", "transit",
    "roundabout", "access management",
    "guardrail", "grading", "right-of-way",
]


def _is_transport(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _TRANSPORT_KEYWORDS)


def parse_html(html: str) -> List[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 not installed")
        return []

    soup = BeautifulSoup(html, "html.parser")
    records: List[dict] = []
    seen: set = set()

    # CivicEngage lists bids as <a> tags under the bid table
    # Each link text is the bid title; closing date is nearby text
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        title = a.get_text(strip=True)

        # CivicEngage bid detail links contain "BidDetail" or "bid" in the path
        if "bid" not in href.lower() and "Bid" not in href:
            continue
        if not title or len(title) < 8:
            continue
        if not _is_transport(title):
            continue
        if title in seen:
            continue
        seen.add(title)

        # Try to find a closing date in nearby parent text
        parent_text = ""
        parent = a.find_parent()
        if parent:
            parent_text = parent.get_text(separator=" ", strip=True)
        due_date_str = ""
        dm = _DATE_RE.search(parent_text)
        if dm:
            due_date_str = dm.group(1)

        # Parse due date
        due_date: date | None = None
        if due_date_str:
            try:
                from dateutil import parser as dp
                due_date = dp.parse(due_date_str).date()
            except Exception:
                pass

        year = due_date.year if due_date else date.today().year

        source_url = href if href.startswith("http") else f"https://www.newtoncountyga.gov{href}"

        status = f"Due date: {due_date.isoformat()}" if due_date else "Due date: see portal"
        status += " | State: Georgia"

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": f"NEWTON-{re.sub(r'[^A-Z0-9]', '-', title.upper())[:30]}",
            "year": year,
            "bucket": "1 - Active RFP" if due_date else "2 - Predicted",
            "status_line": status,
            "source_url": source_url,
        })

    logger.info("Newton County: %d transport bids found", len(records))
    return records


def fetch_and_parse() -> List[dict]:
    from ingestion.fetcher import fetch_static
    result = fetch_static(PORTAL_URL)
    if result.status_code != 200 or not result.html:
        logger.warning("Newton County page unreachable (status=%s)", result.status_code)
        return []
    return parse_html(result.html)
