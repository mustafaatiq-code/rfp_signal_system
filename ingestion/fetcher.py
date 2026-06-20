"""
Production ingestion fetcher for Layer 1 (Ingestion) of the RFP Signal
Detection & Opportunity Scoring System.

Two fetch strategies, matching the midterm deck's architecture:

- fetch_static(url): requests + BeautifulSoup4 for plain server-rendered
  pages (county/agency CMS sites such as fultonschools.org,
  henrycountyga.gov, doas.ga.gov). This is what most Bucket 2 (early signal)
  sources and many Bucket 1 agency procurement pages look like.

- fetch_dynamic(url): Playwright fallback for JS-heavy single-page apps
  (Georgia Procurement Registry, BidNet Direct, OpenGov portals, BoardDocs).
  These render their listings client-side via XHR/AJAX after page load, so a
  plain HTTP GET returns an empty shell.

Run this module on a machine with normal (non-sandboxed) internet access.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "GMG-RFP-Signal-Bot/0.1 (+contact: practicum project; respects robots.txt)"
)

DEFAULT_HEADERS = {"User-Agent": USER_AGENT}


@dataclass
class FetchResult:
    url: str
    status_code: Optional[int]
    html: Optional[str]
    fetched_via: str  # "static" | "dynamic" | "error"
    error: Optional[str] = None


def fetch_static(url: str, timeout: int = 15, retries: int = 2) -> FetchResult:
    """Fetch a server-rendered page with requests. Retries on transient errors."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return FetchResult(
                url=url, status_code=resp.status_code, html=resp.text,
                fetched_via="static",
            )
        except requests.RequestException as exc:
            last_err = str(exc)
            logger.warning("fetch_static attempt %d failed for %s: %s",
                           attempt + 1, url, exc)
            time.sleep(1.5 * (attempt + 1))
    return FetchResult(url=url, status_code=None, html=None,
                        fetched_via="error", error=last_err)


def fetch_dynamic(url: str, wait_selector: Optional[str] = None,
                   timeout_ms: int = 20000) -> FetchResult:
    """
    Fetch a JS-rendered page using Playwright (headless Chromium).
    Use for GPR, BidNet, OpenGov, BoardDocs and similar SPA-style portals.

    Requires: `pip install playwright && playwright install chromium`
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return FetchResult(url=url, status_code=None, html=None,
                            fetched_via="error",
                            error="playwright not installed")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            html = page.content()
            browser.close()
            return FetchResult(url=url, status_code=200, html=html,
                                fetched_via="dynamic")
    except Exception as exc:  # noqa: BLE001 - surface any Playwright error
        return FetchResult(url=url, status_code=None, html=None,
                            fetched_via="error", error=str(exc))


def to_soup(result: FetchResult) -> Optional[BeautifulSoup]:
    if not result.html:
        return None
    # Prefer lxml (fast, lenient) but fall back to the stdlib parser so the
    # production fetcher never hard-fails on a host where lxml isn't installed.
    try:
        return BeautifulSoup(result.html, "lxml")
    except Exception:  # noqa: BLE001 - FeatureNotFound when lxml is absent
        return BeautifulSoup(result.html, "html.parser")


def fetch_from_cache(path: str) -> FetchResult:
    """
    Load a previously saved page (used in this dev sandbox, where outbound
    network is restricted to an allowlist and these government sites can't
    be reached directly). Real production runs should use fetch_static /
    fetch_dynamic above instead.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return FetchResult(url=f"file://{path}", status_code=200, html=text,
                        fetched_via="cache")
