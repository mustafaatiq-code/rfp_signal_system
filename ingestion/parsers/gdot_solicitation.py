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
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

AGENCY = "Georgia DOT"
PORTAL_URL = "https://solicitation.dot.ga.gov/"
CONSULTANT_PAGE = "https://www.dot.ga.gov/GDOT/pages/consultantservices.aspx"

_AUTH_MARKER = "microsoftidentity/account/signin"

# If GMG gets prequalified-consultant access, a human runs tools/capture_gdot.py
# (interactive login) which saves the rendered listing HTML here. When this file
# exists, fetch_and_parse() parses it instead of returning []. The directory is
# gitignored — captures never enter version control.
BASE = Path(__file__).resolve().parents[2]
CAPTURE_FILE = BASE / "data" / "raw" / "gdot_capture" / "page.html"

# A date like 2026-09-30 or 09/30/2026 in a cell.
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def parse_html(html: str, base_url: str = PORTAL_URL) -> List[dict]:
    """Structure-based parser for the GDOT solicitation listing.

    Mirrors the henry_opengov approach: broad, defensive selectors so minor
    markup changes don't silently drop everything, exercised by a synthetic
    fixture in tests/ so the extraction logic is covered before real access
    exists. Returns [] when nothing matches.

    NOTE: the exact GDOT listing markup is behind login and has not been seen
    yet, so the row heuristics below (table rows: [id | linked title | due date])
    should be verified and tuned against the first real capture. The pipeline is
    unaffected until then — with no capture file, fetch_and_parse() returns [].
    """
    soup = BeautifulSoup(html or "", "html.parser")
    records: List[dict] = []
    seen: set = set()

    for row in soup.select("table tr"):
        # Skip header rows (all-<th>, no data cells).
        if not row.find("td"):
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        link = row.find("a", href=True)
        title = (link.get_text(strip=True) if link else "").strip()
        if not title:
            # Fall back to the longest non-numeric cell as the title.
            texts = [c.get_text(" ", strip=True) for c in cells]
            title = max((t for t in texts if not t.replace("-", "").isdigit()),
                        key=len, default="")
        if not title or len(title) < 4:
            continue

        row_text = row.get_text(" ", strip=True)
        due = _extract_due_date(row_text)
        sol_id = _extract_solicitation_id(cells, link, title)
        if sol_id in seen:
            continue
        seen.add(sol_id)

        source_url = urljoin(base_url, link["href"]) if link else base_url
        year = due.year if due else date.today().year
        status_line = f"Due date: {due.isoformat()}" if due else ""
        records.append({
            "agency": AGENCY,
            "title": title,
            "solicitation_id": sol_id,
            "year": year,
            "bucket": "1 - Active RFP",
            "status_line": status_line,
            "source_url": source_url,
        })

    if records:
        logger.info("GDOT capture parsed: %d solicitation(s)", len(records))
    return records


def _extract_due_date(text: str) -> "date | None":
    m = _ISO_DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _US_DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def _extract_solicitation_id(cells, link, title: str) -> str:
    """Best-effort stable ID: a solicitation-number-looking token, else a slug."""
    for c in cells:
        tok = c.get_text(strip=True)
        # e.g. "Q-12345", "RFQ 2026-07", "PI 0012345" — has a digit and is short
        if tok and any(ch.isdigit() for ch in tok) and len(tok) <= 24 and " " not in tok.strip():
            return f"GDOT-{tok}"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return f"GDOT-{slug}"


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
    returns [] in an automated context unless a local capture exists.

    If GMG has prequalified-consultant access, a human runs tools/capture_gdot.py
    (interactive login) to save the rendered listing to CAPTURE_FILE. When that
    file is present, we parse it and return real records; otherwise we fall back
    to the auth-gated health check and return []. So this source contributes
    nothing until access exists, and cannot affect the rest of the pipeline.
    """
    if CAPTURE_FILE.exists():
        try:
            html = CAPTURE_FILE.read_text(encoding="utf-8", errors="ignore")
            records = parse_html(html)
            if records:
                return records
            logger.warning(
                "GDOT capture at %s parsed 0 records — markup may differ from the "
                "parse_html heuristics; inspect and tune selectors.", CAPTURE_FILE
            )
        except Exception as exc:  # noqa: BLE001 - a bad capture must not break the run
            logger.warning("GDOT capture parse failed: %s", exc)
        return []

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
            "then access the portal interactively at %s.",
            CONSULTANT_PAGE, PORTAL_URL,
        )
    else:
        logger.warning(
            "GDOT portal response was unexpected (auth marker not found). "
            "Page may have changed — inspect manually at %s", PORTAL_URL
        )
    return []
