"""Headless rendering via Playwright.

Captures three artifacts per page: the rendered DOM HTML, the visible text (for
hashing + LLM input), and a full-page screenshot (for vision extraction of
image/chart-based pipelines). Per-site `render_config` overrides handle JS dashboards,
accordions, and cookie banners without hand-written parsers.
"""

from __future__ import annotations

import re
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from pipeline_intel.config import settings

HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)=["']([^"']*)["']""")
PIPELINE_IMAGE_HINTS = (
    "pipeline",
    "program",
    "candidate",
    "development",
    "clinical",
)


# Generic escalation applied when render_config.repair_mode is set: a re-render that tries
# harder to surface lazily-loaded or collapsed pipeline content, without per-site config.
GENERIC_DISMISS_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "[aria-label='Accept all']",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I Agree')",
    ".cookie-accept",
)
GENERIC_EXPAND_SELECTORS = (
    "button:has-text('Show more')",
    "button:has-text('Load more')",
    "button:has-text('View all')",
    "[aria-expanded='false']",
    ".accordion__button",
)


@dataclass
class RenderResult:
    url: str
    http_status: int | None
    html: str
    text: str
    screenshot: bytes
    meta: dict = field(default_factory=dict)


def _merge_selectors(site: list[str] | None, generic: tuple[str, ...]) -> list[str]:
    out = list(site or [])
    for sel in generic:
        if sel not in out:
            out.append(sel)
    return out


def repair_render_config(cfg: dict | None) -> dict:
    """Escalated render config for repair-mode re-rendering: longer settle, full-page shot,
    scroll to trigger lazy-load, and generic cookie/expand selectors merged with per-site ones.
    Pure + idempotent so it is unit-testable without a browser."""
    cfg = dict(cfg or {})
    cfg["wait_until"] = cfg.get("wait_until", "load")
    cfg["wait_ms"] = max(int(cfg.get("wait_ms", 2000)), 3500)
    cfg["full_page"] = True
    cfg["scroll"] = True
    cfg["dismiss_selectors"] = _merge_selectors(cfg.get("dismiss_selectors"), GENERIC_DISMISS_SELECTORS)
    cfg["expand_selectors"] = _merge_selectors(cfg.get("expand_selectors"), GENERIC_EXPAND_SELECTORS)
    return cfg


def robots_allows(url: str, user_agent: str) -> bool:
    """Good-citizen check. Fail-open on robots fetch errors (treat as allowed) but
    record the reason in meta upstream."""
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - robots URL is derived from input URL
            lines = resp.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return True
    # Some sites place Crawl-delay before the first User-agent. Python's parser can
    # treat that as a malformed default entry, so trim harmless preamble directives.
    first_agent = next(
        (i for i, line in enumerate(lines) if line.strip().lower().startswith("user-agent:")),
        0,
    )
    rp.parse(lines[first_agent:])
    return rp.can_fetch(user_agent, url)


def render(url: str, render_config: dict | None = None) -> RenderResult:
    """Render a page. render_config keys (all optional):
        wait_until:      load | domcontentloaded | networkidle (default networkidle)
        wait_ms:         extra settle time after load (default 1500)
        expand_selectors:list[str] of selectors to click (accordions/"show more")
        dismiss_selectors:list[str] of selectors to click once (cookie/consent banners)
        full_page:       full-page screenshot (default True)
    """
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — heavy import, defer

    cfg = render_config or {}
    if cfg.get("repair_mode"):
        cfg = repair_render_config(cfg)
    s = settings()
    # `load` over `networkidle`: heavy pharma sites with constant analytics/beacon traffic
    # never reach networkidle and time out with zero artifacts. `load` + a settle wait
    # captures the DOM reliably; sites that truly need networkidle set it in render_config.
    wait_until = cfg.get("wait_until", "load")
    wait_ms = int(cfg.get("wait_ms", 2000))
    goto_timeout = int(cfg.get("goto_timeout_ms", 60000))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=s.browser_user_agent,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            # Many legit small-cap pharma sites have expired/misconfigured certs. We only read
            # public pages (no credentials), so render them rather than failing the company.
            ignore_https_errors=True,
        )
        page = context.new_page()
        status: int | None = None
        try:
            response = page.goto(url, wait_until=wait_until, timeout=goto_timeout)
            status = response.status if response else None
        except Exception as exc:  # noqa: BLE001 — record render failures, don't crash the run
            browser.close()
            raise RenderError(str(exc)) from exc

        for sel in cfg.get("dismiss_selectors", []):
            try:
                page.click(sel, timeout=3000)
            except Exception:
                pass  # banner may be absent on a given visit

        for sel in cfg.get("expand_selectors", []):
            for el in page.query_selector_all(sel):
                try:
                    el.click(timeout=1500)
                except Exception:
                    pass

        if cfg.get("scroll"):
            # Scroll to the bottom in steps to trigger lazy-loaded pipeline rows, then reset.
            try:
                prev_height = -1
                for _ in range(20):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(400)
                    height = page.evaluate("document.body.scrollHeight")
                    if height == prev_height:
                        break
                    prev_height = height
                page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

        page.wait_for_timeout(wait_ms)

        html = page.content()
        text = page.inner_text("body")
        screenshot = page.screenshot(full_page=cfg.get("full_page", True))
        title = page.title()
        links = sorted({urljoin(url, href) for href in HREF_RE.findall(html)})
        pipeline_image_urls = extract_pipeline_image_urls(url, html)
        browser.close()

    return RenderResult(
        url=url,
        http_status=status,
        html=html,
        text=text,
        screenshot=screenshot,
        meta={
            "title": title,
            "wait_until": wait_until,
            "user_agent": s.browser_user_agent,
            "links": links,
            "pipeline_image_urls": pipeline_image_urls,
        },
    )


class RenderError(RuntimeError):
    pass


def _is_poor_render(result: RenderResult) -> bool:
    """A render is 'poor' when there's no readable pipeline evidence — no phase vocabulary in the
    text and no pipeline image. Mirrors the signal in `validate_pipeline_page`; the usual cause is
    a client-side-rendered (JS) pipeline that Playwright captured before it hydrated."""
    from pipeline_intel.ingest.classify import phase_hits  # noqa: PLC0415 — avoid import cycle

    return phase_hits(result.text) < 3 and not result.meta.get("pipeline_image_urls")


def _firecrawl_render(url: str, wait_ms: int) -> RenderResult | None:
    """Re-render a page via Firecrawl (renders client-side JS to markdown). Returns a text-backed
    RenderResult (no screenshot → routes to text extraction, correct for JS table pages) or None
    when Firecrawl yields nothing (no API key / error / empty)."""
    from pipeline_intel.firecrawl_client import firecrawl_scrape  # noqa: PLC0415 — defer

    md = firecrawl_scrape(url, wait_ms=wait_ms)
    if not md or not md.strip():
        return None
    return RenderResult(
        url=url,
        http_status=200,
        html=md,
        text=md,
        screenshot=b"",
        meta={"render_via": "firecrawl", "title": "", "links": [],
              "pipeline_image_urls": [], "wait_until": "firecrawl"},
    )


# HTTP statuses where the site is BLOCKING the scraper (bot-wall / rate limit / edge throttle),
# not where the page is genuinely missing — worth retrying through Firecrawl, which renders from
# its own infrastructure and often gets past edge bot-blocks (e.g. Vertex 403s a datacenter UA).
_BOT_BLOCK_STATUSES = (401, 403, 429, 503)


def render_with_fallback(
    url: str, render_config: dict | None = None, *, allow_firecrawl: bool = True
) -> RenderResult:
    """Render via Playwright, falling back to Firecrawl when Playwright errors, is bot-blocked, or
    returns a JS-empty page. This single wrapper hardens BOTH discovery (resolver validation) and
    ingest:
      - on RenderError              -> Firecrawl markdown (instead of a terminal failure);
      - on a bot-block (403/429/…)  -> Firecrawl outright (the body is a challenge page, not data);
      - on a poor render            -> Firecrawl, kept only if it surfaces more pipeline text.
    No-ops to plain `render` when the fallback is disabled or no Firecrawl key is configured."""
    from pipeline_intel.ingest.classify import phase_hits  # noqa: PLC0415 — avoid import cycle

    s = settings()
    use_fc = allow_firecrawl and s.firecrawl_render_fallback and bool(s.firecrawl_api_key)
    wait_ms = s.firecrawl_fallback_wait_ms

    try:
        r = render(url, render_config)
    except RenderError:
        if use_fc:
            fb = _firecrawl_render(url, wait_ms)
            if fb is not None:
                return fb
        raise

    if use_fc:
        blocked = r.http_status in _BOT_BLOCK_STATUSES
        if blocked or _is_poor_render(r):
            fb = _firecrawl_render(url, wait_ms)
            # On a bot-block the Playwright body is a challenge/blocked page with no pipeline, so
            # take Firecrawl's render outright; on a merely-poor render keep it only if richer.
            if fb is not None and (blocked or phase_hits(fb.text) > phase_hits(r.text)):
                return fb
    return r


def extract_pipeline_image_urls(base_url: str, html: str) -> list[str]:
    """Return likely pipeline/chart image URLs from generic image metadata.

    This is intentionally artifact-specific, not company-specific: it looks for images
    whose src/alt/title/class/id suggests the image itself is pipeline evidence.
    """
    urls: dict[str, None] = {}
    for tag in IMG_RE.findall(html or ""):
        attrs = {name.lower(): value for name, value in ATTR_RE.findall(tag)}
        src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy-src")
        if not src:
            continue
        haystack = " ".join(
            attrs.get(k, "") for k in ("src", "alt", "title", "class", "id", "data-src", "data-lazy-src")
        ).lower()
        if any(hint in haystack for hint in PIPELINE_IMAGE_HINTS):
            urls[urljoin(base_url, src)] = None
    return sorted(urls)
