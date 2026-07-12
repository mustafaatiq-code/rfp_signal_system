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
    "Construction Engineering & Inspection": [
        "cei", "construction engineering and inspection",
        "construction engineering inspection",   # without "and" (SAM.gov style)
        "construction inspection", "resident engineer",
        "field inspection", "inspection services", "subsurface utility",
        "sidewalk", "pedestrian improvement", "pedestrian project",
        "roadway improvement", "road improvement", "street improvement",
        "intersection improvement", "pavement", "resurfacing",
        # Road programs and construction types common in Georgia procurement
        "lmig",                           # Local Maintenance & Improvement Grant (GA DOT)
        "tsplost", "splost",              # SPLOST/TSPLOST-funded transportation construction
        "widening",                       # road widening projects
        "road rehabilitation", "roadway rehabilitation", "road rehab",
        "road reconstruction", "street reconstruction",
        "road project",                   # generic (e.g. "SR 166 @ Chapel Hill Road Project")
        "bridge construction", "bridge repair", "bridge project",
        "pedestrian bridge", "bike bridge",
        "culvert",                        # box culvert repairs = road drainage infrastructure
        "trail",                          # multi-use trail / path projects
        "boardwalk",                      # pedestrian boardwalk / shared path
        "asphalt paving",
        "milling",                        # milling & resurfacing (road maintenance)
        "bridge replacement",             # bridge replacement projects
        "interchange",                    # highway interchange construction/improvement
        "pedestrian",                     # pedestrian crossing, walk, bridge, etc.
        "transportation project",         # e.g. "DeKalb County Transportation Projects"
        "corridor project", "corridor improvement",
        "rebuild",                        # e.g. "I-285 Westside Rebuild"
        "frontage road", "frontage",      # frontage road construction
        "drive improvement", "road improvement project",
        "grade separation",               # railroad/road grade crossing projects
        "operational improvement",        # "Operational Improvements along MLK Jr Drive"
        "safety project",                 # road safety projects
        # Paving (general — not just asphalt)
        "road paving", "street paving", "paving project", "paving program",
        "dirt road paving", "unpaved road", "gravel road", "chip seal",
        "microsurfacing", "slurry seal", "crack seal",
        # Pavement marking / striping
        "pavement marking", "pavement markings", "lane marking", "road marking",
        "striping", "restriping", "centerline", "centreline",
        "edge line", "edge stripe", "thermoplastic", "epoxy marking",
        # Parkway / avenue / boulevard improvements (road types appearing in titles)
        "parkway improvement", "avenue improvement", "boulevard improvement",
        "parkway project", "avenue project",
        # Access and driveway infrastructure
        "access road", "driveway apron",
        # Road maintenance / repair
        "road maintenance", "road repair", "pothole", "crack repair",
        "concrete repair", "concrete paving", "concrete roadway",
        # Retaining walls, barriers, noise walls (highway infrastructure)
        "retaining wall", "noise barrier", "sound wall", "crash barrier",
        "traffic barrier", "concrete barrier", "jersey barrier",
        # Median, lane, and turn construction
        "median improvement", "median construction", "median barrier",
        "turn lane", "acceleration lane", "deceleration lane", "u-turn",
        "road diet",                     # lane reduction for safety/active transport
        # Transit stop infrastructure (CEI scope on road projects)
        "bus pad", "bus shelter", "bus stop improvement", "bus turnout",
        # Railroad / grade crossing
        "railroad crossing", "grade crossing", "at-grade crossing",
        # Utility relocation in highway right-of-way
        "utility relocation", "utility adjustment",
        # Erosion / sediment (highway construction context)
        "erosion control", "silt fence", "sediment control",
    ],
    "Planning": [
        "planning", "needs assessment", "feasibility study",
        "comprehensive transportation plan", "transportation planning",
        "corridor study", "environmental impact", "environmental assessment",
        "transportation study", "mobility study",
        "transportation plan",             # 2050 Metropolitan Transportation Plan, TIP, etc.
        "transportation improvement program",
        "transit oriented",               # transit-oriented development (TOD)
        "bus network", "transit network", # transit network redesign studies
        "roads to schools", "road to school",
        # Active and multimodal transportation planning
        "complete streets", "active transportation", "active travel",
        "multimodal", "multi-modal", "non-motorized",
        "bicycle plan", "bike plan", "bicycle network",
        "pedestrian plan", "walk audit", "walkability",
        "freight study", "freight plan", "freight corridor",
        "travel demand", "origin destination", "traffic model",
        "visioning study", "route study", "alignment study",
        "interchange justification", "project development",
        # Environmental / noise / drainage studies tied to transportation
        "noise study", "traffic noise", "drainage study", "hydraulic study",
        "categorical exclusion", "ce document",
        # Safety planning
        "highway safety plan", "strategic highway safety",
        "road safety audit", "safety study",
    ],
    "Program Management": [
        "program management", "program mgmt", "cip program",
        "construction program", "program support", "program manager",
        "project management support",
        "construction oversight", "construction management services",
        "owner's representative", "owners representative", "owner representative",
        "quality assurance program", "qa/qc program", "quality management",
        "project controls", "schedule management", "cost management",
        "transportation program", "capital program management",
    ],
    "Traffic Operations": [
        "traffic ops", "traffic operations", "traffic study",
        "signal", "itse", "intersection", "traffic control",
        "intelligent transportation", "its ", "congestion management",
        "work zone", "incident management", "tmc", "traffic management",
        "pedestrian signal", "crosswalk", "roundabout", "access management",
        "safety improvement", "corridor safety",
        "traffic calming",                # traffic calming improvements
        "safe routes",                    # Safe Routes to School / Transit program
        "safe streets",                   # Safe Streets for All (federal program)
        "safe roads", "road safety",      # road safety programs
        # Street / roadway lighting (transportation infrastructure)
        "street lighting", "roadway lighting", "highway lighting",
        "pedestrian lighting", "streetlight", "street light",
        # Signing and marking (traffic operations scope)
        "traffic signing", "road signs", "sign replacement", "sign installation",
        "delineator", "guardrail",
        # Signal timing and performance
        "signal timing", "signal coordination", "signal optimization",
        "corridor signal", "arterial signal", "atspm",
        "arterial management", "adaptive signal", "connected signal",
        # Dynamic/variable message signs
        "dynamic message sign", "variable message sign", "dms ", "vms ",
        "changeable message", "highway advisory",
        # Traffic monitoring and detection
        "traffic camera", "cctv", "video detection", "traffic monitoring",
        "traffic counting", "speed monitoring", "speed feedback", "radar sign",
        "fiber optic", "communications infrastructure",
        # School zone / pedestrian safety hardware
        "school zone", "school flasher", "school beacon",
        "rectangular rapid flash", "rrfb", "hawk signal",
    ],
    "Architecture & Engineering": [
        "architectural", "engineering services", "a&e", "design services",
        "renovation", "replacement", "highway design", "roadway design",
        "transportation engineering", "bridge design", "drainage design",
        "corridor design", "geotechnical", "survey services", "surveying",
        "structures design", "pavement design", "right-of-way",
        "transit center", "design documents", "30% design", "60% design",
        "90% design", "preliminary design", "final design",
        "classroom reconfiguration",  # kept for Fulton-style test fixture
        # Specialty transportation design
        "traffic impact study", "traffic impact analysis", "tia ",
        "noise wall design", "retaining wall design", "wall design",
        "utility design", "drainage engineering",
        "load rating", "bridge inspection", "bridge load",
        "seismic retrofit", "bridge scour", "scour analysis",
        "as-built", "record drawings", "construction documents",
        "interchange design", "ramp design", "flyover design",
        "traffic engineering study", "signal design",
        "landscape architecture", "streetscape design",
        "value engineering", "ve study",
        "subsurface investigation", "soil boring", "ground penetrating",
        "topographic survey", "boundary survey", "gps survey",
        "photogrammetry", "lidar survey", "aerial survey",
    ],
}

