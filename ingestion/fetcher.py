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
import socket
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "GMG-RFP-Signal-Bot/0.1 (+contact: practicum project; respects robots.txt)"
)

DEFAULT_HEADERS = {"User-Agent": USER_AGENT}

# Google DNS-over-HTTPS, used as a fallback resolver. Some Georgia state hosts
# (e.g. www.dot.ga.gov, solicitation.dot.ga.gov) sit in a zone whose DNSSEC the
# default/strict resolvers reject ("DNS server failure"), so the names won't
# resolve locally even though the sites are up. DoH bypasses that.
_DOH_URL = "https://dns.google/resolve"


def _doh_resolve(host: str) -> Optional[str]:
    try:
        resp = requests.get(_DOH_URL, params={"name": host, "type": "A"}, timeout=10)
        answers = resp.json().get("Answer", [])
        ips = [a["data"] for a in answers if a.get("type") == 1]
        return ips[0] if ips else None
    except Exception:  # noqa: BLE001
        return None


def _is_dns_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(m in s for m in ("nameresolution", "failed to resolve",
                                "name or service not known", "getaddrinfo",
                                "temporary failure in name resolution"))


class _DohOverride:
    """Temporarily force host->IP in socket.getaddrinfo. SNI and certificate
    validation still use the real hostname, so HTTPS stays verified."""

    def __init__(self, mapping: dict):
        self.mapping = mapping
        self._orig = None

    def __enter__(self):
        self._orig = socket.getaddrinfo
        orig, mapping = self._orig, self.mapping

        def patched(host, *args, **kwargs):
            return orig(mapping.get(host, host), *args, **kwargs)

        socket.getaddrinfo = patched
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._orig


@dataclass
class FetchResult:
    url: str
    status_code: Optional[int]
    html: Optional[str]
    fetched_via: str  # "static" | "dynamic" | "cache" | "blocked" | "error"
    error: Optional[str] = None


# Substrings that mark an anti-bot interstitial (Cloudflare "Just a moment" /
# Turnstile, generic "checking your browser") rather than real page content.
# Observed live on Henry County's OpenGov portal, 2026-06-20.
ANTIBOT_MARKERS = (
    "just a moment",
    "performing security verification",
    "checking your browser before accessing",
    "challenges.cloudflare.com",
    "cf-chl",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
)


def looks_like_antibot(title: str, html: str) -> bool:
    """True if a fetched page is an anti-bot challenge, not real content.
    Kept as a small pure function so it can be unit-tested without a browser."""
    blob = f"{title or ''} {(html or '')[:6000]}".lower()
    return any(marker in blob for marker in ANTIBOT_MARKERS)


def fetch_static(url: str, timeout: int = 15, retries: int = 2,
                  dns_fallback: bool = True) -> FetchResult:
    """Fetch a server-rendered page with requests. Retries on transient errors.

    On a name-resolution failure (e.g. the ga.gov DNSSEC issue), resolves the
    host once via DoH and retries against that IP with the hostname preserved
    for SNI/cert validation."""
    last_err = None
    host = urlparse(url).hostname
    doh_map: dict = {}
    for attempt in range(retries + 1):
        try:
            with _DohOverride(doh_map):
                resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return FetchResult(
                url=url, status_code=resp.status_code, html=resp.text,
                fetched_via="static",
            )
        except requests.RequestException as exc:
            last_err = str(exc)
            if (dns_fallback and host and host not in doh_map
                    and _is_dns_error(exc)):
                ip = _doh_resolve(host)
                if ip:
                    doh_map[host] = ip
                    logger.warning("DNS fallback for %s -> %s (via DoH)", host, ip)
                    continue  # retry immediately with the override in place
            logger.warning("fetch_static attempt %d failed for %s: %s",
                           attempt + 1, url, exc)
            time.sleep(1.5 * (attempt + 1))
    return FetchResult(url=url, status_code=None, html=None,
                        fetched_via="error", error=last_err)


def fetch_dynamic(url: str, wait_selector: Optional[str] = None,
                   timeout_ms: int = 30000, settle_ms: int = 4000) -> FetchResult:
    """
    Fetch a JS-rendered page using Playwright (headless Chromium).
    Use for GPR, BidNet, OpenGov, BoardDocs and similar SPA-style portals.

    Requires: `pip install playwright && playwright install chromium`

    Robustness notes (from live testing):
      * Waits on "domcontentloaded" + a fixed settle, not "networkidle":
        SPAs hold connections open (polling/websockets) so networkidle often
        never fires and times out.
      * If wait_selector is given, a miss is non-fatal — we still return the
        HTML so the anti-bot check can explain *why* the selector never showed.
      * Returns fetched_via="blocked" (not "error") when the page is an
        anti-bot challenge, so callers can distinguish "gated" from "broken".
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
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)  # let client-side XHRs render
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:  # noqa: BLE001 - non-fatal; see anti-bot check
                    pass
            html = page.content()
            title = page.title()
            browser.close()

            if looks_like_antibot(title, html):
                return FetchResult(
                    url=url, status_code=403, html=html, fetched_via="blocked",
                    error=f"anti-bot challenge (page title={title!r}); "
                          "headless scraping not permitted by this host",
                )
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
