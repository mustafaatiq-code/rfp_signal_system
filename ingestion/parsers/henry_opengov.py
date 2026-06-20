"""
Parser + live integration for Henry County, GA bids hosted on OpenGov
(ProcureNow) — the JS-rendered "Bucket 1" portal the midterm deck flagged for
the Playwright fetch path.

STATUS (validated 2026-06-20)
-----------------------------
Henry's OpenGov portal (https://procurement.opengov.com/portal/henryga) is
served behind **Cloudflare Turnstile**. A headless browser is shown a "Just a
moment… performing security verification" interstitial and never reaches the
listing data; the backing API host returns the same challenge / 403. So **no
real Henry listing data is retrievable via automated scraping**, and we do not
attempt to defeat the bot wall (brittle and against OpenGov's terms).

Consequences for this module:
  * fetch_and_parse() drives fetch_dynamic() and degrades gracefully: if the
    page is an anti-bot wall (fetched_via="blocked") it logs the reason and
    returns [] instead of crashing the pipeline.
  * parse_html() is a STRUCTURE-BASED parser for OpenGov ProcureNow listing
    markup. It is exercised by a clearly-synthetic fixture in
    tests/test_pipeline.py (NOT real Henry data) so the extraction logic is
    covered, and it will work unchanged the day real markup is available
    through a permitted path.

Permitted production paths to actually get Henry's data (see README):
  * OpenGov's official API access (vendor registration / data agreement),
  * the portal's email / RSS bid-notification subscription,
  * an aggregator that already licenses the feed (BidNet Direct, GPR).
"""
from __future__ import annotations

import logging
import re
from typing import List

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

AGENCY = "Henry County, GA"
PORTAL_URL = "https://procurement.opengov.com/portal/henryga"

# OpenGov ProcureNow renders each open solicitation as a project row/card with a
# title link and a status badge. Selectors are intentionally broad so minor
# markup changes don't silently drop everything; if none match we return [].
_ROW_SELECTORS = ("[data-test='project-row']", ".project-row",
                  "[class*='ProjectRow']", "[class*='project-card']", "li[role='row']")
_TITLE_SELECTORS = ("a[href*='/projects/']", "[class*='title'] a", "h3 a", "a")
_STATUS_SELECTORS = ("[class*='status']", "[class*='badge']", "[data-test='status']")

_ID_RE = re.compile(r"\b([A-Za-z]{0,4}[-\s]?\d{2,}[-\d]*)\b")


def _classify(status_text: str) -> str:
    s = (status_text or "").lower()
    if "cancel" in s:
        return "Cancelled"
    if "award" in s or "closed" in s:
        return "Awarded"
    if "open" in s or "due" in s or "accepting" in s or "published" in s:
        return "1 - Active RFP"
    return "Unknown"


def parse_html(html: str) -> List[dict]:
    """Extract Henry/OpenGov solicitations from rendered portal HTML.

    Returns the same record shape as the Fulton parser
    (agency, source_url, year, solicitation_id, title, status_line, bucket).
    Returns [] when no listing markup is present (e.g. only an anti-bot or SPA
    shell was captured) — callers treat that as 'no records this run'."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 - lxml may be absent
        soup = BeautifulSoup(html, "html.parser")

    rows = []
    for sel in _ROW_SELECTORS:
        rows = soup.select(sel)
        if rows:
            break
    if not rows:
        return []

    records: List[dict] = []
    for row in rows:
        title_el = next((row.select_one(s) for s in _TITLE_SELECTORS
                         if row.select_one(s)), None)
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title:
            continue
        status_el = next((row.select_one(s) for s in _STATUS_SELECTORS
                          if row.select_one(s)), None)
        status_line = status_el.get_text(" ", strip=True) if status_el else ""
        id_match = _ID_RE.search(title)
        records.append({
            "agency": AGENCY,
            "source_url": PORTAL_URL,
            "year": None,  # OpenGov rows carry dates, not the Fulton year headers
            "solicitation_id": (id_match.group(1).strip() if id_match
                                else f"henryga-{len(records)}"),
            "title": title,
            "status_line": status_line,
            "bucket": _classify(status_line),
        })
    return records


def fetch_and_parse(url: str = PORTAL_URL) -> List[dict]:
    """Production path: render the OpenGov portal with Playwright and parse it.
    Degrades gracefully — returns [] (with a log line) when the source is
    bot-gated or otherwise unreachable, rather than failing the whole run."""
    from ingestion.fetcher import fetch_dynamic  # local import to avoid cycle

    result = fetch_dynamic(url, wait_selector="[href*='/projects/']", settle_ms=6000)
    if result.fetched_via == "blocked":
        logger.warning("Henry/OpenGov is bot-gated, skipping: %s", result.error)
        return []
    if not result.html:
        logger.warning("Henry/OpenGov fetch failed, skipping: %s", result.error)
        return []
    records = parse_html(result.html)
    if not records:
        logger.warning("Henry/OpenGov returned no parseable listings "
                       "(rendered shell only?).")
    return records


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_and_parse(), indent=2))
