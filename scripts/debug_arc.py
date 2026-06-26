import sys, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO)
from ingestion.fetcher import fetch_static
from ingestion.parsers.arc_news import parse_rss, FEED_URL, _has_procurement_signal
import xml.etree.ElementTree as ET

r = fetch_static(FEED_URL)
print(f"Status: {r.status_code}, length: {len(r.html or '')}")
print(f"Error: {r.error}")
if r.html:
    recs = parse_rss(r.html, days_back=365)
    print(f"Records parsed: {len(recs)}")
    for rec in recs:
        print(f"  SID={rec['solicitation_id']}")
        print(f"  TITLE={rec['title'][:80]}")
        print(f"  STATUS={rec['status_line'][:100]}")
        print()
    print("--- All items in feed (signal check) ---")
    root = ET.fromstring(r.html)
    for item in root.find("channel").findall("item"):
        title = item.findtext("title") or ""
        pub_date = item.findtext("pubDate") or ""
        desc = item.findtext("description") or ""
        has_sig = _has_procurement_signal(title, desc)
        print(f"  [{has_sig}] {pub_date[:16]} | {title[:80]}")
