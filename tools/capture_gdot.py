"""
Capture GDOT prequalified-consultant solicitations using an interactive login.

Why this exists: solicitation.dot.ga.gov is behind Microsoft Identity (Azure AD)
login and is only visible to firms GDOT has approved as prequalified consultants.
That login is interactive (with MFA), so it can't be scraped headlessly. This
tool opens a real browser, lets YOU log in once (the session is remembered in a
local profile), then saves the rendered solicitation listing so the parser in
ingestion/parsers/gdot_solicitation.py can turn it into records.

Nothing here runs as part of the pipeline. Until you run this AND are logged in,
the GDOT source stays a no-op that returns [] — it cannot affect anything else.

PRIVACY: this only navigates to solicitation.dot.ga.gov and writes that page's
HTML into data/raw/gdot_capture/ (gitignored). No credentials are handled or
stored by this script — you type them into the real Microsoft login page.

Usage (from the rfp_signal_system/ folder, after `playwright install chromium`):
    python tools/capture_gdot.py
    # A browser opens. Log in, navigate to the solicitations list, then press
    # Enter in this terminal to capture the page.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CAPTURE_DIR = BASE / "data" / "raw" / "gdot_capture"
PROFILE_DIR = CAPTURE_DIR / ".browser_profile"   # persists the login session
PORTAL_URL = "https://solicitation.dot.ga.gov/"


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture GDOT consultant solicitations via interactive login")
    ap.add_argument("--url", default=PORTAL_URL, help="listing URL to capture")
    ap.add_argument("--headless", action="store_true",
                    help="run headless (only works once the session is already saved)")
    args = ap.parse_args()

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=args.headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR launching browser: {exc}")
            print(">>> Run `playwright install chromium` first.")
            return 1

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(f"Navigating to: {args.url}")
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)

        if not args.headless:
            print("\n" + "=" * 68)
            print("A browser window is open.")
            print("  1. Log in with your GDOT prequalified-consultant account.")
            print("  2. Navigate to the page that LISTS the open solicitations.")
            print("  3. Come back here and press Enter to capture that page.")
            print("=" * 68)
            try:
                input("Press Enter when the solicitations list is on screen... ")
            except (EOFError, KeyboardInterrupt):
                ctx.close()
                print("\nCancelled — nothing captured.")
                return 1

        title = page.title()
        html = page.content()
        try:
            table_text = page.inner_text("table")
        except Exception:  # noqa: BLE001
            table_text = page.inner_text("body")
        ctx.close()

    (CAPTURE_DIR / "page.html").write_text(html, encoding="utf-8")
    (CAPTURE_DIR / "table.txt").write_text(table_text, encoding="utf-8")

    print(f"\npage title: {title!r}")
    if "sign in" in (title or "").lower() or "login" in (title or "").lower():
        print("!! Still on a login page — the capture likely has no solicitations.")
        print("   Re-run (without --headless), finish logging in, reach the LIST, then press Enter.")
    print(f"wrote:\n  {CAPTURE_DIR / 'page.html'} ({len(html):,} bytes)")
    print(f"  {CAPTURE_DIR / 'table.txt'} ({len(table_text):,} chars)")
    print("\nDone. The pipeline will now parse this on the next `python run_pipeline.py --live`.")
    print("If it parses 0 records, share page.html and the parse_html selectors get tuned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
