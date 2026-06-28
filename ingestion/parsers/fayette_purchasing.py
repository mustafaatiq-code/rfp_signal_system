"""
Adapter for Fayette County Purchasing bid portal.

URL: https://fayettecountyga.gov/departments/purchasing/bids___request_for_proposals.php
Type: Static HTML table — publicly accessible, no authentication required.

Table structure: two columns (Due Date | Description link).
Solicitation number format: ITB 26136-B, RFP 26087-P, RFQ 26067-A, RFI 26012-I

General county purchasing portal — filtered for transportation titles.
Relevance for GMG: traffic signal construction, road resurfacing, sidewalk/pedestrian
work, pavement marking maps to CEI and Traffic Ops service lines.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

AGENCY = "Fayette County"
PORTAL_URL = (
    "https://fayettecountyga.gov/departments/purchasing/"
    "bids___request_for_proposals.php"
)
BASE_URL = "https://fayettecountyga.gov/departments/purchasing/"

# Solicitation type prefix
_SOL_TYPE_RE = re.compile(
    r"^(ITB|RFP|RFQ|RFI|IFB)\s+([\w\-]+)\s*[:\–\-]?\s*(.*)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)

_TYPE_BUCKET = {
    "ITB": "1 - Active RFP",
    "IFB": "1 - Active RFP",
    "RFP": "1 - Active RFP",
    "RFQ": "1 - Active RFP",
    "RFI": "2 - Predicted",
}

_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", " drive", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", " trail", "multiuse trail",
    "bridge", "culvert", "drainage", "stormwater",
    "traffic", "signal", "traffic sign", "street sign",
    "pavement", "road resurfacing", "pavement resurfacing", "asphalt",
    "striping", "pavement marking", "milling", "overlay",
    "transportation", "transit",
    "roundabout", "access management", "traffic safety",
    "guardrail", "grading", "earthwork", "right-of-way",
]


def _is_transport(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _TRANSPORT_KEYWORDS)


def _parse_due_date(raw: str) -> Optional[str]:
    m = _DATE_RE.search(raw)
    if not m:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(m.group(0)).date().isoformat()
    except Exception:
        return None


def parse_html(html: str) -> List[dict]:
    """Parse Fayette County purchasing page HTML into record dicts."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("Fayette: HTML parse error: %s", exc)
        return []

    records: List[dict] = []
    seen: set = set()

    # Each bid is an <a> link pointing to bid_detail_*.php
    for a in soup.find_all("a", href=re.compile(r"bid_detail", re.IGNORECASE)):
        raw_text = a.get_text(" ", strip=True)

        # Parse "ITB 26136-B Traffic Signal – Banks Road..." from link text
        m = _SOL_TYPE_RE.match(raw_text)
        if m:
            sol_type = m.group(1).upper()
            sol_no = m.group(2).strip()
            title = m.group(3).strip()
        else:
            # Fallback: whole text is the title, no structured number
            sol_type = "ITB"
            sol_no = f"FAYETTE-{len(records)}"
            title = raw_text

        if not title or not _is_transport(title):
            continue

        full_sol = f"{sol_type} {sol_no}"
        sid = f"FAYETTE-{sol_type}-{sol_no}"
        if sid in seen:
            continue
        seen.add(sid)

        # Due date from the first <td> of the parent <tr>
        due_date: Optional[str] = None
        tr = a.find_parent("tr")
        if tr:
            tds = tr.find_all("td")
            if tds:
                due_date = _parse_due_date(tds[0].get_text(" ", strip=True))

        # Detail URL
        href = a.get("href", "")
        detail_url = href if href.startswith("http") else BASE_URL + href

        bucket = _TYPE_BUCKET.get(sol_type, "1 - Active RFP")
        status_parts = [f"Solicitation: {full_sol}"]
        if due_date:
            status_parts.insert(0, f"Due date: {due_date}")
        status_parts.append("State: Georgia")

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": sid,
            "year": int(due_date[:4]) if due_date else date.today().year,
            "bucket": bucket,
            "status_line": " | ".join(status_parts),
            "source_url": detail_url or PORTAL_URL,
        })

    logger.info("Fayette Purchasing: %d transportation solicitations parsed",
                len(records))
    return records


def fetch_and_parse() -> List[dict]:
    """Fetch and parse Fayette County purchasing bids page."""
    from ingestion.fetcher import fetch_static

    result = fetch_static(PORTAL_URL)
    if not result.html:
        logger.warning("Fayette Purchasing: empty response (error: %s)",
                       result.error)
        return []

    return parse_html(result.html)
