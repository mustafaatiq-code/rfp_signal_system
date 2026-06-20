"""
Parser for Fulton County Schools "Solicitations (Out For Bid)" page
(Bucket 1 — active RFP portal, per the midterm deck).

Source: https://www.fultonschools.org/all-departments/operations/capital-programs/solicitations-out-for-bid

The page lists solicitations grouped under year headings
("## 2026 Current Solicitations - Capital Programs"), each item formatted as
a title line followed by a status/due-date line. This parser extracts those
into structured records.

Tested against a real, verbatim copy of the live page saved at
data/raw/fulton_schools_solicitations_20260620.md (fetched 2026-06-20).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List

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


def parse_file(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return [asdict(r) for r in parse(text)]


if __name__ == "__main__":
    import json
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else (
        "data/raw/fulton_schools_solicitations_20260620.md"
    )
    print(json.dumps(parse_file(target), indent=2))