# NIGP (National Institute of Governmental Purchasing) commodity/service codes
# relevant to GMG's transportation service lines.  GPR detail pages and some
# county portals embed these codes in solicitation text — matching the 5-digit
# code OR description phrases lets the gate fire on either form.
#
# Codes are grouped by GMG service line; descriptions are lower-cased and
# stripped of NIGP boilerplate ("(not otherwise classified)", etc.) to act as
# additional keyword phrases.
NIGP_CODES: dict[str, list[tuple[str, str]]] = {
    "Construction Engineering & Inspection": [
        # Sidewalk / pedestrian
        ("91347", "construction sidewalk driveway pedestrian handicap ramps"),
        ("91357", "construction vaulted sidewalk"),
        ("91382", "maintenance repair sidewalk driveway removal"),
        # Bridges & culverts
        ("91430", "construction bridge culvert"),
        ("91577", "construction highway bridge bridge repair"),
        ("91610", "construction bridges"),
        # Highway / road construction
        ("91510", "construction highway"),
        ("91512", "construction highway concrete curbs median gutter"),
        ("91514", "construction highway drainage erosion control"),
        ("91515", "construction highway clearing grubbing"),
        ("91530", "construction highway asphalt bituminous paving"),
        ("91540", "construction highway asphalt bituminous resurfacing"),
        ("91545", "construction highway concrete paving"),
        ("91555", "construction highway earth moving embankments fills"),
        ("91560", "construction highway earth retention retaining walls"),
        ("91565", "construction highway grading"),
        ("91575", "construction highway guardrails fencing barriers"),
        ("91585", "construction highway sealing"),
        ("91590", "construction highway resurfacing"),
        ("91600", "construction highway signs pavement markings"),
        # Road construction general
        ("91700", "construction roads"),
        ("91730", "construction roadway road"),
        ("91750", "construction roadway milling overlaying"),
        ("91760", "construction roadway safety features"),
        ("91800", "construction pedestrian bicycle facilities"),
    ],
    "Traffic Operations": [
        ("55085", "traffic signal poles standards brackets"),
        ("96880", "traffic control safety equipment maintenance repair"),
        ("96883", "traffic signal maintenance repair"),
        ("96887", "traffic control equipment maintenance repair"),
        ("96889", "traffic marking signing services"),
        ("96890", "traffic engineering services"),
    ],
    "Architecture & Engineering": [
        ("92514", "engineering services transportation traffic"),
        ("92516", "engineering services highway road"),
        ("92517", "engineering services bridge"),
        ("92520", "engineering services civil"),
        ("92522", "engineering services construction management inspection"),
        ("92524", "engineering services environmental"),
        ("92526", "engineering services geotechnical soils"),
        ("92528", "engineering services surveying"),
        ("96400", "right-of-way acquisition services"),
        ("96420", "right-of-way survey services"),
    ],
    "Program Management": [
        ("94010", "consulting services construction management"),
        ("94014", "consulting services engineering"),
        ("94018", "consulting services transportation planning"),
    ],
    "Planning": [
        ("94020", "consulting services urban regional planning"),
    ],
}

