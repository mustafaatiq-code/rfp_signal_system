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


def fetch_all(db_path: Path = DB_PATH) -> List[dict]:
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM opportunities ORDER BY rfp_likelihood DESC NULLS LAST"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
