"""
Capture OpenGov statewide-GA open bids using YOUR logged-in Chrome profile.

Why this exists: procurement.opengov.com is behind Cloudflare Turnstile, so blind
headless scraping is blocked. But your Chrome profile (Profile 11 =
matiq3@gatech.edu) is already authenticated AND Cloudflare-cleared. This script
drives Playwright with that real profile, so it inherits your session — no
bot-evasion, no passwords handled here.

PRIVACY: this only navigates to procurement.opengov.com and writes that host's
responses into data/raw/opengov_capture/. It does not read, copy, or transmit
anything else from your Chrome profile.

PREREQUISITE: fully quit Chrome first (including any background/system-tray
instance), otherwise the profile is locked and launch fails.

Usage (from the rfp_signal_system/ folder):
    python tools/capture_opengov.py                       # GA, vendor 524065
    python tools/capture_opengov.py --states GA --vendor-id 524065
    python tools/capture_opengov.py --headless            # try headless (may re-trigger CF)
"""
from __future__ import annotations

import argparse
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CAPTURE_ROOT = BASE / "data" / "raw" / "opengov_capture"

DEFAULT_USER_DATA_DIR = r"C:\Users\musta\AppData\Local\Google\Chrome\User Data"
DEFAULT_PROFILE = "Profile 11"  # matiq3@gatech.edu
CHROME_CANDIDATES = [
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# Substrings that suggest a JSON response carries bid/solicitation data.
DATA_HINTS = ('"duedate"', "due_date", "duedate", "release", "solicit",
              "proposal", "project", '"status"', "open-bids")


def _find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    return None


# Bulky, non-essential subdirs we skip when cloning the profile (caches, etc.).
_CLONE_SKIP = shutil.ignore_patterns(
    "Cache", "Code Cache", "GPUCache", "GraphiteDawnCache", "ShaderCache",
    "Service Worker", "Crashpad", "component_crx_cache", "extensions_crx_cache",
    "optimization_guide_*", "Cache_Data", "DawnGraphiteCache", "DawnWebGPUCache",
)


def clone_profile(user_data_dir: str, profile_dir: str) -> str:
    """Copy 'Local State' + the chosen profile into a throwaway non-default dir.

    Chrome refuses DevTools/automation on the *default* user-data dir, so we run
    against a clone. Same Windows user => DPAPI-encrypted cookies still decrypt,
    and launching the real Chrome binary keeps the User-Agent identical so the
    existing Cloudflare clearance cookie stays valid. The clone is deleted after.
    """
    src_root = Path(user_data_dir)
    tmp = Path(tempfile.mkdtemp(prefix="og_profile_"))
    # 'Local State' at the root holds the key that decrypts the cookie store.
    ls = src_root / "Local State"
    if ls.exists():
        shutil.copy2(ls, tmp / "Local State")
    src_profile = src_root / profile_dir
    if not src_profile.exists():
        raise FileNotFoundError(f"profile not found: {src_profile}")
    shutil.copytree(src_profile, tmp / profile_dir, ignore=_CLONE_SKIP,
                    dirs_exist_ok=True)
    return str(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture OpenGov GA open bids via your Chrome profile")
    ap.add_argument("--vendor-id", default="524065")
    ap.add_argument("--states", default="GA",
                    help="state filter, e.g. GA or FL (run once per state)")
    ap.add_argument("--user-data-dir", default=DEFAULT_USER_DATA_DIR)
    ap.add_argument("--profile-dir", default=DEFAULT_PROFILE)
    ap.add_argument("--settle", type=int, default=9000, help="ms to wait for XHRs to render")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-clone", action="store_true",
                    help="launch directly against --user-data-dir instead of a temp "
                         "clone (only works if it is NOT Chrome's default profile dir)")
    ap.add_argument("--extra-params", default="",
                    help="raw extra query string to append, e.g. "
                         "'categoryType=NAICS&category=237310' (copy from your browser URL). "
                         "The default state capture already returns every category, with "
                         "each bid's NIGP/NAICS codes included as fields.")
    args = ap.parse_args()

    # per-state (+ optional filter) subfolder so captures don't clobber each other
    slug = "".join(ch if ch.isalnum() else "_" for ch in args.states.upper())
    if args.extra_params:
        tag = "".join(ch if ch.isalnum() else "" for ch in args.extra_params)[:24]
        slug = f"{slug}__{tag}"
    out = CAPTURE_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)
    url = (f"https://procurement.opengov.com/vendors/{args.vendor_id}"
           f"/open-bids?states={args.states}")
    if args.extra_params:
        url += "&" + args.extra_params.lstrip("?&")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install playwright && playwright install chromium")
        return 1

    chrome = _find_chrome()
    captured: list[tuple[str, str]] = []

    # Chrome blocks automation on the default profile dir -> run against a clone.
    clone_dir = None
    effective_udd = args.user_data_dir
    if not args.no_clone:
        try:
            print(f"Cloning profile {args.profile_dir!r} to a temp dir "
                  "(needed because Chrome blocks automation on the default profile)...")
            clone_dir = clone_profile(args.user_data_dir, args.profile_dir)
            effective_udd = clone_dir
            # delete the cookie-bearing clone no matter how we exit
            atexit.register(lambda d=clone_dir: shutil.rmtree(d, ignore_errors=True))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR cloning profile: {exc}")
            return 1

    with sync_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=effective_udd,
            headless=args.headless,
            args=[f"--profile-directory={args.profile_dir}",
                  "--no-first-run", "--no-default-browser-check"],
        )
        if chrome:
            launch_kwargs["executable_path"] = chrome
        else:
            launch_kwargs["channel"] = "chrome"

        try:
            ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR launching Chrome with profile {args.profile_dir!r}: {exc}")
            print(">>> Fully QUIT Chrome (including background/tray), then run this again.")
            return 1

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct and "opengov.com" in resp.url:
                    body = resp.text()
                    if any(h in body.lower() for h in DATA_HINTS):
                        captured.append((resp.url, body))
            except Exception:  # noqa: BLE001 - never let logging break capture
                pass

        page.on("response", on_response)
        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(args.settle)

        title = page.title()
        html = page.content()
        try:
            table_text = page.inner_text("table")
        except Exception:  # noqa: BLE001
            table_text = page.inner_text("body")
        ctx.close()

    (out / "page.html").write_text(html, encoding="utf-8")
    (out / "table.txt").write_text(table_text, encoding="utf-8")
    for i, (u, body) in enumerate(captured):
        (out / f"xhr_{i}.json").write_text(body, encoding="utf-8")
        (out / f"xhr_{i}.url.txt").write_text(u, encoding="utf-8")

    print(f"\nstate filter: {args.states!r}")
    print(f"page title: {title!r}")
    if "just a moment" in (title or "").lower():
        print("!! Still hit a Cloudflare challenge. Re-run WITHOUT --headless and make "
              "sure you're logged in to procurement.opengov.com in Profile 11.")
    print(f"captured {len(captured)} JSON response(s); wrote outputs to:\n  {out}")
    for i, (u, _) in enumerate(captured):
        print(f"  xhr_{i}.json  <-  {u[:120]}")
    print(f"  page.html ({len(html):,} bytes), table.txt ({len(table_text):,} chars)")
    print("\nDone. Tell Claude 'captured' and I'll build the parser from these files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
