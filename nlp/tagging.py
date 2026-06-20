"""
Layer 2 (NLP & Parsing) — service-type filtering and signal-type tagging.

Per the midterm deck's tech stack, this layer is spaCy (NER) +
HuggingFace Transformers + service-type filtering + signal-type tagging.
Named-entity extraction (dates, money, org names) is genuinely well-served by
spaCy; service-type / signal-type classification on short procurement titles
is not — those vocabularies are narrow and domain-specific, so a maintained
keyword/rule layer is both more accurate and more explainable for the
proposals team than a generic NER model. This module implements that rule
layer and tries to use spaCy for entity extraction if the model is available,
falling back to regex if not (spaCy model downloads aren't guaranteed in
every deployment environment).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# Service lines called out in the midterm deck (from Gude's own proposals)
SERVICE_TYPE_KEYWORDS = {
    "CEI": ["cei", "construction engineering and inspection", "subsurface utility"],
    "Planning": ["planning", "needs assessment", "comprehensive transportation plan",
                 "feasibility study"],
    "Program Mgmt": ["program management", "program mgmt", "cip program",
                      "construction program"],
    "Traffic Ops": ["traffic ops", "traffic operations", "traffic study",
                     "signal", "itse", "intersection"],
    "A&E": ["architectural", "engineering services", "a&e", "design services",
            "renovation", "replacement", "classroom reconfiguration"],
}

SIGNAL_TYPE_KEYWORDS = {
    "SPLOST": ["splost", "tsplost", "esplost"],
    "Bond Issuance": ["bond", "bond referendum", "bonds sold"],
    "Capital Budget": ["capital budget", "capital plan", "cip"],
    "Political Meetings": ["board minutes", "council meeting", "commission meeting",
                            "board of education"],
    "State Budget Session": ["appropriations", "general assembly"],
    "Legislation": ["bill", "iija", "raise grant"],
    "Planning Study": ["needs assessment", "planning study"],
    "News / Press": ["press release", "news article"],
    "Active RFP": ["due date", "rfp", "ifb", "request for proposal",
                    "request for qualifications"],
}

MONEY_RE = re.compile(r"\$\s?[\d,.]+\s?(?:[MK]|million|thousand)?", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)

try:
    import spacy
    _NLP = spacy.load("en_core_web_sm")
except Exception:  # noqa: BLE001 - model/package may not be installed
    _NLP = None


@dataclass
class TaggedRecord:
    record: dict
    service_types: List[str] = field(default_factory=list)
    signal_types: List[str] = field(default_factory=list)
    money_mentions: List[str] = field(default_factory=list)
    date_mentions: List[str] = field(default_factory=list)
    entities: List[dict] = field(default_factory=list)


def _match_keywords(text: str, vocab: dict) -> List[str]:
    text_l = text.lower()
    return [label for label, kws in vocab.items()
            if any(kw in text_l for kw in kws)]


def extract_entities(text: str) -> List[dict]:
    if _NLP is not None:
        doc = _NLP(text)
        return [{"text": ent.text, "label": ent.label_} for ent in doc.ents]
    # Regex fallback: dates and money only (no general NER without spaCy model)
    ents = [{"text": m.group(0), "label": "DATE"} for m in DATE_RE.finditer(text)]
    ents += [{"text": m.group(0), "label": "MONEY"} for m in MONEY_RE.finditer(text)]
    return ents


def tag_record(record: dict) -> TaggedRecord:
    """Tag a single ingestion record (e.g. one Solicitation dict) with
    service types and signal types based on its title + status fields."""
    blob = " ".join(str(record.get(f, "")) for f in
                     ("title", "status_line", "agency", "bucket"))
    return TaggedRecord(
        record=record,
        service_types=_match_keywords(blob, SERVICE_TYPE_KEYWORDS),
        signal_types=_match_keywords(blob, SIGNAL_TYPE_KEYWORDS),
        money_mentions=MONEY_RE.findall(blob),
        date_mentions=DATE_RE.findall(blob),
        entities=extract_entities(blob),
    )


def tag_records(records: List[dict]) -> List[TaggedRecord]:
    return [tag_record(r) for r in records]
