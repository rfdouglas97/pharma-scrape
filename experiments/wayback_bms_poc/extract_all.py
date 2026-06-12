"""Extract all rendered quarterly BMS captures with the real Opus 4.8 vision extractor.
Saves one JSON per quarter to artifacts/bms_history/extractions/.
"""
from __future__ import annotations
import json, os, sys, glob
sys.path.insert(0, "/Users/ryandouglas/Desktop/pharma_scrape")
from dotenv import load_dotenv
load_dotenv("/Users/ryandouglas/Desktop/pharma_scrape/.env")
from pipeline_intel.extract.extractor import run_extraction

BASE = "/Users/ryandouglas/Desktop/pharma_scrape/artifacts/bms_history"
RENDERS, EXTR = f"{BASE}/renders", f"{BASE}/extractions"
os.makedirs(EXTR, exist_ok=True)
URL = "https://www.bms.com/researchers-and-partners/in-the-pipeline.html"
SEL = json.load(open(f"{BASE}/selected_quarters.json"))

def main():
    for item in SEL:
        q, ts = item["quarter"], item["ts"]
        out_path = f"{EXTR}/{q}.json"
        if os.path.exists(out_path):
            print(f"{q}: cached, skip", flush=True); continue
        txt_f = f"{RENDERS}/{q}_{ts}.txt"; png_f = f"{RENDERS}/{q}_{ts}.png"
        if not os.path.exists(txt_f):
            print(f"{q}: no render, skip", flush=True); continue
        page_text = open(txt_f).read(); png = open(png_f, "rb").read()
        try:
            result, usage, stop = run_extraction("Bristol Myers Squibb", URL, page_text, [png])
            n_ass = len(result.assets); n_prog = sum(len(a.programs) for a in result.assets)
            json.dump({"quarter": q, "ts": ts, "captured": item["captured"],
                       "result": result.model_dump(), "usage": usage, "stop_reason": stop},
                      open(out_path, "w"), indent=2, default=str)
            print(f"{q}: assets={n_ass} programs={n_prog} stop={stop} out_tok={usage.get('output_tokens')}", flush=True)
        except Exception as exc:
            print(f"{q}: ERROR {str(exc)[:200]}", flush=True)
    print(f"\nextracted {len(glob.glob(f'{EXTR}/*.json'))}/{len(SEL)} quarters")

if __name__ == "__main__": main()
