"""Chronological replay: 22 quarterly BMS extractions -> a longitudinal pipeline history
+ change-event stream, applying the §3a semantics from WAYBACK_BACKFILL_PLAN.md.

PRIMARY OUTPUT = ASSET-LEVEL (trustworthy):
  - identity resolution (name-variant collapse + dev-code anchoring) gives stable assets
  - phase normalization gives a per-asset phase trajectory (max phase across its indications)
  - §3a replay: cold-start baseline (R6), bad-capture guard (§2), 2-quarter confirmation (R4),
    interval dates (R5)
Program-level (asset x indication) is NOT headlined: indication wording drifts heavily across
captures (OPDIVO alone shows 93 distinct indication strings), so a clean program-level feed needs
ontology mapping (the M4 enrich layer, not run here). We report its size but flag it as approximate.
No DB, no schema.
"""
from __future__ import annotations
import glob, json, os, re
from datetime import datetime
import yaml

BASE = "/Users/ryandouglas/Desktop/pharma_scrape/artifacts/bms_history"
EXTR = f"{BASE}/extractions"
PHASE_YAML = "/Users/ryandouglas/Desktop/pharma_scrape/config/vocab/phase.yaml"

# ---------- phase normalization (mirror of normalize/vocab.py, DB-free) ----------
_PARENS = re.compile(r"\([^)]*\)")
_FOOTNOTE = re.compile(r"[*#†‡§✝~^✦∗◆]+")
_NOISE = re.compile(r"\b(in progress|achieved|ongoing|status|completed)\b")
_WS = re.compile(r"\s+")
_pv = yaml.safe_load(open(PHASE_YAML))
ALIAS2CODE, CODE2LABEL, CODE2ORDER = {}, {}, {}
for ph in _pv["phases"]:
    CODE2LABEL[ph["code"]] = ph["label"]; CODE2ORDER[ph["code"]] = ph["sort_order"]
    for a in ph["aliases"]:
        ALIAS2CODE[a] = ph["code"]

def preclean(v):
    v = (v or "").lower(); v = _PARENS.sub(" ", v); v = _FOOTNOTE.sub(" ", v); v = _NOISE.sub(" ", v)
    return _WS.sub(" ", v).strip()

def norm_phase(verbatim):
    key = preclean(verbatim)
    if not key: return None, None
    if key in ALIAS2CODE: return ALIAS2CODE[key], CODE2LABEL[ALIAS2CODE[key]]
    for alias, code in ALIAS2CODE.items():
        if key.startswith(alias): return code, CODE2LABEL[code]
    return None, key

# ---------- asset identity resolution (scoped decision store) ----------
_DEVCODE = re.compile(r"\b(?:BMS|CC|JNJ|ONO|CD)[-\s]?\d{3,}\b", re.I)
_SYM = re.compile(r"[®™✦^#*∗◆†‡§~]+")

def clean_name(n):
    base = _SYM.sub("", n); base = _PARENS.sub(" ", base)
    base = re.sub(r"[^a-z0-9 ]", " ", base.lower()); return _WS.sub("", base).strip()

def dev_codes(*names):
    out = set()
    for n in names:
        for m in _DEVCODE.findall(n or ""): out.add(re.sub(r"[-\s]", "", m).upper())
    return out

class Identity:
    def __init__(self):
        self.core2cid, self.code2cid, self.cid_names, self.cid_codes = {}, {}, {}, {}; self._next = 0
    def _new(self):
        c = self._next; self._next += 1; self.cid_names[c] = {}; self.cid_codes[c] = set(); return c
    def resolve(self, preferred, synonyms):
        names = [preferred] + list(synonyms or []); core = clean_name(preferred); codes = dev_codes(*names)
        cid = None
        for code in codes:
            if code in self.code2cid: cid = self.code2cid[code]; break
        if cid is None and core in self.core2cid: cid = self.core2cid[core]
        if cid is None: cid = self._new()
        self.core2cid.setdefault(core, cid)
        for code in codes: self.code2cid.setdefault(code, cid); self.cid_codes[cid].add(code)
        self.cid_names[cid][preferred] = self.cid_names[cid].get(preferred, 0) + 1
        return cid
    def display(self, cid):
        names = self.cid_names[cid]
        branded = [n for n in names if re.search(r"[A-Z]{4,}", n) or "®" in n or "™" in n]
        pool = branded or list(names); return max(pool, key=lambda n: (names[n], len(n)))

