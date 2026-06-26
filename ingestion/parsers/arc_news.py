"""
Adapter for Atlanta Regional Commission (ARC) Transportation & Mobility news feed.

ARC is the MPO (Metropolitan Planning Organization) for the 10-county Atlanta
region. Its news feed contains early-signal articles that precede transportation
RFPs by 6-24 months:

  * TIP amendments  → project has federal funding → RFP in 6-18 months
  * LRTP/MTP study  → future pipeline being set → RFP in 12-24 months
  * Corridor study   → needs assessment phase → RFP in 12-24 months
  * Grant award      → funding secured → RFP in 3-12 months
  * SPLOST / TSPLOST → local funding mechanism → RFP in 12-18 months

Feed: https://atlantaregional.org/news/transportation-mobility/feed/
Type: WordPress RSS 2.0 — no authentication required, returns clean XML.
Coverage: Metro Atlanta (Fulton, DeKalb, Gwinnett, Cobb, Clayton, Cherokee,
          Fayette, Henry, Rockdale, Douglas) — GMG's primary service area.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import List, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

AGENCY = "ARC (Atlanta Regional Commission)"
FEED_URL = "https://atlantaregional.org/news/transportation-mobility/feed/"

# Articles must contain at least one of these keywords (title OR full article body)
# to be included. Keywords are deliberately specific to avoid general-interest
# articles (staff announcements, commuter surveys, bike events, tech conferences).
_INCLUDE_KEYWORDS = [
    # Federal funding program signals (TIP amendments = projects getting $$)
    "transportation improvement program", " tip ", "stip",
    "metropolitan transportation plan", "lrtp", "mtp", "mobility 20",
    "tip amendment", "mtp amendment", "lrtp amendment",
    # Planning study signals (needs assessment → design → CEI)
    "corridor study", "corridor plan", "corridor project",
    "needs assessment", "feasibility study",
    "planning study", "long range plan", "long-range plan",
    "environmental study", "environmental impact statement",
    # Local funding signals
    "splost", "tsplost", "esplost",
    "bond referendum", "bond issue", "bond proceeds",
    # Grant/appropriation signals
    "federal grant", "safe streets", "raise grant", "raise act",
    "iija", "bipartisan infrastructure",
    "infrastructure investment and jobs",
    # Direct procurement signals
    "rfp", "solicitation", "procurement", "contract award",
    "request for proposal", "request for qualifications",
]

# High-value title signals → bump bucket classification
_HIGH_SIGNAL_PATTERNS = [
    re.compile(r"\bT[IS]P\b", re.IGNORECASE),          # TIP, STIP
    re.compile(r"transportation improvement program", re.IGNORECASE),
    re.compile(r"adopt[se]?\s+amendment", re.IGNORECASE),
    re.compile(r"corridor\s+(?:study|plan|project)", re.IGNORECASE),
    re.compile(r"\b(?:S|E|T)SPLOST\b", re.IGNORECASE),
    re.compile(r"(?:lrtp|mtp|mobility\s+2\d{3})", re.IGNORECASE),
    re.compile(r"award(?:ed)?\s+\$[\d.,]+", re.IGNORECASE),
    re.compile(r"federal\s+grant", re.IGNORECASE),
]


def _strip_html(text: str) -> str:
    s = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", s).strip()


def _has_procurement_signal(title: str, description: str) -> bool:
    combined = (title + " " + description).lower()
    return any(kw in combined for kw in _INCLUDE_KEYWORDS)


def _signal_label(title: str, description: str) -> str:
    combined = title + " " + description
    tl = combined.lower()
    if any(p.search(combined) for p in _HIGH_SIGNAL_PATTERNS):
        if "splost" in tl or "tsplost" in tl or "esplost" in tl:
            return "SPLOST"
        if "tip" in tl or "transportation improvement" in tl or "amendment" in tl:
            return "State Budget Session"
        if "grant" in tl or "funding" in tl or "award" in tl:
            return "Legislation"
        if "corridor" in tl or "lrtp" in tl or "mtp" in tl or "study" in tl:
            return "Planning Study"
    if "bond" in tl:
        return "Bond Issuance"
    if "capital improvement" in tl or "capital budget" in tl or " cip " in tl:
        return "Capital Budget"
    if "meeting" in tl or "commission" in tl or "committee" in tl:
        return "Political Meetings"
    return "News / Press"


def parse_rss(xml_text: str, days_back: int = 365) -> List[dict]:
    """Parse WordPress RSS 2.0 feed XML into record dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("ARC RSS: XML parse error: %s", exc)
        return []

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    cutoff = date.today() - timedelta(days=days_back)
    records: List[dict] = []

    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        title = _strip_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()
        # Use full article body (content:encoded) if available, fall back to description
        full_body = item.find("content:encoded", ns)
        desc_raw = (full_body.text if full_body is not None else None) or item.findtext("description") or ""
        description = _strip_html(desc_raw)

        # Parse date
        try:
            from dateutil import parser as dp
            pub_date = dp.parse(pub_date_raw).date()
        except Exception:
            continue

        if pub_date < cutoff:
            continue

        if not _has_procurement_signal(title, description):
            continue

        signal = _signal_label(title, description)
        excerpt = description[:200].rstrip() + ("…" if len(description) > 200 else "")

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": f"ARC-{pub_date.isoformat()}-{len(records)}",
            "year": pub_date.year,
            "bucket": "2 - Predicted",
            "status_line": (
                f"Published: {pub_date.isoformat()} | Signal: {signal} | "
                f"State: Georgia | {excerpt}"
            ),
            "source_url": link or FEED_URL,
        })

    logger.info("ARC news: %d transportation-signal articles (last %d days)", len(records), days_back)
    return records


def fetch_and_parse(
    feed_url: str = FEED_URL,
    days_back: int = 90,
) -> List[dict]:
    """Fetch and parse ARC Transportation & Mobility RSS feed.

    No authentication required. Returns articles from the last `days_back` days
    that contain transportation procurement signals (TIP amendments, corridor
    studies, SPLOST votes, grant awards, etc.).
    """
    from ingestion.fetcher import fetch_static

    result = fetch_static(feed_url)
    if not result.html:
        logger.warning("ARC news: empty response from %s (error: %s)", feed_url, result.error)
        return []

    return parse_rss(result.html, days_back=days_back)
