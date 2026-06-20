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
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from nlp.tagging import TaggedRecord

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
    "Unknown": 0.3,
}

GEOGRAPHY_KEYWORDS = ["georgia", "ga", "gwinnett", "fulton", "henry", "south fulton",
                       "atlanta", "fdot"]  # Phase 1 = Georgia + FDOT D3 (NW Florida) footprint


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


def relevance_gate(tagged: TaggedRecord, est_budget: Optional[float],
                    geography_text: str) -> tuple[bool, str]:
    """Step 1: binary PASS/FAIL gate.

    NOTE: est_budget is currently always None because budget extraction is not
    yet implemented. The $30K floor is a no-op until a budget-parsing step
    is added to the NLP layer and wired through run_pipeline.py.
    """
    if not tagged.service_types:
        return False, "No matching service type (CEI/Planning/Program Mgmt/Traffic Ops/A&E)"
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
    year = record.get("year", today.year)
    age_years = max(today.year - int(year), 0)
    return math.exp(-0.6 * age_years)


def _source_weight(tagged: TaggedRecord) -> float:
    if not tagged.signal_types:
        return 0.2
    return max(SOURCE_WEIGHTS.get(s, 0.3) for s in tagged.signal_types)


def _pipeline_stage_score(record: dict) -> float:
    return PIPELINE_STAGE_SCORE.get(record.get("bucket", "Unknown"), 0.3)


def rfp_likelihood_score(tagged: TaggedRecord) -> float:
    record = tagged.record
    if record.get("bucket") == "1 - Active RFP":
        return 1.0  # Active RFP = 1.0 per deck

    score = (
        0.35 * _signal_count_norm(tagged)
        + 0.30 * _recency_score(record)
        + 0.20 * _source_weight(tagged)
        + 0.15 * _pipeline_stage_score(record)
    )
    return round(min(max(score, 0.0), 1.0), 4)


def score_opportunity(tagged: TaggedRecord, est_budget: Optional[float] = None
                       ) -> ScoredOpportunity:
    record = tagged.record
    geography_text = " ".join(str(record.get(f, "")) for f in
                               ("agency", "title", "source_url"))
    passed, reason = relevance_gate(tagged, est_budget, geography_text)

    if not passed:
        return ScoredOpportunity(
            record=record, passed_gate=False, gate_reason=reason,
            rfp_likelihood=None, flagged_for_review=False,
            bucket=record.get("bucket", "Unknown"),
            service_types=tagged.service_types, signal_types=tagged.signal_types,
        )

    likelihood = rfp_likelihood_score(tagged)
    return ScoredOpportunity(
        record=record, passed_gate=True, gate_reason=reason,
        rfp_likelihood=likelihood,
        flagged_for_review=likelihood >= REVIEW_THRESHOLD,
        bucket=record.get("bucket", "Unknown"),
        service_types=tagged.service_types, signal_types=tagged.signal_types,
    )


def score_all(tagged_records: List[TaggedRecord]) -> List[ScoredOpportunity]:
    return [score_opportunity(t) for t in tagged_records]
