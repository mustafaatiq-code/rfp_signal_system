"""
Layer 3 (Scoring Engine) — relevance gate + RFP likelihood score, exactly as
specified in the midterm deck:

Step 1 — Relevance Gate (binary PASS/FAIL)
    service type match + Georgia geography filter + minimum project budget
    ($30,000). Records failing the gate are discarded.

Step 2 — RFP Likelihood Score (0.0-1.0, weighted sum)
    score = 0.35*signal_count_norm + 0.30*recency_score
          + 0.20*source_weight    + 0.15*pipeline_stage_score
    Active RFP (Bucket 1) = 1.0.

Step 3 — Review Threshold
    score >= 0.50 -> flagged for the proposals team's review queue.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from dateutil import parser as dateparser

from nlp.tagging import TaggedRecord, DATE_RE

MIN_BUDGET = 30_000
REVIEW_THRESHOLD = 0.50

# Source weight ranking from the deck: funding approval > needs study > news article
SOURCE_WEIGHTS = {
    "SPLOST": 1.0,
    "Bond Issuance": 0.9,
    "Capital Budget": 0.9,
    "State Budget Session": 0.85,
    "Legislation": 0.7,
    "Planning Study": 0.6,
    "Political Meetings": 0.5,
    "News / Press": 0.3,
    "Active RFP": 1.0,
}

PIPELINE_STAGE_SCORE = {
    "1 - Active RFP": 1.0,
    "2 - Predicted": 0.5,
    "Awarded": 0.0,   # opportunity already closed -- not actionable
    "Cancelled": 0.0,
    "Expired RFP (past due)": 0.0,  # due date already passed -- not actionable
    "Unknown": 0.3,
}

GEOGRAPHY_KEYWORDS = [
    # Georgia
    "georgia",
    # GA government URL patterns (covers ssl.doas.state.ga.us / GPR, countyga.gov, etc.)
    "state.ga.us", "ga.gov",
    # GA-specific road funding programs (implies Georgia by definition)
    "lmig", "tsplost",
    # Metro Atlanta counties
    "gwinnett", "fulton", "henry", "south fulton", "atlanta", "dekalb", "cobb",
    "cherokee", "fayette", "forsyth", "newton", "barrow", "walton", "rockdale",
    "paulding", "douglas", "coweta", "spalding",
    # GA cities / agencies common in our data
    "chamblee", "decatur", "alpharetta", "roswell", "marietta", "smyrna",
    "canton", "cumming", "norcross", "duluth", "lawrenceville", "conyers",
    "newnan", "carrollton", "brookhaven", "buford", "albany", "augusta",
    "marta", "atlanta regional",     # ARC/MARTA
    "gdot", "bartow",
    # Florida
    "florida", "fdot",
]


@dataclass
class ScoredOpportunity:
    record: dict
    passed_gate: bool
    gate_reason: str
    rfp_likelihood: Optional[float]
    flagged_for_review: bool
    bucket: str
    service_types: List[str]
    signal_types: List[str]
    due_date: Optional[str] = None   # ISO date parsed from the status line, if any
    is_expired: bool = False         # active RFP whose due date has already passed


# ISO date in status_line, e.g. "Due date: 2026-06-23" (Cobb/Gwinnett/Fayette style)
_ISO_DUE_RE = re.compile(r"due date[:\s]+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def parse_due_date(record: dict) -> Optional[date]:
    """Pull a due date out of an Active-RFP status line.

    Handles both ISO format ('Due date: 2026-06-23', used by county parsers)
    and spelled-out month format ('March 24, 2026', used by Fulton Schools /
    MARTA parsers).
    """
    if record.get("bucket") != "1 - Active RFP":
        return None
    status = str(record.get("status_line", ""))
    # ISO format first (county parsers: Cobb, Gwinnett, Fayette, BidNet)
    m = _ISO_DUE_RE.search(status)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    # Spelled-out month format (Fulton Schools, MARTA)
    m = DATE_RE.search(status)
    if not m:
        return None
    try:
        return dateparser.parse(m.group(0)).date()
    except (ValueError, OverflowError):
        return None


def effective_bucket(record: dict, today: Optional[date] = None) -> str:
    """Bucket as scored: a '1 - Active RFP' whose due date has passed is
    reclassified as expired so it is neither scored 1.0 nor flagged."""
    today = today or date.today()
    bucket = record.get("bucket", "Unknown")
    if bucket == "1 - Active RFP":
        due = parse_due_date(record)
        if due is not None and due < today:
            return "Expired RFP (past due)"
    return bucket


def relevance_gate(tagged: TaggedRecord, est_budget: Optional[float],
                    geography_text: str) -> tuple[bool, str]:
    """Step 1: binary PASS/FAIL gate.

    NOTE: est_budget is currently always None because budget extraction is not
    yet implemented. The $30K floor is a no-op until a budget-parsing step
    is added to the NLP layer and wired through run_pipeline.py.
    """
    if not tagged.service_types:
        return False, "No matching service type (Construction Engineering & Inspection / Planning / Program Management / Traffic Operations / Architecture & Engineering)"
    if not any(kw in geography_text.lower() for kw in GEOGRAPHY_KEYWORDS):
        return False, "Outside Phase 1 Georgia geography"
    if est_budget is not None and est_budget < MIN_BUDGET:
        return False, f"Below minimum project budget (${MIN_BUDGET:,})"
    return True, "PASS"


def _signal_count_norm(tagged: TaggedRecord, max_signals: int = 4) -> float:
    return min(len(tagged.signal_types), max_signals) / max_signals


def _recency_score(record: dict, today: Optional[date] = None) -> float:
    """Exponential decay by record year vs. today. Active/current-year
    items score highest; older awarded/cancelled items decay."""
    today = today or date.today()
    year = record.get("year") or today.year  # some sources (OpenGov) carry no year
    age_years = max(today.year - int(year), 0)
    return math.exp(-0.6 * age_years)


def _source_weight(tagged: TaggedRecord) -> float:
    if not tagged.signal_types:
        return 0.2
    raw = max(SOURCE_WEIGHTS.get(s, 0.3) for s in tagged.signal_types)
    # For Predicted entries, "Active RFP" is a keyword false-positive — if a
    # solicitation were truly open it would be in bucket 1.  Exclude it from the
    # weight calculation; use the best remaining signal instead.
    bucket = tagged.record.get("bucket", "")
    if "Predicted" in bucket:
        non_rfp = [s for s in tagged.signal_types if s != "Active RFP"]
        if non_rfp:
            return max(SOURCE_WEIGHTS.get(s, 0.3) for s in non_rfp)
        return SOURCE_WEIGHTS["Planning Study"]   # 0.6 floor when only signal was "Active RFP"
    return raw


def _pipeline_stage_score(bucket: str) -> float:
    return PIPELINE_STAGE_SCORE.get(bucket, 0.3)


def rfp_likelihood_score(tagged: TaggedRecord, today: Optional[date] = None) -> float:
    record = tagged.record
    today = today or date.today()
    bucket = effective_bucket(record, today)
    if bucket == "1 - Active RFP":
        return 1.0  # open Active RFP (due date in the future) = 1.0 per deck

    score = (
        0.35 * _signal_count_norm(tagged)
        + 0.30 * _recency_score(record, today)
        + 0.20 * _source_weight(tagged)
        + 0.15 * _pipeline_stage_score(bucket)
    )
    return round(min(max(score, 0.0), 1.0), 4)


def score_opportunity(tagged: TaggedRecord, est_budget: Optional[float] = None,
                       today: Optional[date] = None) -> ScoredOpportunity:
    record = tagged.record
    today = today or date.today()
    geography_text = " ".join(str(record.get(f, "")) for f in
                               ("agency", "title", "source_url", "status_line"))
    passed, reason = relevance_gate(tagged, est_budget, geography_text)

    bucket = effective_bucket(record, today)
    due = parse_due_date(record)
    is_expired = bucket == "Expired RFP (past due)"

    if not passed:
        return ScoredOpportunity(
            record=record, passed_gate=False, gate_reason=reason,
            rfp_likelihood=None, flagged_for_review=False,
            bucket=bucket,
            service_types=tagged.service_types, signal_types=tagged.signal_types,
            due_date=due.isoformat() if due else None, is_expired=is_expired,
        )

    likelihood = rfp_likelihood_score(tagged, today)
    return ScoredOpportunity(
        record=record, passed_gate=True, gate_reason=reason,
        rfp_likelihood=likelihood,
        # an expired RFP is never actionable, so never flagged regardless of score
        flagged_for_review=(not is_expired) and likelihood >= REVIEW_THRESHOLD,
        bucket=bucket,
        service_types=tagged.service_types, signal_types=tagged.signal_types,
        due_date=due.isoformat() if due else None, is_expired=is_expired,
    )


def score_all(tagged_records: List[TaggedRecord],
              today: Optional[date] = None) -> List[ScoredOpportunity]:
    return [score_opportunity(t, today=today) for t in tagged_records]
