"""
Adapter for GDOT's Professional Services Solicitation System.

STATUS (validated 2026-06-20)
-----------------------------
solicitation.dot.ga.gov requires Microsoft Identity (Azure AD) authentication —
GDOT's prequalified-consultant RFQ portal for CEI / design / planning / program
management work. Only firms that have completed GDOT's Engineering Consultant
Qualification process and been approved can log in and see solicitations.

The portal itself is reachable (DoH bypass for the .ga.gov DNSSEC issue is
handled by fetch_static automatically), but the root page is a login form, not
a public listing.

This module degrades gracefully:
  * fetch_and_parse() returns [] + a log explaining the auth requirement.
  * A health-check probe confirms the portal is up (useful for monitoring).

Permitted production paths for GMG:
  * Complete GDOT's prequalified-consultant registration — firms in the
    CEI / traffic-ops / planning / program-management space operating in
    Georgia should do this anyway.  Once approved, the portal is used
    interactively at solicitation.dot.ga.gov (login with Microsoft account).
  * sam_gov.py (this same pipeline): GDOT projects funded via FHWA federal-aid
    money are published as federal contract opportunities on SAM.gov and flow
    into the pipeline automatically through that adapter.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Georgia DOT"
PORTAL_URL = "https://solicitation.dot.ga.gov/"
CONSULTANT_PAGE = "https://www.dot.ga.gov/GDOT/pages/consultantservices.aspx"

_AUTH_MARKER = "microsoftidentity/account/signin"


def _portal_is_up() -> bool:
    """Returns True if the GDOT solicitation portal responds (even with login page)."""
    try:
        from ingestion.fetcher import fetch_static
        result = fetch_static(PORTAL_URL, retries=1)
        return result.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def fetch_and_parse(url: str = PORTAL_URL) -> List[dict]:
    """Attempt to fetch GDOT solicitations.

    The portal requires Microsoft Identity (Azure AD) authentication, so this
    will always return [] in an automated context.  It logs a clear explanation
    and a health-check status so the pipeline knows the source is structurally
    reachable (just gated), not broken.

    For federal-aid GDOT projects, see sam_gov.fetch_and_parse() — that source
    covers a significant subset of GDOT transportation solicitations automatically.
    """
    from ingestion.fetcher import fetch_static
    result = fetch_static(url, retries=1)

    if result.status_code != 200 or not result.html:
        logger.warning(
            "GDOT solicitation portal unreachable (status=%s): %s",
            result.status_code, result.error
        )
        return []

    # Confirm it is the login page (not a public listing)
    if _AUTH_MARKER in (result.html or "").lower():
        logger.info(
            "GDOT solicitation portal is UP but requires authentication "
            "(Microsoft Identity / GDOT consultant prequalification). "
            "Automated fetch returns no records. "
            "GMG path: register as a prequalified GDOT consultant at %s, "
            "then access the portal interactively at %s. "
            "Federal-aid GDOT opportunities are available via SAM.gov.",
            CONSULTANT_PAGE, PORTAL_URL,
        )
    else:
        logger.warning(
            "GDOT portal response was unexpected (auth marker not found). "
            "Page may have changed — inspect manually at %s", PORTAL_URL
        )
    return []
