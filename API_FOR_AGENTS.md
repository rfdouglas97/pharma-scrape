# Company DB API — access guide

A read-only HTTP API over the Project Rand company database (`companies.duckdb`).
Use it to fetch the biopharma company universe (ticker/name pairs + metadata) and the
catalyst event store. **Read-only** — no writes, no auth, localhost only.

## 1. Start the server (on the host machine, once)

```bash
cd /Users/ryandouglas/Desktop/robinhood
python3 -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Leave it running. Base URL is `http://127.0.0.1:8000`. Interactive docs: `http://127.0.0.1:8000/docs`.

> Note: invoke as `python3 -m uvicorn` (the bare `uvicorn` command may not be on PATH).

## 2. Endpoints

All responses are JSON. List endpoints wrap rows in `{count, limit, offset, results: [...]}`.

| Method & path | Purpose | Query params |
|---|---|---|
| `GET /companies` | List/filter the universe (lean: ticker, name, sector, industry, country, market_cap, last_price, as_of) | `included` (bool, default true), `sector` (str), `q` (substring of name or ticker, case-insensitive), `min_market_cap` (float), `limit` (1–5000, default 100), `offset` |
| `GET /companies/{ticker}` | Full detail for one company + `tags` (therapeutic_area / modality / lead_program) + `description`. Ticker is case-insensitive. Returns **404** if unknown. | — |
| `GET /companies/{ticker}/events` | Catalyst events for one ticker, newest first | — |
| `GET /events` | List/filter the catalyst event store, newest first | `event_type` (e.g. `readout_p3`, `fda_pdufa`), `verified` (bool), `limit`, `offset` |
| `GET /` | Endpoint index | — |

## 3. Example requests

```bash
# Ticker/name pairs for the 50 largest companies in the tradable universe
curl "http://127.0.0.1:8000/companies?limit=50"

# Search by name or ticker
curl "http://127.0.0.1:8000/companies?q=onco&limit=10"

# Full detail for one company (tags + description)
curl "http://127.0.0.1:8000/companies/LLY"

# Phase-3 readout events
curl "http://127.0.0.1:8000/events?event_type=readout_p3&limit=20"
```

Minimal Python client (stdlib only — no installs needed):

```python
import json, urllib.request

BASE = "http://127.0.0.1:8000"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())

# all ticker/name pairs in the universe
for c in get("/companies?limit=5000")["results"]:
    print(c["ticker"], c["name"])

# one company's detail
lly = get("/companies/LLY")
print(lly["name"], lly["tags"]["therapeutic_area"])
```

## 4. Response shapes

`GET /companies` (each item in `results`):
```json
{"company_id": 1, "ticker": "LLY", "name": "Eli Lilly and Company Common Stock",
 "sector": "...", "industry": "...", "country": "...",
 "market_cap": 1047795583042.79, "last_price": 1100.5, "as_of": "2025-06-17"}
```

`GET /companies/{ticker}` adds identity/profile fields plus:
```json
{"...": "...",
 "tags": {"therapeutic_area": ["oncology"], "modality": ["small molecule"], "lead_program": ["..."]},
 "description": "..."}
```

Event objects (`/events`, `/companies/{ticker}/events`):
```json
{"event_id": 1, "event_date": "2025-06-18", "source_ticker": "ABBV",
 "source_company": "...", "source_drug": "...", "event_type": "readout_p3",
 "phase": "P3", "outcome": null, "indication": "...", "market_session": "...",
 "confidence": 0.8, "source_url": "https://...", "verified": false, "source_company_id": 42}
```

## 5. Smoketest (run after starting the server)

```bash
python3 smoketest_api.py                       # defaults to http://127.0.0.1:8000
python3 smoketest_api.py http://127.0.0.1:9000 # or pass a custom base URL
```

Expected output — 7 checks, exit code 0:
```
  PASS  GET / -> 200
  PASS  GET /companies -> rows with ticker/name
  PASS  GET /companies/{ticker} -> detail + tags
  PASS  GET /companies/{ticker} case-insensitive
  PASS  GET /companies/{ticker}/events -> 200
  PASS  GET /events -> 200
  PASS  GET /companies/<unknown> -> 404

7 passed, 0 failed
```

If you get `Connection refused`, the server isn't running — go back to step 1.
```
