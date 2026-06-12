"""§1 LLM rename-adjudication (scoped decision store): cluster same-compound asset names.

Gives the model every distinct BMS asset name seen across 22 quarters and asks it to group
names that refer to the SAME molecule (brand <-> INN <-> dev-code <-> descriptive), conservatively.
Output -> asset_merge.json, consumed by build_history.py as a second identity layer.
"""
from __future__ import annotations
import glob, json, sys, time
sys.path.insert(0, "/Users/ryandouglas/Desktop/pharma_scrape")
from dotenv import load_dotenv; load_dotenv("/Users/ryandouglas/Desktop/pharma_scrape/.env")
from pydantic import BaseModel, Field
from pipeline_intel.extract.client import get_client

BASE = "/Users/ryandouglas/Desktop/pharma_scrape/artifacts/bms_history"

class Cluster(BaseModel):
    canonical: str = Field(description="The clearest name for the compound (prefer brand name if approved, else INN, else dev code)")
    members: list[str] = Field(description="All names in the input list that refer to THIS SAME single molecule")
    confidence: float = Field(description="0-1 confidence these are the same molecule")

class Clusters(BaseModel):
    clusters: list[Cluster] = Field(description="Only clusters with 2+ members (same compound under different names). Singletons omitted.")

def collect_names():
    names = {}
    for f in glob.glob(f"{BASE}/extractions/*.json"):
        for a in json.load(open(f))["result"]["assets"]:
            nm = a["preferred_name"].strip()
            names.setdefault(nm, set()).update(a.get("synonyms", []))
    return names

SYS = """You are a pharma pipeline analyst. You will get a list of asset names from Bristol Myers Squibb's
pipeline page captured across 2021-2026. The same drug is often listed under different names over time:
development code (BMS-986xxx), INN/generic (e.g. nivolumab, deucravacitinib, lisocabtagene maraleucel),
brand (e.g. OPDIVO, SOTYKTU, BREYANZI, ABECMA, AUGTYRO, COBENFY), or a descriptive mechanism placeholder
(e.g. 'TYK2 Inhibitor', 'liso-cel', 'ide-cel').

Group names that refer to the SAME SINGLE MOLECULE. Be CONSERVATIVE:
- Merge only when you are confident it is the same compound (known brand<->INN<->code<->descriptive lineage).
- Do NOT merge two distinct molecules that merely share a target/mechanism (e.g. two different 'BTK Inhibitor's,
  or a monotherapy vs a DIFFERENT compound) unless one is clearly the named successor of the other.
- Do NOT merge combinations with their single agents (e.g. 'OPDIVO + relatlimab' is NOT 'OPDIVO').
- Keep ambiguous generic mechanism-only names as their own singletons (omit them) rather than guessing.
Return only clusters with 2+ members."""

def main():
    names = collect_names()
    listing = "\n".join(f"- {n}" + (f"  [also: {', '.join(sorted(s))}]" if s else "") for n, s in sorted(names.items()))
    client = get_client()
    last = None
    for attempt in range(5):
        try:
            with client.messages.stream(
                model="claude-opus-4-8", max_tokens=16000,
                system=[{"type": "text", "text": SYS, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Cluster these {len(names)} BMS asset names:\n\n{listing}"}],
                output_format=Clusters,
            ) as stream:
                resp = stream.get_final_message()
            res = resp.parsed_output
            break
        except Exception as e:
            last = e; print(f"  attempt {attempt+1} failed: {str(e)[:90]}; backoff", flush=True); time.sleep(12*(attempt+1))
    else:
        raise RuntimeError(f"merge LLM failed: {last}")

    clusters = [c.model_dump() for c in res.clusters if len(c.members) >= 2 and c.confidence >= 0.8]
    json.dump(clusters, open(f"{BASE}/asset_merge.json", "w"), indent=2)
    print(f"{len(names)} distinct names -> {len(clusters)} merge clusters (conf>=0.8, 2+ members)\n")
    for c in clusters:
        print(f"  [{c['confidence']:.2f}] {c['canonical']}  <=  {c['members']}")

if __name__ == "__main__":
    main()
