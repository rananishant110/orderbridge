# OrderBridge — Engineering Plan

**Status:** Draft v1
**Last updated:** 2026-04-23

---

## 1. Architecture at a glance

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (Chrome / Safari, desktop only)                     │
│  ┌─────────────────────────┐   ┌────────────────────────┐    │
│  │ Process Order page      │   │ Refresh Catalogs page  │    │
│  │  (index.html + app.js)  │   │  (catalogs.html)       │    │
│  └───────────┬─────────────┘   └──────────┬─────────────┘    │
└──────────────┼───────────────────────────┼──────────────────┘
               │ fetch() multipart / JSON  │
               ▼                           ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI (Python 3.11)                                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ routes/orders.py   — upload, review, apply, download   │  │
│  │ routes/catalogs.py — refresh OneStop & GM templates    │  │
│  │ routes/auth.py     — HTTP Basic guard                  │  │
│  │ services/matching.py — normalize + exact + fuzzy       │  │
│  │ services/excel_reader.py — openpyxl parsers            │  │
│  │ services/excel_writer.py — openpyxl in-place writer    │  │
│  │ db.py              — sqlite3 + migrations              │  │
│  │ main.py            — static mount, CORS, startup       │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌────────────────────────────────┐   ┌────────────────────────┐
│  SQLite file (mappings.db)      │  │  storage/templates/    │
│  mapping, gm_catalog,           │  │  - onestop_active.xlsx │
│  onestop_template, order_run    │  │  - gm_active.xlsx      │
└─────────────────────────────────┘  │  storage/runs/         │
                                     │  - timestamped outputs │
                                     └────────────────────────┘
