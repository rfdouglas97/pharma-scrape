"""Render all selected quarterly BMS captures via standard Wayback replay.
Egress blocked to *.archive.org, toolbar stripped, 429-backoff, politeness delay.
"""
from __future__ import annotations
import json, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = "/Users/ryandouglas/Desktop/pharma_scrape/artifacts/bms_history"
RENDERS = f"{BASE}/renders"
import os; os.makedirs(RENDERS, exist_ok=True)
SEL = json.load(open(f"{BASE}/selected_quarters.json"))

STRIP_JS = """() => { document.querySelectorAll('[id^="wm-"], #donato, #wm-ipp-base, #wm-ipp-print, .wb-autocomplete-suggestions').forEach(e => e.remove()); return true; }"""

def host_allowed(url):
    h = urlparse(url).hostname or ""
    return h == "archive.org" or h.endswith("archive.org")

def render_one(p, item):
    ts, original = item["ts"], item["url"]
    replay = f"https://web.archive.org/web/{ts}/{original}"
    blocked = set(); res = {"quarter": item["quarter"], "ts": ts, "captured": item["captured"]}
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent="PipelineIntelBot/0.1 (+experiment; ryanfdoug@gmail.com)")
    page = ctx.new_page()
    def route(r):
        if host_allowed(r.request.url): r.continue_()
        else: blocked.add(urlparse(r.request.url).hostname or "?"); r.abort()
    page.route("**/*", route)
    status = None
    for attempt in range(4):
        try:
            resp = page.goto(replay, wait_until="domcontentloaded", timeout=60000)
            status = resp.status if resp else None
            if status == 429:
                time.sleep(15*(attempt+1)); continue
            break
        except Exception as exc:
            res["render_error"] = str(exc)[:200]; time.sleep(8)
    try: page.wait_for_load_state("networkidle", timeout=20000)
    except Exception: pass
    page.wait_for_timeout(3000)
    try: page.evaluate(STRIP_JS)
    except Exception: pass
    try:
        html, text, png, title = page.content(), page.inner_text("body"), page.screenshot(full_page=True), page.title()
    except Exception as exc:
        res["capture_error"] = str(exc)[:200]; html, text, png, title = "", "", b"", ""
    browser.close()
    open(f"{RENDERS}/{item['quarter']}_{ts}.txt","w").write(text)
    open(f"{RENDERS}/{item['quarter']}_{ts}.png","wb").write(png)
    res.update(http_status=status, text_len=len(text), screenshot_bytes=len(png),
               blocked_live_hosts=sorted(blocked), render_ok=bool(text and len(text)>200))
    return res

def main():
    summary=[]
    with sync_playwright() as p:
        for item in SEL:
            r = render_one(p, item)
            print(f"{r['quarter']}  http={r.get('http_status')}  text_len={r['text_len']:>6}  ok={r['render_ok']}  leaks={len(r['blocked_live_hosts'])}", flush=True)
            summary.append(r); json.dump(summary, open(f"{BASE}/render_summary.json","w"), indent=2)
            time.sleep(6)
    ok = sum(r["render_ok"] for r in summary); leaks = sum(len(r["blocked_live_hosts"]) for r in summary)
    print(f"\nrendered {ok}/{len(summary)} ok; total live-domain leak attempts: {leaks}")

if __name__ == "__main__": main()
