"""Remove stale/bad records left in the DB from pre-fix pipeline runs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.db import get_connection

STALE_IDS = [
    # Old MARTA bad-parse records (solicitation IDs before the regex fix)
    "MARTA-1", "MARTA-2", "MARTA-3", "MARTA-4", "MARTA-5",
    # Old SAM.gov records from bad runs (wrong NAICS/state — military/DNA/forest)
    "f74993bf6d60419c80f23de5f5fa6be7",  # R3 National Forest Roads IDIQ
    "eaad9d7cd5f84f9c8ad1ccdddd3c9b9e",  # OME Library Preparation + Sequencing
    "ce1fccb2aadc47bb8bace9462ce76186",  # Solar Construction on New Building
    "cb50907a2aa1448597961851272b3ff3",  # DNA LIBRARY PREP & METABARCODING
    "c995faa37cd5408d998a685bcbf9487f",  # IFB FA480026B0002 - JBLE Paving
    "c80d403cc58d486990f48e0d87310640",  # Replace Physical Access Control System
    "d70b5517d7d544218e726d4c39612e29",  # PEO-TIS Radio Industry Day
]

with get_connection() as conn:
    placeholders = ",".join("?" for _ in STALE_IDS)
    deleted = conn.execute(
        f"DELETE FROM opportunities WHERE solicitation_id IN ({placeholders})",
        STALE_IDS
    ).rowcount
    print(f"Deleted {deleted} stale records")
    n = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    print(f"Rows remaining: {n}")
    for row in conn.execute(
        "SELECT solicitation_id, title FROM opportunities ORDER BY solicitation_id"
    ).fetchall():
        print(f"  {row[0]}: {row[1][:70]}")