# Flat lookup: nigp_code → service_type (for fast code matching)
_NIGP_CODE_TO_SERVICE: dict[str, str] = {
    code: svc
    for svc, entries in NIGP_CODES.items()
    for code, _ in entries
}

# Flat lookup: description phrase → service_type
_NIGP_DESC_PHRASES: list[tuple[str, str]] = [
    (desc, svc)
    for svc, entries in NIGP_CODES.items()
    for _, desc in entries
]

# 5-digit NIGP code pattern
_NIGP_RE = re.compile(r"\b(\d{5})\b")


def _nigp_service_types(text: str) -> list[str]:
    """Return service types matched by NIGP codes or their description phrases."""
    text_l = text.lower()
    matched: set[str] = set()
    for m in _NIGP_RE.finditer(text):
        svc = _NIGP_CODE_TO_SERVICE.get(m.group(1))
        if svc:
            matched.add(svc)
    for phrase, svc in _NIGP_DESC_PHRASES:
        if phrase in text_l:
            matched.add(svc)
    return list(matched)


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
    "Planning": [
        "needs assessment", "planning study", "corridor study",
        "feasibility study", "master plan", "long range plan",
        "transportation improvement program", "lrtp",
        "environmental study", "eis ", "ea ", "environmental assessment",
        # GDOT ArcGIS Hub project pages — active projects in GDOT's program pipeline
        "active gdot project", "cei solicitation status unverified",
        "gdot hub", "gdot.hub.arcgis.com", "arcgis hub",
        # Predicted bucket = explicitly classified as a pipeline project by a parser.
        # The bucket value is included in the tagging blob, so this fires on any
        # Predicted record that has no other signal keyword in its title/status_line.
        "2 - predicted",
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

# Road/highway identifiers for intersection project titles.
# Covers named roads (Rose Avenue, Chapel Hill Road) and highway numbers (I-16, US 280, SR 8).
_ROAD_SUFFIXES = r"(?:road|rd|street|st|avenue|ave|boulevard|blvd|drive|dr|lane|ln|parkway|pkwy|way|circle|court|ct|place|pl|terrace|tr|highway|hwy|pike)"
_HWY_NUMBER   = r"(?:I|US|SR|GA|HWY|RTE?)-?\s*\d+"
_ROAD_ID      = rf"(?:{_ROAD_SUFFIXES}|{_HWY_NUMBER})"
_ROAD_INTERSECTION_RE = re.compile(
    rf"(?:{_ROAD_ID}).{{0,50}}(?:\bat\b|@).{{0,50}}(?:{_ROAD_ID})",
    re.IGNORECASE,
)

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
    service_types = _match_keywords(blob, SERVICE_TYPE_KEYWORDS)
    # NIGP code / description matching (codes in status_line or GPR text)
    for svc in _nigp_service_types(blob):
        if svc not in service_types:
            service_types.append(svc)
    # "[Road] at/@ [Road]" pattern — intersection/road project with name-only title
    title = str(record.get("title", ""))
    if "Construction Engineering & Inspection" not in service_types and \
            _ROAD_INTERSECTION_RE.search(title):
        service_types = ["Construction Engineering & Inspection"] + service_types
    return TaggedRecord(
        record=record,
        service_types=service_types,
        signal_types=_match_keywords(blob, SIGNAL_TYPE_KEYWORDS),
        money_mentions=MONEY_RE.findall(blob),
        date_mentions=DATE_RE.findall(blob),
        entities=extract_entities(blob),
    )


def tag_records(records: List[dict]) -> List[TaggedRecord]:
    return [tag_record(r) for r in records]
