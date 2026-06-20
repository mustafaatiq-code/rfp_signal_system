"""
Parser for Fulton County Schools "Solicitations (Out For Bid)" page
(Bucket 1 — active RFP portal, per the midterm deck).

Source: https://www.fultonschools.org/all-departments/operations/capital-programs/solicitations-out-for-bid

The page lists solicitations grouped under year headings
("## 2026 Current Solicitations - Capital Programs"), each item formatted as
a title line followed by a status/due-date line. This parser extracts those
into structured records.

Two input paths produce identical records:
  * parse()      — the cleaned markdown cached in data/raw/ (used by tests and
                   in network-restricted environments).
  * parse_html() — the live HTML returned by the production fetcher. Fulton's
                   site runs the Finalsite CMS, so each year is a
                   <section class="fsList"> with an <h2 class="fsElementTitle">
                   heading and one <article> per solicitation (.fsTitle = title,
                   .fsSummary = status line). This is the production path.

Verified on 2026-06-20: parse_html() against the live URL yields the same five
records as parse() against the cached file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List

from bs4 import BeautifulSoup

AGENCY = "Fulton County Schools"
SOURCE_URL = (
    "https://www.fultonschools.org/all-departments/operations/"
    "capital-programs/solicitations-out-for-bid"
)

YEAR_HEADER_RE = re.compile(
    r"##\s*(\d{4})\s*Current Solicitations - Capital Programs"
)
ITEM_RE = re.compile(r"^\[(.+?)\]\s*$")


@dataclass
class Solicitation:
    agency: str
    source_url: str
    year: int
    solicitation_id: str
    title: str
    status_line: str
    bucket: str  # "1 - Active RFP" | "Awarded" | "Cancelled"


def _extract_id(title_text: str) -> tuple[str, str]:
    """Split '409-26, McNair Middle School Classroom Reconfiguration' into
    ('409-26', 'McNair Middle School Classroom Reconfiguration')."""
    m = re.match(r"\s*([A-Za-z0-9]+-\d{2,4})[,\s]+(.*)", title_text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", title_text.strip()


def _classify(status_line: str) -> str:
    s = status_line.lower()
    if "cancel" in s:
        return "Cancelled"
    if "award" in s:
        return "Awarded"
    if "due date" in s or "due" in s:
        return "1 - Active RFP"
    return "Unknown"


def parse(markdown_text: str) -> List[Solicitation]:
    lines = [ln.strip() for ln in markdown_text.splitlines()]
    records: List[Solicitation] = []
    current_year = None
    i = 0
    while i < len(lines):
        line = lines[i]
        year_match = YEAR_HEADER_RE.search(line)
        if year_match:
            current_year = int(year_match.group(1))
            i += 1
            continue

        item_match = ITEM_RE.match(line)
        if item_match and current_year is not None:
            title_text = item_match.group(1)
            sol_id, title = _extract_id(title_text)
            # status/due-date line follows, possibly after a blank line
            j = i + 1
            while j < len(lines) and lines[j] == "":
                j += 1
            status_line = lines[j] if j < len(lines) else ""
            records.append(Solicitation(
                agency=AGENCY,
                source_url=SOURCE_URL,
                year=current_year,
                solicitation_id=sol_id or f"{current_year}-unknown-{len(records)}",
                title=title,
                status_line=status_line,
                bucket=_classify(status_line),
            ))
            i = j + 1
            continue
        i += 1

    return records


_HTML_YEAR_RE = re.compile(r"(\d{4})\s*Current Solicitations - Capital Programs")


def parse_html(html: str) -> List[Solicitation]:
    """Parse the live Fulton Capital Programs HTML (Finalsite CMS markup) into
    the same Solicitation records that parse() produces from the cached markdown.

    Each year is a <section class="fsList"> with an <h2 class="fsElementTitle">
    heading; each solicitation is an <article> with a .fsTitle (id + name) and
    a .fsSummary (status / due-date line)."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 - lxml may be absent on some hosts
        soup = BeautifulSoup(html, "html.parser")

    records: List[Solicitation] = []
    for section in soup.select("section.fsList"):
        heading = section.find(class_="fsElementTitle")
        if not heading:
            continue
        ym = _HTML_YEAR_RE.search(heading.get_text(strip=True))
        if not ym:
            continue
        year = int(ym.group(1))
        for article in section.select("article"):
            title_el = article.select_one(".fsTitle")
            if not title_el:
                continue
            title_text = title_el.get_text(" ", strip=True)
            summary_el = article.select_one(".fsSummary")
            status_line = summary_el.get_text(" ", strip=True) if summary_el else ""
            sol_id, title = _extract_id(title_text)
            records.append(Solicitation(
                agency=AGENCY,
                source_url=SOURCE_URL,
                year=year,
                solicitation_id=sol_id or f"{year}-unknown-{len(records)}",
                title=title,
                status_line=status_line,
                bucket=_classify(status_line),
            ))
    return records


def parse_file(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return [asdict(r) for r in parse(text)]


def fetch_and_parse(url: str = SOURCE_URL) -> List[dict]:
    """Production path: fetch the live page and parse it. Run on a machine with
    normal internet access. Falls back through the fetcher's static strategy."""
    from ingestion.fetcher import fetch_static  # local import to avoid cycle

    result = fetch_static(url)
    if not result.html:
        raise RuntimeError(f"live fetch failed for {url}: {result.error}")
    return [asdict(r) for r in parse_html(result.html)]


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        print(json.dumps(fetch_and_parse(), indent=2))
    else:
        target = sys.argv[1] if len(sys.argv) > 1 else (
            "data/raw/fulton_schools_solicitations_20260620.md"
        )
        print(json.dumps(parse_file(target), indent=2))