```

**One deployable:** FastAPI serves JSON under `/api/*` and the static frontend under `/`. No separate web-server or build step.

## 2. Tech stack & dependencies

**Backend**
- Python 3.11+
- FastAPI + Uvicorn
- openpyxl (cell-level read/write, preserves formatting)
- rapidfuzz (fast fuzzy matching — Cython, C++ backend)
- sqlite3 (stdlib)
- pydantic v2 (request/response schemas)
- python-multipart (file upload support)

**Frontend**
- Plain HTML + vanilla JS (no framework, no build step).
- A small CSS file for layout. Fetch API for requests.
- Total ~3 files. Intentional simplicity — this UI will change rarely.

**Dev tooling**
- `pytest` + a tiny fixture of synthetic OneStop/GM xlsx files for unit tests.
- `ruff` for lint, optional.

## 3. Repository layout

```
OrderBridge/
├── PRD.md
├── PLAN.md
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── orderbridge/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── auth.py
│   │   ├── schemas.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── normalize.py
│   │   │   ├── excel_reader.py
│   │   │   ├── excel_writer.py
│   │   │   └── matching.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── orders.py
│   │       └── catalogs.py
│   └── tests/
│       ├── fixtures/
│       ├── test_normalize.py
│       ├── test_matching.py
│       └── test_excel_roundtrip.py
├── frontend/
│   ├── index.html        # Process Order
│   ├── catalogs.html     # Refresh Catalogs
│   ├── app.js
│   └── styles.css
└── storage/
    ├── templates/        # active OneStop + GM .xlsx
    ├── runs/             # archived completed orders
    └── mappings.db       # SQLite
```

## 4. Build phases (shippable checkpoints)

### Phase 1 — CLI prototype (proves the matching logic end-to-end)
- `python -m orderbridge.cli match --onestop filled.xlsx --gm-out out.xlsx`
- Loads active GM template from `storage/`.
- Exact + fuzzy match, writes quantities to GM's ORDER column, preserves formatting.
- No DB yet — in-memory run.
- **Exit criterion:** manually diff output .xlsx against input GM template; only ORDER cells should differ.

### Phase 2 — Persistence layer
- SQLite schema (`mapping`, `gm_catalog`, `onestop_template`, `order_run`).
- Migrations on startup (create tables if not present).
- CLI gains `--use-mappings`, writes confirmed auto-matches into `mapping` on first run to bootstrap.

### Phase 3 — FastAPI wrapper
- `/api/orders/upload` → returns match buckets as JSON.
- `/api/orders/apply` → takes confirmed resolutions, writes file, returns download URL.
- `/api/catalogs/refresh` → accepts new OneStop + GM, returns diff.
- `/api/catalogs/confirm` → applies mapping decisions.
- HTTP Basic auth on all `/api/*` routes.

### Phase 4 — Frontend
- Two pages. Fetch-based. No framework. Aim: obvious to use, fast to load.
- Review bucket rendered as an HTML table; each row has a GM item# dropdown (populated lazily via `/api/catalogs/search?q=`).

### Phase 5 — Deployment
- Dockerfile (python:3.11-slim base, uvicorn entry).
- Deploy to Render.com $7 tier with a persistent disk mounted at `/app/storage`.
- Subdomain + Let's Encrypt (Render handles both).
- Daily cron (Render cron job) that `sqlite3 .dump`s the mapping DB to a backup folder.

### Phase 6 (optional, post-MVP)
- Gmail poller (separate service or n8n on top).
- Quantity anomaly warnings.
- Order history page.

## 5. Milestones & rough estimates

| Phase | Estimate | Delivers |
|-------|----------|----------|
| 1 — CLI prototype | half a day | Correct xlsx output for a real order |
| 2 — SQLite layer  | half a day | Learning persists across runs |
| 3 — FastAPI       | 1 day      | All endpoints live and testable with curl |
| 4 — Frontend      | 1 day      | End-user flow usable in a browser |
| 5 — Deploy        | 2 hours    | Public URL with auth |

**Critical path:** Phase 1 (excel_writer preserving formatting) is the single biggest risk. Everything else is routine.

## 6. Key design decisions

### 6.1 openpyxl over pandas
pandas discards styles on write. openpyxl keeps them because we open-and-modify the actual template `.xlsx` rather than constructing one from data. The writer **never creates a new workbook** — it loads the active GM template, writes to specific cells, saves under a new filename.

### 6.2 Normalization rules
One function, one place: `services/normalize.py`.
```
normalize(s) = re.sub(r"\s+", " ",
                re.sub(r"[^\w\s]", " ", s.upper())).strip()
```
Anything else (abbreviation expansion, stopword removal) adds lookup misses — we resist it unless match-rate data says otherwise.

### 6.3 Pack-size guard
Regex `r"\b\d+[Xx]\d+\s*[A-Za-z]+\b"` extracts tokens like `12X1G`, `20X500G`. If OneStop row has one, GM candidate must have the same (case-insensitive) or match drops to review bucket.

### 6.4 REST LIST handling
Locked decision (PRD §8): always write to specialized sheet, skip REST LIST. Implementation: when building `gm_catalog`, record sheet. When a match resolves to an item# that exists on both, prefer the non-`REST LIST` row.

### 6.5 Confidence bands
- ≥0.92 → auto (score from rapidfuzz.token_set_ratio / 100)
- 0.75–0.92 → review
- <0.75 → no-match / OneStop-exclusive

These thresholds are config constants, tunable after first week of real use.

### 6.6 OneStop catalog is *not* canonical
We only persist OneStop descriptions that have been *seen* in the active template. When a new template is uploaded, we diff against last week's and surface new / removed rows.

## 7. Testing strategy

- **Unit:** `normalize`, pack-size extractor, matching function with a synthetic GM index.
- **Integration:** load real OneStop + GM fixtures, run end-to-end match, assert
  - exact-match count matches baseline (~74%)
  - no duplicate writes to REST LIST
  - output workbook's styles/dimensions match input
- **Manual:** one real order each of the first few weeks, visually confirm with ops.

## 8. Deployment plan

- **Host:** Render.com web service.
- **Disk:** 1 GB persistent volume mounted at `/app/storage`.
- **Env vars:** `OB_USER`, `OB_PASS`, `OB_SECRET` (for session cookie if we add one).
- **Domain:** CNAME to Render, free TLS.
- **Backups:** daily cron dumps `mappings.db` to a separate backup folder; optionally rclone to Backblaze B2 once volume justifies it.
- **Observability:** stdout logs via Render dashboard (enough for a 1–5 user tool). Add Sentry only if we start seeing silent failures.

## 9. Risks & mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| openpyxl corrupts some GM formatting (merged cells, conditional formatting) | High — GM rejects file | Phase 1 manual diff; fall back to xlsxwriter+template copy if needed |
| Fuzzy matcher produces confident-wrong results | Med — wrong item# ordered | Pack-size guard + conservative 0.92 threshold + review bucket |
| Weekly catalog churn breaks stored mappings | Med — staff re-review floods | Mapping keyed on `onestop_desc_normalized`, not item#; GM-side churn auto-detected via diff screen |
| Staff skip review and apply a bad match | Med | Review bucket is default-blocked; Apply button only enabled once every row has a resolution |
| SQLite corruption | High — lose learned mappings | Daily dump + WAL mode + off-box backup |

## 10. Open technical questions

1. Does GM's template have macros, conditional formatting, or charts that openpyxl would silently drop? (Check on the real file in Phase 1.)
2. Are there any OneStop rows where column A already contains non-numeric values we should ignore (e.g. notes)? (Defensive parsing.)
3. Is there a stable item# key on the GM side we can use as a tiebreaker when a description matches two different item#s on different sheets?

## 11. What we are explicitly *not* building

- A pricing engine, margin calculator, or invoice generator.
- Integration with GM's systems beyond handing staff a file.
- Anything that modifies the OneStop customer's original upload.
- A mapping editor for arbitrary cross-references between suppliers.