def norm_ind(v):
    v = _SYM.sub("", v or ""); v = re.sub(r"[^a-z0-9 ]", " ", v.lower()); return _WS.sub(" ", v).strip()

_PARTNER_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|pharmaceuticals|pharmaceutical|"
                             r"pharma|therapeutics|biopharma|biosciences|bioscience|sa|ag|gmbh|plc|nv|"
                             r"monoclonals|a johnson . johnson company)\b\.?", re.I)
_PARTNER_ALIAS = {"janssen": "johnson johnson", "j j": "johnson johnson", "gemoab": "avencell"}
def norm_partner(name):
    """Collapse corporate-suffix/formatting noise (Janssen == 'Janssen Pharmaceuticals, Inc.') but
    KEEP genuinely different companies distinct (Acceleron != Merck), so real handoffs survive."""
    n = _PARENS.sub(" ", name or ""); n = _PARTNER_SUFFIX.sub(" ", n.lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n); n = _WS.sub(" ", n).strip()
    return _PARTNER_ALIAS.get(n, n)

def median(xs):
    xs = sorted(xs); n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1] + xs[n//2]) / 2

def exit_class(phase_code):
    """A drug leaving the pipeline page is AMBIGUOUS: approved-and-graduated vs discontinued vs
    rename. The pipeline page alone can't disambiguate; exit phase is the best available signal.
    Disambiguating fully needs external approval/press data (generalizes plan §4)."""
    if phase_code in ("filed", "approved"):
        return "likely_approved_graduated"       # reached Filed then left -> almost certainly approved
    if phase_code == "phase_3":
        return "late_stage_exit_ambiguous"        # approval OR Phase 3 failure — needs external signal
    return "likely_discontinued_early"            # Phase 1/2/preclinical exit -> usually deprioritized

def load_quarters():
    qs = [json.load(open(f)) for f in glob.glob(f"{EXTR}/*.json")]
    qs.sort(key=lambda d: datetime.strptime(d["captured"], "%Y-%m-%d")); return qs

# ---------- replay (asset-level) ----------
def build():
    quarters = load_quarters()
    ident = Identity()
    for d in quarters:
        for a in d["result"]["assets"]:
            ident.resolve(a["preferred_name"], a.get("synonyms", []))

    # §1 rename-adjudication layer (decision store): LLM clusters if present, else curated map.
    # Composes ON TOP of deterministic identity: union cids that fall in the same cluster.
    merge_path = f"{BASE}/asset_merge.json" if os.path.exists(f"{BASE}/asset_merge.json") else f"{BASE}/curated_aliases.json"
    merge_source = os.path.basename(merge_path)
    clusters = json.load(open(merge_path)) if os.path.exists(merge_path) else []
    core2canon = {}
    for c in clusters:
        for m in c["members"]:
            core2canon[clean_name(m)] = c["canonical"]
    cid2super, super_display = {}, {}
    for cid in range(ident._next):
        cores = {clean_name(n) for n in ident.cid_names[cid]}
        canon = next((core2canon[c] for c in cores if c in core2canon), None)
        sk = ("a", canon) if canon else ("c", cid)
        cid2super[cid] = sk
        super_display[sk] = canon if canon else ident.display(cid)
    n_super = len(set(cid2super.values()))

    partner_disp = {}  # normalized key -> a clean display name

    def assets_of(d):
        """quarter -> {super_key: {asset, max_phase_code, max_phase_label, indications:set, partners:set, n_programs}}"""
        out = {}
        for a in d["result"]["assets"]:
            cid = cid2super[ident.resolve(a["preferred_name"], a.get("synonyms", []))]
            partners = set()
            for p in a.get("partners", []):
                k = norm_partner(p["name"])
                if not k: continue
                partners.add(k)
                if k not in partner_disp or len(p["name"]) < len(partner_disp[k]):
                    partner_disp[k] = p["name"]  # prefer the shortest clean original
            rec = out.setdefault(cid, {"asset": super_display[cid], "max_phase_code": None,
                                       "max_phase_label": None, "indications": set(),
                                       "partners": set(), "n_programs": 0})
            rec["partners"] |= partners
            for p in a["programs"]:
                rec["n_programs"] += 1
                rec["indications"].add(norm_ind(p["indication_verbatim"]))
                code, label = norm_phase(p["phase_verbatim"])
                if code and CODE2ORDER.get(code, 0) > CODE2ORDER.get(rec["max_phase_code"], -1):
                    rec["max_phase_code"], rec["max_phase_label"] = code, label
        return out

    qsets = [(d["quarter"], d["captured"], assets_of(d)) for d in quarters]

    events, state, quarantined = [], {}, []
    def add(**kw): events.append(kw)

    # R6 cold-start baseline
    bq, bdate, bmap = qsets[0]
    for cid, rec in bmap.items():
        state[cid] = {**rec, "first_seen_q": bq, "last_seen_q": bq, "last_seen_date": bdate,
                      "open": True, "absent_streak": 0}
    accepted = [len(bmap)]
    DROP = 0.6

    # naive asset churn (raw preferred_name strings, no identity) for comparison
    def naive_names(d):
        return {a["preferred_name"].strip().lower() for a in d["result"]["assets"]}
    naive_churn, prev_naive = 0, naive_names(quarters[0])

    for i in range(1, len(qsets)):
        q, qdate, cur = qsets[i]
        cn = naive_names(quarters[i]); naive_churn += len(cn - prev_naive) + len(prev_naive - cn); prev_naive = cn

        trail = median(accepted[-4:])
        if 0 < len(cur) < DROP * trail:
            quarantined.append({"quarter": q, "assets": len(cur), "trail_median": trail}); continue
        accepted.append(len(cur))

        cur_ids = set(cur)
        for cid, rec in cur.items():
            st = state.get(cid)
            if st is None:
                add(type="asset_added", asset=rec["asset"], phase=rec["max_phase_label"],
                    n_indications=len(rec["indications"]), quarter=q, eff_min=None, eff_max=qdate, status="confirmed")
                state[cid] = {**rec, "first_seen_q": q, "last_seen_q": q, "last_seen_date": qdate, "open": True, "absent_streak": 0}
            elif not st["open"]:
                add(type="asset_reappeared", asset=rec["asset"], phase=rec["max_phase_label"],
                    quarter=q, eff_min=st["last_seen_date"], eff_max=qdate, status="confirmed")
                st.update(**rec, last_seen_q=q, last_seen_date=qdate, open=True, absent_streak=0)
            else:
                if rec["max_phase_code"] != st["max_phase_code"] and rec["max_phase_code"] is not None:
                    o, n = CODE2ORDER.get(st["max_phase_code"], 0), CODE2ORDER.get(rec["max_phase_code"], 0)
                    add(type="asset_phase_changed", direction="advance" if n > o else ("regress" if n < o else "lateral"),
                        asset=rec["asset"], from_phase=st["max_phase_label"], to_phase=rec["max_phase_label"],
                        quarter=q, eff_min=st["last_seen_date"], eff_max=qdate, status="confirmed")
                # partner diffs
                for added in rec["partners"] - st["partners"]:
                    add(type="partner_added", asset=rec["asset"], partner=partner_disp.get(added, added), quarter=q, eff_min=None, eff_max=qdate, status="confirmed")
                for dropped in st["partners"] - rec["partners"]:
                    add(type="partner_removed", asset=rec["asset"], partner=partner_disp.get(dropped, dropped), quarter=q, eff_min=None, eff_max=qdate, status="confirmed")
                st.update(max_phase_code=rec["max_phase_code"], max_phase_label=rec["max_phase_label"],
                          partners=rec["partners"], indications=rec["indications"], n_programs=rec["n_programs"],
                          last_seen_q=q, last_seen_date=qdate, absent_streak=0)

        for cid, st in list(state.items()):
            if not st["open"] or cid in cur_ids: continue
            st["absent_streak"] += 1
            if st["absent_streak"] == 1:
                st["_fa_q"], st["_fa_date"] = q, qdate
            elif st["absent_streak"] >= 2:
                add(type="asset_left_pipeline", asset=st["asset"], last_phase=st["max_phase_label"],
                    last_phase_code=st["max_phase_code"], exit_class=exit_class(st["max_phase_code"]),
                    quarter=st.get("_fa_q", q), eff_min=st["last_seen_date"], eff_max=st.get("_fa_date", qdate), status="confirmed")
                st["open"] = False

    for cid, st in state.items():
        if st["open"] and st["absent_streak"] == 1:
            add(type="asset_left_pipeline", asset=st["asset"], last_phase=st["max_phase_label"],
                last_phase_code=st["max_phase_code"], exit_class=exit_class(st["max_phase_code"]),
                quarter=st.get("_fa_q"), eff_min=st["last_seen_date"], eff_max=st.get("_fa_date"), status="provisional")

    # asset phase timeline
    timeline = {}
    for q, qdate, cur in qsets:
        for cid, rec in cur.items():
            timeline.setdefault(rec["asset"], {})[q] = rec["max_phase_label"]

    prog_level = sum(len({(cid, ind) for cid, r in cur.items() for ind in r["indications"]}) for _, _, cur in qsets)
    stats = {
        "window": f"{qsets[0][0]}–{qsets[-1][0]}",
        "quarters_total": len(qsets), "quarantined": quarantined,
        "raw_assets_per_quarter": {d["quarter"]: len(d["result"]["assets"]) for d in quarters},
        "canonical_assets_per_quarter": {q: len(cur) for q, _, cur in qsets},
        "distinct_names_raw": len({a["preferred_name"].strip() for d in quarters for a in d["result"]["assets"]}),
        "assets_after_deterministic_identity": ident._next,
        "assets_after_rename_merge": n_super,
        "merge_source": merge_source,
        "naive_asset_churn_events": naive_churn,
        "resolved_asset_events": len(events),
        "events_by_type": {}, "program_level_rows_note": "indication-level feed deferred to ontology mapping",
    }
    for e in events: stats["events_by_type"][e["type"]] = stats["events_by_type"].get(e["type"], 0) + 1

    json.dump(events, open(f"{BASE}/change_events.json", "w"), indent=2, default=str)
    json.dump(timeline, open(f"{BASE}/timeline.json", "w"), indent=2, default=str)
    json.dump(stats, open(f"{BASE}/stats.json", "w"), indent=2, default=str)
    write_report(events, timeline, stats, qsets)
    print(json.dumps({k: v for k, v in stats.items() if k != "raw_assets_per_quarter"}, indent=2, default=str))

def write_report(events, timeline, stats, qsets):
    red = 100 * (1 - stats["resolved_asset_events"]/max(stats["naive_asset_churn_events"], 1))
    left = [e for e in events if e["type"]=="asset_left_pipeline" and e["status"]=="confirmed"]
    by_exit = {"likely_approved_graduated": [], "late_stage_exit_ambiguous": [], "likely_discontinued_early": []}
    for e in left: by_exit[e["exit_class"]].append(e)
    L = []
    L += ["# BMS Pipeline — 5-Year Longitudinal History (2021 Q1 – 2026 Q2)\n",
          "Reconstructed from **22 quarterly Wayback captures** of bms.com's pipeline page → standard Wayback",
          "replay (zero live-domain contamination) → Opus 4.8 vision extraction → chronological replay (§3a)",
          f"with asset identity resolution ({stats['distinct_names_raw']} raw names → "
          f"{stats['assets_after_deterministic_identity']} deterministic → {stats['assets_after_rename_merge']} after",
          f"rename-merge via `{stats['merge_source']}`) + phase normalization.\n",
          "## ⚠️ Headline finding: 'disappeared from page' ≠ 'discontinued'\n",
          "A drug leaving the pipeline page is **three-way ambiguous** — it may have been **approved & graduated**",
          "off the page (BMS's page shows only investigational assets), **discontinued**, or **renamed**. The page",
          "alone cannot disambiguate. Known BMS approvals (SOTYKTU '22, CAMZYOS '22, AUGTYRO '23, COBENFY '24) do",
          "**not** appear as advances to *Approved* — they simply vanish, because approved indications leave the page.",
          "**Implication for the feed:** additions and in-pipeline phase advances are trustworthy from this source;",
          "exits require an external approval/press signal to classify (generalizes plan §4). Below, exits are",
          "split by exit phase as the best available proxy.\n",
          "## Trustworthy signals\n",
          f"- **New assets entering pipeline:** {sum(1 for e in events if e['type']=='asset_added')}",
          f"- **In-pipeline phase advances:** {sum(1 for e in events if e['type']=='asset_phase_changed' and e.get('direction')=='advance')}",
          f"- **Partner changes:** {sum(1 for e in events if e['type'] in ('partner_added','partner_removed'))}\n",
          "## Ambiguous signal — assets that LEFT the pipeline (by exit phase)\n",
          f"- **Reached Filed → left (almost certainly APPROVED & graduated):** {len(by_exit['likely_approved_graduated'])}",
          f"- **Left from Phase 3 (approval OR failure — needs external signal):** {len(by_exit['late_stage_exit_ambiguous'])}",
          f"- **Left from Phase 1/2 (usually discontinued/deprioritized):** {len(by_exit['likely_discontinued_early'])}\n",
          "## Noise reduction (why the identity + normalization layers matter)\n",
          f"- Naive churn (raw names, no identity/phase-norm): **{stats['naive_asset_churn_events']}** add/remove events",
          f"- After identity resolution + rename-merge + phase-norm: **{stats['resolved_asset_events']}** events",
          f"- **~{red:.0f}% of naive churn was name-variant / phase-wording noise.**\n",
          "> The rename-merge map is hand-curated (`curated_aliases.json`) as a stand-in for the LLM clustering",
          "> step (`merge_assets.py`), which was blocked tonight by an API usage limit. Residual rename noise remains.\n"]
    if stats["quarantined"]:
        L.append("## Data-quality guard (bad-capture)\n")
        L.append(f"{len(stats['quarantined'])} of {stats['quarters_total']} captures were quarantined (anomalously low")
        L.append("extraction vs trailing median) and carried forward — no false events emitted:")
        for qq in stats["quarantined"]:
            L.append(f"- **{qq['quarter']}**: {qq['assets']} assets vs trailing median {qq['trail_median']:.0f}")
        L.append("")
    L.append("## Pipeline size over time\n| Quarter | raw assets | canonical (merged) |\n|---|---|---|")
    for q, _, cur in qsets:
        flag = " *(quarantined)*" if any(z['quarter']==q for z in stats['quarantined']) else ""
        L.append(f"| {q} | {stats['raw_assets_per_quarter'].get(q,'-')} | {len(cur)}{flag} |")
    L.append("\n> The 2021 chart-format captures over-segment combination/variant rows vs BMS's stated '50+ compounds';")
    L.append("> the 2021→2025 decline is partly real streamlining, partly extraction-format normalization.\n")
    L.append("## In-pipeline phase advances (trustworthy)\n")
    for e in [e for e in events if e["type"]=="asset_phase_changed" and e.get("direction")=="advance"]:
        L.append(f"- {e['quarter']}: **{e['asset']}** {e['from_phase']} → {e['to_phase']}")
    L.append("\n## New assets entering the pipeline (trustworthy)\n")
    for e in [e for e in events if e["type"]=="asset_added"]:
        L.append(f"- {e['quarter']}: **{e['asset']}** @ {e['phase']} ({e['n_indications']} indication(s))")
    L.append("\n## Left from Filed — almost certainly APPROVED & graduated\n")
    for e in by_exit["likely_approved_graduated"]:
        L.append(f"- ~{e['eff_min']}…{e['eff_max']}: **{e['asset']}** (was {e['last_phase']})")
    L.append("\n## Left from Phase 3 — AMBIGUOUS (approval or failure)\n")
    for e in by_exit["late_stage_exit_ambiguous"]:
        L.append(f"- ~{e['eff_min']}…{e['eff_max']}: **{e['asset']}**")
    L.append("\n## Left from Phase 1/2 — likely discontinued / deprioritized\n")
    for e in by_exit["likely_discontinued_early"]:
        L.append(f"- ~{e['eff_min']}…{e['eff_max']}: **{e['asset']}** (was {e['last_phase']})")
    L.append("\n## Partner changes\n")
    for e in [e for e in events if e["type"] in ("partner_added","partner_removed")]:
        arrow = "＋" if e["type"]=="partner_added" else "－"
        L.append(f"- {e['quarter']}: {arrow} **{e['asset']}** — {e['partner']}")
    open(f"{BASE}/HISTORY_REPORT.md", "w").write("\n".join(L))

if __name__ == "__main__":
    build()
