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
    "CEI": [
        "cei", "construction engineering and inspection",
        "construction engineering inspection",   # without "and" (SAM.gov style)
        "construction inspection", "resident engineer",
        "field inspection", "inspection services", "subsurface utility",
        "sidewalk", "pedestrian improvement", "pedestrian project",
        "roadway improvement", "road improvement", "street improvement",
        "intersection improvement", "pavement", "resurfacing",
    ],
    "Planning": [
        "planning", "needs assessment", "feasibility study",
        "comprehensive transportation plan", "transportation planning",
        "corridor study", "environmental impact", "environmental assessment",
        "transportation study", "mobility study",
    ],
    "Program Mgmt": [
        "program management", "program mgmt", "cip program",
        "construction program", "program support", "program manager",
        "project management support",
    ],
    "Traffic Ops": [
        "traffic ops", "traffic operations", "traffic study",
        "signal", "itse", "intersection", "traffic control",
        "intelligent transportation", "its ", "congestion management",
        "work zone", "incident management", "tmc", "traffic management",
        "pedestrian signal", "crosswalk", "roundabout", "access management",
        "safety improvement", "corridor safety",
    ],
    "A&E": [
        "architectural", "engineering services", "a&e", "design services",
        "renovation", "replacement", "highway design", "roadway design",
        "transportation engineering", "bridge design", "drainage design",
        "corridor design", "geotechnical", "survey services", "surveying",
        "structures design", "pavement design", "right-of-way",
        "transit center", "design documents", "30% design", "60% design",
        "90% design", "preliminary design", "final design",
        "classroom reconfiguration",  # kept for Fulton-style test fixture
    ],
}

SIGNAL_TYPE_KEYWORDS = {
    "SPLOST": [
        "splost", "tsplost", "esplost",
        "special purpose local option sales tax",
        "transportation special purpose",
    ],
    "Bond Issuance": [
        "bond", "bond referendum", "bonds sold",
        "revenue bond", "general obligation bond", "bond issuance",
        "bond proceeds", "bond sale",
    ],
    "Capital Budget": [
        "capital budget", "capital plan", "cip",
        "capital improvement program", "capital improvement plan",
        "capital project", "capital outlay",
    ],
    "Political Meetings": [
        "board minutes", "council meeting", "commission meeting",
        "board of education", "school board meeting", "school board",
        "chamber meeting", "chamber of commerce",
        "dot board", "gdot board", "fdot board", "dot district meeting",
        "county commission", "city council", "board of commissioners",
        "planning commission", "zoning board",
    ],
    "State Budget Session": [
        "appropriations", "general assembly",
        "state budget", "budget session", "house budget", "senate budget",
        "federal appropriation", "stip", "tip amendment",
    ],
    "Legislation": [
        "bill", "iija", "raise grant", "raise act",
        "house bill", "senate bill", "act signed", "public law",
        "infrastructure investment", "bipartisan infrastructure",
    ],
    "Planning Study": [
        "needs assessment", "planning study", "corridor study",
        "feasibility study", "master plan", "long range plan",
        "transportation improvement program", "lrtp",
        "environmental study", "eis ", "ea ", "environmental assessment",
    ],
    "News / Press": [
        "press release", "news article", "announced", "proposed",
        "planned", "awarded contract", "breaking ground",
    ],
    "Active RFP": [
        "due date", "rfp", "ifb", "rfi", "rfq",
        "request for proposal", "request for qualifications",
        "request for information", "invitation for bids",
        "solicitation", "bid opening",
    ],
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
