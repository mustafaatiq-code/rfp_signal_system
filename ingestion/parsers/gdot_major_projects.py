"""
Adapter for GDOT's Major Projects page.

https://www.dot.ga.gov/GDOT/Pages/MajorProjects.aspx

Publicly accessible — no authentication required. Lists active major GDOT
projects (interchanges, widening, corridor improvements) with links to
individual ArcGIS Hub project pages. These are construction/design-phase
projects representing CEI, A&E, and program management opportunities.

Serves as an early-signal source: projects on this page are in active
delivery and may have upcoming CEI solicitations.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List

logger = logging.getLogger(__name__)

AGENCY = "Georgia DOT"
PORTAL_URL = "https://www.dot.ga.gov/GDOT/Pages/MajorProjects.aspx"

# ArcGIS Hub subdomains used by GDOT project pages
_HUB_RE = re.compile(r"https://[a-z0-9\-]+-gdot\.hub\.arcgis\.com/?", re.IGNORECASE)

# Lines that are page chrome, not project names
_SKIP_RE = re.compile(
    r"^(?:interchange projects?|improvement projects?|widening projects?|"
    r"bridge projects?|major projects?|home|contact|sign in|real.time|"
    r"toggle|skip|turn on|turn off|if ie|endif|currently selected|recent|"
    r"gdot|search|​|​)$",
    re.IGNORECASE,
)


def parse_html(html: str) -> List[dict]:
    """Parse the GDOT Major Projects page into signal records."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 not installed")
        return []

    soup = BeautifulSoup(html, "html.parser")
    records: List[dict] = []
    seen: set = set()

    # Each project is an <a> linking to a GDOT ArcGIS Hub subdomain
    for a in soup.find_all("a", href=_HUB_RE):
        title = a.get_text(strip=True)
        hub_url = a["href"].strip()

        if not title or len(title) < 5:
            continue
        if _SKIP_RE.match(title):
            continue
        if title in seen:
            continue
        seen.add(title)

        # Derive a slug-based solicitation ID from the hub subdomain
        slug_m = re.match(r"https://([a-z0-9\-]+)-gdot\.hub\.arcgis\.com", hub_url, re.IGNORECASE)
        slug = slug_m.group(1).upper() if slug_m else re.sub(r"\W+", "-", title.upper())[:40]
        sol_id = f"GDOT-MAJOR-{slug}"

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": sol_id,
            "year": date.today().year,
            "bucket": "2 - Predicted",
            "status_line": f"Active GDOT project — CEI solicitation status unverified | Hub: {hub_url} | State: Georgia",
            "source_url": hub_url,
        })

    logger.info("GDOT Major Projects: %d active projects found", len(records))
    return records


def fetch_and_parse() -> List[dict]:
    from ingestion.fetcher import fetch_static
    result = fetch_static(PORTAL_URL)
    if result.status_code != 200 or not result.html:
        logger.warning("GDOT Major Projects page unreachable (status=%s)", result.status_code)
        return []
    return parse_html(result.html)
