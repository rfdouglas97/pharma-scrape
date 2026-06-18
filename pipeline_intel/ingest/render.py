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


@dataclass
class RenderResult:
    url: str
    http_status: int | None
    html: str
    text: str
    screenshot: bytes
    meta: dict = field(default_factory=dict)


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
    s = settings()
    wait_until = cfg.get("wait_until", "networkidle")
    wait_ms = int(cfg.get("wait_ms", 1500))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=s.crawler_user_agent)
        page = context.new_page()
        status: int | None = None
        try:
            response = page.goto(url, wait_until=wait_until, timeout=45000)
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
            "user_agent": s.crawler_user_agent,
            "links": links,
            "pipeline_image_urls": pipeline_image_urls,
        },
    )


class RenderError(RuntimeError):
    pass


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
