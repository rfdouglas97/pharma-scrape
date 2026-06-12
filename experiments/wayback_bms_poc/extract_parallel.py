"""Parallel extraction of remaining quarterly BMS captures (skips cached)."""
from __future__ import annotations
import json, os, sys, glob
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/Users/ryandouglas/Desktop/pharma_scrape")
from dotenv import load_dotenv
load_dotenv("/Users/ryandouglas/Desktop/pharma_scrape/.env")
from pipeline_intel.extract.extractor import run_extraction

BASE = "/Users/ryandouglas/Desktop/pharma_scrape/artifacts/bms_history"
RENDERS, EXTR = f"{BASE}/renders", f"{BASE}/extractions"
os.makedirs(EXTR, exist_ok=True)
URL = "https://www.bms.com/researchers-and-partners/in-the-pipeline.html"
SEL = json.load(open(f"{BASE}/selected_quarters.json"))

def one(item):
    q, ts = item["quarter"], item["ts"]
    out_path = f"{EXTR}/{q}.json"
    if os.path.exists(out_path):
        return f"{q}: cached"
    txt_f, png_f = f"{RENDERS}/{q}_{ts}.txt", f"{RENDERS}/{q}_{ts}.png"
    if not os.path.exists(txt_f):
        return f"{q}: no render"
    page_text, png = open(txt_f).read(), open(png_f, "rb").read()
    try:
        result, usage, stop = run_extraction("Bristol Myers Squibb", URL, page_text, [png])
        json.dump({"quarter": q, "ts": ts, "captured": item["captured"],
                   "result": result.model_dump(), "usage": usage, "stop_reason": stop},
                  open(out_path, "w"), indent=2, default=str)
        return f"{q}: assets={len(result.assets)} programs={sum(len(a.programs) for a in result.assets)} stop={stop}"
    except Exception as exc:
        return f"{q}: ERROR {str(exc)[:160]}"

def main():
    todo = [it for it in SEL if not os.path.exists(f"{EXTR}/{it['quarter']}.json")]
    print(f"extracting {len(todo)} remaining quarters with 5 workers", flush=True)
    with ThreadPoolExecutor(max_workers=5) as ex:
        for fut in as_completed([ex.submit(one, it) for it in todo]):
            print(fut.result(), flush=True)
    print(f"\nextracted {len(glob.glob(f'{EXTR}/*.json'))}/{len(SEL)} quarters", flush=True)

if __name__ == "__main__": main()
