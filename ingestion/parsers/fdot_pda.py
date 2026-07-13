"""
Adapter for FDOT's Procurement Development Application (PDA).

STATUS (validated 2026-06-20)
-----------------------------
pdaexternal.fdot.gov requires authentication. The AngularJS app ("PSIPDA")
redirects to /Error/Forbidden immediately on page load when no session is present,
so headless Playwright captures only the shell error page — no listing data.

The backing REST API (pdaextapi.fdot.gov/api/AdvertisementPublic/...) is on
Azure App Service and returns HTTP 404 "No action found on controller" for all
tested paths — the correct action routes are unknown from the outside.

Consequences for this module:
  * fetch_and_parse() degrades gracefully: returns [] + a log explaining the
    auth path, like gdot_solicitation.py does for the GDOT portal.
  * The FDOT Professional Services Inquiry page (www.fdot.gov) is behind
    Cloudflare bot-protection (403) so cannot be scraped.

Permitted production paths for GMG:
  * Register as a vendor on FDOT's Vendor Management system and log in to the
    PDA at pdaexternal.fdot.gov to access consultant solicitations.
  * FDOT District offices sometimes send solicitation notices via email lists.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Florida DOT"
PORTAL_URL = "https://pdaexternal.fdot.gov/"
VENDOR_PAGE = "https://fdotwp1.dot.state.fl.us/VendorRegistration/"

# The AngularJS app renders "Procurement Development Application - Page Error"
# (title) when the session check fails. Also look for /Error/Forbidden in HTML.
_AUTH_MARKERS = ("error/forbidden", "page error", "forbidden")


def fetch_and_parse(url: str = PORTAL_URL) -> List[dict]:
    """Attempt to fetch FDOT PDA solicitations.

    The portal requires FDOT vendor authentication (session cookie). The
    AngularJS app immediately redirects to /Error/Forbidden when no session is
    present, rendering a "Page Error" page.
    """
    try:
        from ingestion.fetcher import fetch_dynamic
        result = fetch_dynamic(url, settle_ms=4000)
        html_lower = (result.html or "").lower()
        is_auth_gated = any(m in html_lower for m in _AUTH_MARKERS)
        if is_auth_gated or result.fetched_via == "blocked":
            logger.info(
                "FDOT PDA portal is UP but requires FDOT vendor authentication "
                "(app shows Page Error / Forbidden without a valid session). "
                "Automated fetch returns no records. "
                "GMG path: register as a vendor at %s, log in to %s.",
                VENDOR_PAGE, PORTAL_URL,
            )
        else:
            logger.warning(
                "FDOT PDA fetch returned unexpected content — "
                "portal may have changed. Inspect manually at %s", PORTAL_URL
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("FDOT PDA fetch failed: %s", exc)
    return []
