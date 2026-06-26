"""
Adapter for MARTA (Metropolitan Atlanta Rapid Transit Authority) procurement portal.

Portal: https://martabid.marta.net — public, no authentication required.

Two pages are scraped:
  * CurrentOpportunities.aspx — active RFPs/IFBs open for response
  * AnticipatedProcurement.aspx — future procurement schedule (pipeline signals)

Page structure (validated 2026-06-27):
  * Static HTML + JS rendering via Playwright (settle_ms=5000)
  * Each active bid block: [Title] [Type] ([Abbr]) - [BidNum] ... Bid Submittal To: [date]
  * Anticipated table: [BidNum] [Title] [TBD|date] [RFP|IFB] [Department]

Relevance for GMG:
  MARTA is the primary transit authority for metro Atlanta. It regularly
  solicits transportation engineering services: CEI for station/track
  construction, A&E design services, traffic/pedestrian studies (Safe Routes
  to Transit), program management, and ITS/signal work. These map directly
  to GMG's CEI, A&E, Planning, Traffic Ops, and Program Mgmt service lines.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional

from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

AGENCY = "MARTA (Atlanta)"
CURRENT_URL = "https://martabid.marta.net/CurrentOpportunities.aspx"
ANTICIPATED_URL = "https://martabid.marta.net/AnticipatedProcurement.aspx"

# Phrase that marks the end of the nav/preamble on the current-opportunities page
_PREAMBLE_END = "NOTE: You must sign in to submit a response to any solicitation opportunity"

_TYPE_ABBR = {
    "request for proposal": "RFP",
    "invitation for bids": "IFB",
    "invitation for bid": "IFB",
    "invitation for quotes": "IFQ",
    "request for qualifications": "RFQ",
    "request for information": "RFI",
    "design build": "D-B",
}

# Include the parenthesised abbreviation "(RFP)", "(IFB/CPB)" etc. in the
# separator so we don't accidentally split on "Design Build Services" inside
# a bid title or description (which is not followed by a parenthetical).
_TYPE_SPLIT_RE = re.compile(
    r"((?:Request for (?:Proposal|Qualifications|Information)"
    r"|Invitation for (?:Bids?|Quotes?))"
    r"\s*\([A-Z/]+\))",           # e.g. "(RFP)" or "(IFB/CPB)"
    re.IGNORECASE,
)


def _clean(html: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        return dateparser.parse(raw.split()[0]).strftime("%Y-%m-%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Current Opportunities parser
# ---------------------------------------------------------------------------

def parse_current_html(html: str) -> List[dict]:
    """Parse CurrentOpportunities.aspx rendered HTML into record dicts."""
    text = _clean(html)

    # Locate the content section (after MARTA nav/header ends)
    preamble_idx = text.find(_PREAMBLE_END)
    if preamble_idx >= 0:
        content = text[preamble_idx + len(_PREAMBLE_END):]
    else:
        # Fallback: second occurrence of "Current Opportunities"
        idx = text.find("Current Opportunities")
        idx = text.find("Current Opportunities", idx + 50)
        content = text[idx:] if idx >= 0 else text

    # Split on bid-type declarations to get alternating [title_chunk, type, detail_chunk, ...]
    parts = _TYPE_SPLIT_RE.split(content)
    if len(parts) < 3:
        logger.info("MARTA CurrentOpportunities: no bid type declarations found — 0 bids")
        return []

    records: List[dict] = []
    for i in range(1, len(parts) - 1, 2):
        bid_type_text = parts[i].strip()
        detail_block = parts[i + 1] if i + 1 < len(parts) else ""

        # --- Title: last meaningful text in the preceding chunk ---
        prev = parts[i - 1]
        # After the first bid's "Project Contact: email", the next bid's title follows
        pc_match = re.search(r"Project Contact:\s*\S+\s*(.*)", prev, re.DOTALL)
        title_raw = pc_match.group(1).strip() if pc_match else prev.strip()
        # Strip leftover nav phrases
        title_raw = re.sub(
            r"^\s*(?:NOTE|IMPORTANT|Please|To download|Click)[^.]*\.\s*",
            "", title_raw, flags=re.IGNORECASE
        ).strip()
        title = title_raw[:200]

        # --- Bid number ---
        num_match = re.search(r"[-–]\s*([A-Z]+\s+[A-Z]?\d+)", detail_block[:120])
        bid_num = num_match.group(1).strip() if num_match else f"MARTA-{i // 2 + 1}"

        # --- Description ---
        desc_match = re.search(
            r"Description:\s*(.{20,500}?)(?=\s+Bid Submittal|\s+Conference|\s+Site Visit|\s+$)",
            detail_block, re.DOTALL,
        )
        description = desc_match.group(1).strip()[:300] if desc_match else ""

        # --- Due date (Bid Submittal To or Proposal/Quote Submittal To) ---
        due_match = re.search(r"Bid Submittal To:\s*([\d/]+)", detail_block)
        if not due_match:
            due_match = re.search(r"Proposal/Quote Submittal To:\s*([\d/]+)", detail_block)
        due_iso = _parse_date(due_match.group(1) if due_match else None)

        # Abbreviation is in the parenthetical of the separator, e.g. "(RFP)" or "(IFB/CPB)"
        abbr_match = re.search(r"\(([A-Z/]+)\)", bid_type_text)
        abbr = abbr_match.group(1).split("/")[0] if abbr_match else "RFP"
        status_parts = []
        if due_iso:
            status_parts.append(f"Due date: {due_iso}")
        status_parts += [f"{abbr} {bid_num}", "State: Georgia"]

        records.append({
            "agency": AGENCY,
            "title": (title or description[:120] or bid_num).strip(),
            "solicitation_id": bid_num.replace(" ", "-"),
            "year": date.today().year,
            "bucket": "1 - Active RFP",
            "status_line": " | ".join(status_parts),
            "source_url": CURRENT_URL,
        })

    return records


# ---------------------------------------------------------------------------
# Anticipated Procurement parser
# ---------------------------------------------------------------------------

def parse_anticipated_html(html: str) -> List[dict]:
    """Parse AnticipatedProcurement.aspx rendered HTML into record dicts."""
    text = _clean(html)

    marker = "Anticipated Procurements The table below"
    idx = text.find(marker)
    if idx < 0:
        logger.info("MARTA AnticipatedProcurement: table not found")
        return []

    # Skip past column headers
    col_header = "Department"
    header_end = text.find(col_header, idx)
    if header_end < 0:
        return []
    content = text[header_end + len(col_header):]

    # Remove tier headings like "Estimated Value Over $10M" or "Estimated Value between $1 - $5M".
    # After _clean() all whitespace is collapsed to single spaces (no \n), so we must match
    # the specific pattern rather than "everything to end of line".
    content = re.sub(
        r"Estimated Value\s+\S+\s+\$[\d.]+[KMBkmb]?"
        r"(?:\s*[-–]\s*\$[\d.]+[KMBkmb]?)?",
        " ", content, flags=re.IGNORECASE,
    )

    # Match table rows: [BidNum] [Title] [Date/TBD] [RFP|IFB|RFQ] [Department]
    ROW_RE = re.compile(
        r"([PB]\d{4,6}(?:-[\w]+)?)\s+"    # bid number e.g. P50723, B50679
        r"(.+?)\s+"                         # title (greedy-lazy)
        r"(TBD|\d{1,2}/\d{1,2}/\d{4})\s+" # anticipated date
        r"(RFP|IFB|RFQ|ITB|IFQ)\s+"        # type
        r"(.+?)(?=\s+[PB]\d{4}|\s*Our Mission|$)",
        re.DOTALL,
    )

    records: List[dict] = []
    for m in ROW_RE.finditer(content):
        bid_num = m.group(1).strip()
        title = m.group(2).strip().lstrip("-$0123456789M ").strip()[:200]
        ant_date = m.group(3).strip()
        bid_type = m.group(4).strip()
        department = m.group(5).strip()[:80]

        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": f"MARTA-ANT-{bid_num}",
            "year": date.today().year,
            "bucket": "2 - Predicted",
            "status_line": (
                f"Anticipated: {ant_date} | {bid_type} {bid_num} "
                f"| Dept: {department} | State: Georgia"
            ),
            "source_url": ANTICIPATED_URL,
        })

    return records


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_and_parse(
    current_url: str = CURRENT_URL,
    anticipated_url: str = ANTICIPATED_URL,
) -> List[dict]:
    """Fetch and parse MARTA current + anticipated procurement opportunities.

    Uses Playwright (JS rendering). No authentication required.
    Returns combined list: active RFPs/IFBs first, then anticipated pipeline.
    """
    from ingestion.fetcher import fetch_dynamic, looks_like_antibot

    records: List[dict] = []

    for label, url, parser in [
        ("current", current_url, parse_current_html),
        ("anticipated", anticipated_url, parse_anticipated_html),
    ]:
        try:
            result = fetch_dynamic(url, settle_ms=5000)
            html = result.html or ""
            if looks_like_antibot("", html[:3000]):
                logger.warning("MARTA %s page triggered anti-bot detection", label)
                continue
            if not html:
                logger.warning("MARTA %s page returned empty HTML", label)
                continue
            batch = parser(html)
            logger.info("MARTA %s: %d records parsed", label, len(batch))
            records.extend(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MARTA %s fetch failed: %s", label, exc)

    return records
