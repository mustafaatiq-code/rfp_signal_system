"""
SAM.gov Contract Opportunities adapter (Layer 1 — Ingestion).

Provides federal transportation contract opportunities in Georgia and Florida.
Federal-aid GDOT and FDOT contracts that go through federal procurement (FHWA
direct, Army Corps, etc.) appear here automatically.

API: https://api.sam.gov/opportunities/v2/search  (official, public REST)
Key: register free at https://sam.gov/profile/details
     Free tier: 10 requests/day.  Set env var SAM_GOV_API_KEY.

If no key is set, fetch_and_parse() returns [] with an actionable log line.

Notes on the SAM.gov v2 API behaviour (validated 2026-06-27):
  * The `naicsCode` query param is silently ignored by the server — post-filter
    in Python instead. Records with NO naicsCode are also excluded (strict).
  * The `placeOfPerformanceState` query param is ALSO silently ignored — the
    server returns results from all states/countries regardless. Post-filter
    by placeOfPerformance.state.code (nested JSON) in Python instead.
    Records with no state code are kept (may be nationwide/multi-state).
    Records with a non-US country code are excluded.
  * Rate limiting: ~5 req/min on the free tier — a 13 s sleep between calls
    keeps us well inside it.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, timedelta
from typing import List, Optional, Set

import requests

logger = logging.getLogger(__name__)

AGENCY = "SAM.gov (Federal)"
API_URL = "https://api.sam.gov/opportunities/v2/search"

# NAICS codes for GMG's service lines.
# SAM.gov API ignores naicsCode as a filter param, so we post-filter here.
TARGET_NAICS: Set[str] = {
    "541330",  # Engineering Services — CEI, traffic, design, A&E
    "541380",  # Testing Laboratories — materials, inspection
    "541614",  # Process/Logistics Consulting — traffic operations, planning
    "541618",  # Other Management Consulting — program management
    "541690",  # Other Scientific/Technical Consulting — transportation studies
    "237310",  # Highway, Street, and Bridge Construction — large CEI-eligible projects
    "237130",  # Power & Communication Line Construction — ITS infrastructure
}

# Keyword queries for SAM.gov federal opportunities.
# NOTE: SAM.gov v2 silently ignores naicsCode, placeOfPerformanceState, AND
# appears to treat keyword as a broad full-text match. Most GA/FL transportation
# contracts live on state portals (GDOT/FDOT/GPR), NOT on SAM.gov. SAM.gov is
# most useful for contracts funded directly by FHWA, Army Corps, or Amtrak.
# 2 keywords × 2 states = 4 calls/run (well inside 10/day free limit).
TRANSPORT_KEYWORDS = [
    "highway bridge inspection",        # FHWA / Army Corps direct bridge work
    "A-E transportation services",      # Architect-Engineer contracts (federal format)
]

TARGET_STATES = ["GA", "FL"]

# Seconds to sleep between API calls to avoid 429 (free tier: ~5 req/min).
_INTER_CALL_SLEEP = 13

# SAM.gov baseType -> pipeline bucket
_TYPE_BUCKET = {
    "o": "1 - Active RFP",   # Solicitation (RFP / RFQ / IFB)
    "p": "2 - Predicted",    # Pre-solicitation
    "s": "1 - Active RFP",   # Special notice
    "r": "2 - Predicted",    # Sources Sought / RFI
    "a": "Awarded",
    "u": "Cancelled",
    "i": "2 - Predicted",
    "j": "2 - Predicted",
    "k": "2 - Predicted",
}


def _bucket(opp: dict) -> str:
    active = (opp.get("active") or "").lower()
    raw = (opp.get("baseType") or opp.get("type") or "o").lower()
    t = raw[0] if raw else "o"
    b = _TYPE_BUCKET.get(t, "1 - Active RFP")
    if active == "no" and b == "1 - Active RFP":
        b = "Awarded"
    return b


def _year_from(opp: dict) -> int:
    for field in ("postedDate", "responseDeadLine", "archiveDate"):
        val = opp.get(field) or ""
        m = re.search(r"(\d{4})", val)
        if m:
            return int(m.group(1))
    return date.today().year


def _status_line(opp: dict) -> str:
    deadline = (opp.get("responseDeadLine") or "")[:10]
    posted = (opp.get("postedDate") or "")[:10]
    naics = opp.get("naicsCode") or ""
    # State name for geography gate (engine checks status_line for state names)
    perf = opp.get("placeOfPerformance") or {}
    state_name = (perf.get("state") or {}).get("name") or ""
    parts = []
    if deadline:
        parts.append(f"Due date: {deadline}")
    if posted:
        parts.append(f"Posted: {posted}")
    if naics:
        parts.append(f"NAICS: {naics}")
    if state_name:
        parts.append(f"State: {state_name}")
    return " | ".join(parts)


def _parse_response(data: dict,
                    naics_whitelist: Optional[Set[str]] = None,
                    state_whitelist: Optional[Set[str]] = None) -> List[dict]:
    """Convert SAM.gov v2 response into standard record dicts.

    naics_whitelist: exclude records whose naicsCode is absent OR not in the set
    (server ignores the naicsCode query param, so this is the real filter).
    state_whitelist: exclude records with a non-matching state code OR a
    non-US country code (server ignores placeOfPerformanceState query param).
    Records with no state code at all are kept (may be nationwide contracts).
    """
    records = []
    for opp in (data.get("opportunitiesData") or []):
        title = (opp.get("title") or "").strip()
        if not title:
            continue

        # NAICS strict whitelist — records with no NAICS code are also excluded.
        naics = (opp.get("naicsCode") or "").strip()
        if naics_whitelist and naics not in naics_whitelist:
            continue

        # State/country post-filter (server ignores placeOfPerformanceState param).
        if state_whitelist:
            perf = opp.get("placeOfPerformance") or {}
            state_obj = perf.get("state") or {}
            country_obj = perf.get("country") or {}
            state_code = (state_obj.get("code") or "").upper().strip()
            country_code = (country_obj.get("code") or "USA").upper().strip()
            # Drop overseas records entirely.
            if country_code and country_code not in ("USA", "US", ""):
                continue
            # Drop records for wrong US states (blank = nationwide, keep it).
            if state_code and state_code not in state_whitelist:
                continue

        notice_id = (opp.get("noticeId") or
                     opp.get("solicitationNumber") or
                     f"sam-{len(records)}")
        org = (opp.get("organizationName") or
               (opp.get("fullParentPathName") or "").split("|")[-1]).strip()
        ui_link = (opp.get("uiLink") or
                   f"https://sam.gov/opp/{notice_id}/view")
        records.append({
            "agency": org or AGENCY,
            "source_url": ui_link,
            "year": _year_from(opp),
            "solicitation_id": notice_id,
            "title": title,
            "status_line": _status_line(opp),
            "bucket": _bucket(opp),
        })
    return records


def _query_one(api_key: str, keyword: str, state: str,
               days_back: int, call_index: int = 0) -> List[dict]:
    if call_index > 0:
        time.sleep(_INTER_CALL_SLEEP)   # stay inside the per-minute rate limit

    fmt = "%m/%d/%Y"
    params = {
        "api_key": api_key,
        "keyword": keyword,
        "postedFrom": (date.today() - timedelta(days=days_back)).strftime(fmt),
        "postedTo": date.today().strftime(fmt),
        "placeOfPerformanceState": state,
        "active": "true",
        "limit": 250,
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = _parse_response(data, naics_whitelist=TARGET_NAICS,
                                state_whitelist={state})
        total = data.get("totalRecords", "?")
        logger.info("SAM.gov: keyword=%r state=%s -> %d/%s after NAICS+state filter",
                    keyword[:50], state, len(batch), total)
        return batch
    except Exception as exc:  # noqa: BLE001
        logger.warning("SAM.gov fetch error (keyword=%r state=%s): %s",
                       keyword[:50], state, exc)
        return []


def fetch_and_parse(api_key: Optional[str] = None,
                    states: Optional[List[str]] = None,
                    days_back: int = 90) -> List[dict]:
    """Fetch federal transportation opportunities from SAM.gov.

    Set SAM_GOV_API_KEY in the environment, or pass api_key directly.
    Free tier: 10 req/day — 2 keywords × 2 states = 4 req/run, with 13 s
    pauses to stay inside the per-minute rate limit.

    NAICS post-filter keeps only Engineering Services / Transportation NAICS
    codes (server-side naicsCode param is silently ignored by the API).

    Returns [] with a log line if no API key is available.
    """
    key = api_key or os.environ.get("SAM_GOV_API_KEY", "").strip()
    if not key:
        logger.warning(
            "SAM.gov: SAM_GOV_API_KEY not set — skipping. "
            "Register free at https://sam.gov/profile/details "
            "(free tier = 10 req/day, enough for daily pipeline runs)."
        )
        return []

    target = states or TARGET_STATES
    seen: set = set()
    records: List[dict] = []
    call_idx = 0

    for kw in TRANSPORT_KEYWORDS:
        for st in target:
            for rec in _query_one(key, kw, st, days_back, call_index=call_idx):
                sid = rec["solicitation_id"]
                if sid not in seen:
                    seen.add(sid)
                    records.append(rec)
            call_idx += 1

    logger.info("SAM.gov: %d unique records after dedup (keywords=%d states=%s)",
                len(records), len(TRANSPORT_KEYWORDS), target)
    return records


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO)
    result = fetch_and_parse()
    if not result:
        print("No records — set SAM_GOV_API_KEY in the environment.")
        sys.exit(0)
    print(json.dumps(result, indent=2))
