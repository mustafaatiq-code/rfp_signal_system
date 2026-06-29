"""
Adapter for Georgia Procurement Registry (GPR).

https://ssl.doas.state.ga.us/gpr/index

GPR is a JavaScript SPA backed by a DataTables server-side AJAX endpoint
(/gpr/eventSearch). It requires a Chrome-like User-Agent to bypass its browser-
detection middleware (returns only a 5KB "unsupported browser" page otherwise).

Strategy:
  Fetch all OPEN bids in two categories that cover GMG services:
    - Construction / Public Works
    - Design Professional, General Consultant
  Then filter locally by transportation keywords (same as other county parsers).
  Deduplicates by esourceNumber across both category fetches.

No login required — the public search is fully open once the UA check passes.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://ssl.doas.state.ga.us/gpr"
SEARCH_URL = f"{BASE_URL}/eventSearch"
DETAIL_BASE = f"{BASE_URL}/eventDetails"

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# "Jul 09, 2026 @ 02:00 PM" → parse the date part only
_DATE_RE = re.compile(r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})")

_TRANSPORT_KEYWORDS = [
    "road", "street", "avenue", "boulevard", "highway", "corridor",
    "intersection", "sidewalk", "pedestrian", "trail", "multiuse",
    "bridge", "culvert", "drainage", "stormwater",
    "traffic", "signal", "traffic sign",
    "pavement", "resurfacing", "asphalt", "striping", "milling", "overlay",
    "transportation", "transit",
    "roundabout", "access management",
    "guardrail", "grading", "right-of-way",
    "safe streets", "ss4a", "mpo", "lmig",
]


def _is_transport(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _TRANSPORT_KEYWORDS)


def _parse_due_date(closing_str: str) -> Optional[date]:
    m = _DATE_RE.search(closing_str or "")
    if not m:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(m.group(1)).date()
    except Exception:
        return None


def _build_payload(cat_type: str, start: int = 0, length: int = 200) -> dict:
    cols = [
        ("electronicBid", ""),
        ("esourceNumber", "esourceNumber"),
        ("title", "title"),
        ("agencyName", "agencyName"),
        ("postingDateStr", "postingDateStr"),
        ("closingDateStr", "closingDateStr"),
        ("endingIn", ""),
        ("status", "status"),
    ]
    payload: dict = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "5",
        "order[0][dir]": "asc",
        "responseType": "ALL",
        "eventIdTitle": "",
        "eventStatus": "OPEN",
        "govType": "ALL",
        "govEntity": "",
        "catType": cat_type,
        "eventProcessType": "ALL",
        "dateRangeType": "",
        "rangeStartDate": "",
        "rangeEndDate": "",
        "isReset": "false",
        "persisted": "false",
        "refreshSearchData": "true",
    }
    for i, (data, name) in enumerate(cols):
        payload[f"columns[{i}][data]"] = data
        payload[f"columns[{i}][name]"] = name
        payload[f"columns[{i}][searchable]"] = "true"
        payload[f"columns[{i}][orderable]"] = "false" if i in (0, 6, 7) else "true"
        payload[f"columns[{i}][search][value]"] = ""
        payload[f"columns[{i}][search][regex]"] = "false"
    return payload


def _fetch_category(session, cat_type: str) -> List[dict]:
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE_URL}/index",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://ssl.doas.state.ga.us",
    }
    rows: List[dict] = []
    start = 0
    page_size = 200
    while True:
        payload = _build_payload(cat_type, start=start, length=page_size)
        try:
            r = session.post(SEARCH_URL, data=payload, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning("GPR fetch failed catType=%s start=%d: %s", cat_type, start, e)
            break
        page_rows = data.get("data", [])
        rows.extend(page_rows)
        total = data.get("recordsFiltered", 0)
        start += page_size
        if start >= total or not page_rows:
            break
    return rows


def fetch_and_parse() -> List[dict]:
    try:
        import requests
    except ImportError:
        logger.error("requests not installed")
        return []

    session = requests.Session()
    try:
        session.get(
            f"{BASE_URL}/index",
            headers={"User-Agent": _CHROME_UA, "Accept": "text/html"},
            timeout=15,
        )
    except Exception as e:
        logger.warning("GPR session warm-up failed: %s", e)
        return []

    cat_types = ["Construction_PublicWorks", "DesignProf_GeneralConsultant"]
    all_rows: List[dict] = []
    seen: set = set()
    for cat in cat_types:
        rows = _fetch_category(session, cat)
        logger.info("GPR catType=%s: %d raw rows", cat, len(rows))
        for row in rows:
            eid = row.get("esourceNumber", "")
            if not eid or eid in seen:
                seen.add(eid)
                continue
            seen.add(eid)
            all_rows.append(row)

    records: List[dict] = []
    for row in all_rows:
        title = (row.get("title") or "").strip()
        if not title or not _is_transport(title):
            continue

        eid = row.get("esourceNumber", "")
        eid_key = row.get("esourceNumberKey", eid)
        source_id = row.get("sourceId", "")
        agency = (row.get("agencyName") or "").strip()
        closing_str = row.get("closingDateStr", "")

        due_date = _parse_due_date(closing_str)
        year = due_date.year if due_date else date.today().year

        detail_url = (
            f"{DETAIL_BASE}?eSourceNumber={eid_key}&sourceSystemType={source_id}"
            if eid_key else SEARCH_URL
        )

        status = f"Due date: {due_date.isoformat()}" if due_date else f"Closing: {closing_str}"
        status += f" | Agency: {agency} | State: Georgia"

        records.append({
            "agency": agency or "Georgia Procurement Registry",
            "title": title,
            "solicitation_id": f"GPR-{eid}",
            "year": year,
            "bucket": "1 - Active RFP",
            "status_line": status,
            "source_url": detail_url,
        })

    logger.info("GPR: %d transport bids (from %d total fetched)", len(records), len(all_rows))
    return records
