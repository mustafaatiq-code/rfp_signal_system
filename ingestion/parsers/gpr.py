"""
Adapter for Georgia Procurement Registry (GPR).

STATUS (validated 2026-06-20)
-----------------------------
GPR is at https://ssl.doas.state.ga.us/gpr/ (note: .state.ga.us, not .ga.gov).
The site resolves via Google DoH (IP 18.217.27.8) but returns HTTP 403 Forbidden
for all paths — indicating IP restriction (likely only accessible from Georgia
state-agency networks) or authentication requirement.

Important context discovered 2026-06-20:
  * GPR is now exclusively for LOCAL GOVERNMENT use (counties, cities, school
    boards, special purpose districts).
  * STATE AGENCY procurement moved to "GA@WORK" — a PeopleSoft eProcurement
    system at fscm.teamworks.georgia.gov. GDOT uses GA@WORK for purchases.
  * The old ssl.doas.ga.gov/gpr/ URL is dead (Acquia "Web Site Not Found").

Consequences for GMG:
  * GPR captures COUNTY and CITY transportation projects (local roads, traffic
    signals, drainage — work that GMG's CEI and Traffic Ops teams could pursue).
    Examples: county road-widening CEI, city traffic signal modernization.
  * GDOT (state agency) work does NOT go through GPR; it goes through the GDOT
    consultant portal (solicitation.dot.ga.gov) or SAM.gov (federal-aid projects).

This module degrades gracefully — returns [] + an informative log.

Permitted production paths for local-government GPR data:
  * Register as a supplier on GPR (free) and subscribe to email bid notices.
    GPR sends email alerts for new bids matching your selected categories.
  * Access GPR directly via a registered account from a non-restricted network.
  * GA@WORK (fscm.teamworks.georgia.gov): contact DOAS to check if a public
    solicitation search is available — this covers all state agency purchasing.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Georgia Procurement Registry (Local Gov)"
PORTAL_URL = "https://ssl.doas.state.ga.us/gpr/"
GAWORK_URL = "https://fscm.teamworks.georgia.gov/"
DOAS_PAGE = "https://doas.ga.gov/state-purchasing/supplier-registration-bid-notices"

_FORBIDDEN_MARKERS = ("403 forbidden", "403 - forbidden")


def fetch_and_parse(url: str = PORTAL_URL) -> List[dict]:
    """Attempt to fetch Georgia Procurement Registry solicitations.

    Returns [] in all current cases (403 Forbidden from the server), with a log
    that explains the two-tier GPR/GA@WORK structure and the permitted paths.
    """
    from ingestion.fetcher import fetch_static
    result = fetch_static(url, retries=1)

    if result.status_code == 200 and result.html:
        # Future-proofing: if GPR becomes publicly accessible, parse here
        logger.info("GPR responded with 200 — parsing not yet implemented. "
                    "Inspect %s and add parse_html() to this module.", url)
        return []

    is_403 = (
        result.status_code == 403
        or (result.error and "403" in result.error)
        or (result.html and any(m in result.html.lower() for m in _FORBIDDEN_MARKERS))
    )
    if is_403:
        logger.info(
            "GPR (ssl.doas.state.ga.us) returns 403 Forbidden — the portal "
            "is IP-restricted or requires a registered account. "
            "NOTE: GPR is for LOCAL GOVERNMENTS only (counties, cities, "
            "school boards); STATE agency procurement (incl. GDOT) moved to "
            "GA@WORK at %s. "
            "GMG path for local-gov bids: register as a GPR supplier at %s "
            "and subscribe to transportation category email alerts. "
            "GMG path for state-agency bids: use the GDOT consultant portal "
            "(solicitation.dot.ga.gov) or SAM.gov (via sam_gov.py).",
            GAWORK_URL, DOAS_PAGE,
        )
        return []

    logger.warning(
        "GPR fetch returned unexpected status %s: %s",
        result.status_code, result.error
    )
    return []
