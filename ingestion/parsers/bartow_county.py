"""
Adapter for Bartow County (GA) Projects for Bid page.

https://www.bartowcountyga.gov/departments/county_commissioner/projects_for_bid/index.php

Static HTML page listing bid PDFs as anchor tags. Due dates are not exposed in
the HTML — they're inside the PDF documents — so records are bucketed as
"2 - Predicted". The stale filter in run_pipeline.py drops any record whose
year < current year, so 2024-numbered bids are automatically discarded once the
year turns.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Bartow County"
PORTAL_URL = (
    "https://www.bartowcountyga.gov/departments/county_commissioner/"
    "projects_for_bid/index.php"
)
BASE_URL = "https://www.bartowcountyga.gov"

# Solicitation number: "24-005", "24-55-08", "26-RFP-001", etc.
_SOL_RE = re.compile(r"\b(\d{2}[-\s]\S+)", re.IGNORECASE)

# Year prefix from solicitation number
_YEAR_RE = re.compile(r"^(\d{2})")

_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", "trail", "multiuse",
    "bridge", "culvert", "drainage", "stormwater",
    "traffic", "signal", "traffic sign",
    "pavement", "resurfacing", "asphalt", "striping", "milling", "overlay",
    "transportation", "transit",
    "roundabout", "access management",
    "guardrail", "grading", "right-of-way",
    "safe streets", "ss4a", "mpo",
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

    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)

        # Only PDF bid links
        if not href.lower().endswith(".pdf"):
            continue
        if not title or len(title) < 10:
            continue
        # Skip addenda — we want the primary solicitation only
        if "addendum" in title.lower():
            continue
        if not _is_transport(title):
            continue
        if title in seen:
            continue
        seen.add(title)

        # Extract solicitation number from title text
        sol_m = _SOL_RE.search(title)
        sol_no = sol_m.group(1).strip() if sol_m else re.sub(r"\W+", "-", title[:30])

        # Derive year from two-digit prefix of sol number
        yr_m = _YEAR_RE.match(sol_no)
        year = (2000 + int(yr_m.group(1))) if yr_m else date.today().year

        # Absolute URL
        source_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": f"BARTOW-{sol_no}",
            "year": year,
            "bucket": "2 - Predicted",
            "status_line": (
                f"Due date: see PDF — {source_url} | State: Georgia"
            ),
            "source_url": source_url,
        })

    logger.info("Bartow County: %d transport bids found", len(records))
    return records


def fetch_and_parse() -> List[dict]:
    from ingestion.fetcher import fetch_static
    result = fetch_static(PORTAL_URL)
    if result.status_code != 200 or not result.html:
        logger.warning("Bartow County page unreachable (status=%s)", result.status_code)
        return []
    return parse_html(result.html)
