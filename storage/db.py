"""
SQLite persistence layer (prototype storage tier from the deck;
PostgreSQL is the planned production swap -- same schema applies).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from scoring.engine import ScoredOpportunity

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "opportunities.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agency TEXT,
    title TEXT,
    solicitation_id TEXT,
    year INTEGER,
    bucket TEXT,
    passed_gate INTEGER,
    gate_reason TEXT,
    rfp_likelihood REAL,
    flagged_for_review INTEGER,
    service_types TEXT,
    signal_types TEXT,
    source_url TEXT,
    due_date TEXT,
    is_expired INTEGER,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agency, solicitation_id)
);
"""

# Columns added after the first schema version; applied to pre-existing DBs.
_MIGRATIONS = {"due_date": "TEXT", "is_expired": "INTEGER"}


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)")}
    for col, coltype in _MIGRATIONS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col} {coltype}")
    conn.commit()
    return conn


def upsert_opportunities(opps: List[ScoredOpportunity],
                          db_path: Path = DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        with conn:  # auto-commit on success, auto-rollback on exception
            cur = conn.cursor()
            count = 0
            for o in opps:
                r = o.record
                cur.execute(
                    """
                    INSERT INTO opportunities
                        (agency, title, solicitation_id, year, bucket, passed_gate,
                         gate_reason, rfp_likelihood, flagged_for_review,
                         service_types, signal_types, source_url, due_date, is_expired)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agency, solicitation_id) DO UPDATE SET
                        title=excluded.title, year=excluded.year, bucket=excluded.bucket,
                        passed_gate=excluded.passed_gate, gate_reason=excluded.gate_reason,
                        rfp_likelihood=excluded.rfp_likelihood,
                        flagged_for_review=excluded.flagged_for_review,
                        service_types=excluded.service_types,
                        signal_types=excluded.signal_types,
                        source_url=excluded.source_url,
                        due_date=excluded.due_date, is_expired=excluded.is_expired
                    """,
                    (
                        r.get("agency"), r.get("title"), r.get("solicitation_id"),
                        r.get("year"), o.bucket, int(o.passed_gate), o.gate_reason,
                        o.rfp_likelihood, int(o.flagged_for_review),
                        json.dumps(o.service_types), json.dumps(o.signal_types),
                        r.get("source_url"), o.due_date, int(o.is_expired),
                    ),
                )
                count += 1
    finally:
        conn.close()
    return count


def refresh_expired_buckets(db_path: Path = DB_PATH) -> int:
    """Reclassify Active RFPs whose due date has passed to 'Expired RFP (past due)'.

    Called on every dashboard load so the DB stays accurate as time passes
    without needing a full pipeline re-run.  Returns the number of rows updated.
    """
    from datetime import date
    today = date.today()
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE opportunities
                SET bucket = 'Expired RFP (past due)',
                    is_expired = 1,
                    rfp_likelihood = 0.0,
                    flagged_for_review = 0
                WHERE (
                    (bucket = '1 - Active RFP' AND due_date IS NOT NULL AND due_date < ?)
                    OR bucket IN ('Awarded', 'Cancelled')
                )
                """,
                (today.isoformat(),),
            )
            return cur.rowcount
    finally:
        conn.close()


def purge_expired(db_path: Path = DB_PATH) -> int:
    """Delete stale Active RFP rows. Returns count removed.

    Removes rows that are:
    - have a due_date that has already passed, OR
    - have no due_date but year < current year (no parseable close date, clearly old)
    """
    from datetime import date
    today = date.today()
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM opportunities
                WHERE bucket = '1 - Active RFP'
                  AND (
                    (due_date IS NOT NULL AND due_date < ?)
                    OR (due_date IS NULL AND year < ?)
                  )
                """,
                (today.isoformat(), today.year),
            )
            return cur.rowcount
    finally:
        conn.close()


def rescore_existing(db_path: Path = DB_PATH) -> int:
    """Re-tag and re-score all rows using current SERVICE_TYPE_KEYWORDS.

    Called after keyword updates so existing DB records pick up the new rules
    without requiring a full pipeline re-run against live sources.
    """
    import json
    from datetime import date

    from nlp.tagging import tag_record
    from scoring.engine import relevance_gate, rfp_likelihood_score, REVIEW_THRESHOLD

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM opportunities").fetchall()]
    conn.row_factory = None

    today = date.today()
    # Only rescore gate-failed rows; gate-passed rows used status_line during
    # original ingestion (not stored in DB) and should keep their scores.
    rows = [r for r in rows if not r.get("passed_gate")]

    with conn:
        cur = conn.cursor()
        for r in rows:
            record = {
                "title": r.get("title") or "",
                "agency": r.get("agency") or "",
                "bucket": r.get("bucket") or "",
                "status_line": "",        # not persisted in DB; title carries most signal
                "source_url": r.get("source_url") or "",
            }
            tagged = tag_record(record)
            geography_text = f"{record['agency']} {record['title']} {record['source_url']}"
            passed, reason = relevance_gate(tagged, None, geography_text)

            bucket = r.get("bucket") or "Unknown"
            stored_due = r.get("due_date")
            if bucket == "1 - Active RFP" and stored_due:
                try:
                    if date.fromisoformat(stored_due) < today:
                        bucket = "Expired RFP (past due)"
                except ValueError:
                    pass
            is_expired = bucket in ("Expired RFP (past due)", "Awarded", "Cancelled")

            if passed:
                likelihood = rfp_likelihood_score(tagged, today)
                flagged = (not is_expired) and likelihood >= REVIEW_THRESHOLD
            else:
                likelihood = None
                flagged = False

            cur.execute(
                """
                UPDATE opportunities
                SET passed_gate=?, gate_reason=?, rfp_likelihood=?,
                    flagged_for_review=?, service_types=?, signal_types=?
                WHERE id=?
                """,
                (
                    int(passed), reason,
                    likelihood, int(flagged),
                    json.dumps(tagged.service_types),
                    json.dumps(tagged.signal_types),
                    r["id"],
                ),
            )
    conn.close()
    return len(rows)


def fetch_all(db_path: Path = DB_PATH) -> List[dict]:
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM opportunities ORDER BY rfp_likelihood DESC NULLS LAST"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
