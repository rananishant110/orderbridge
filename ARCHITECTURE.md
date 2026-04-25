# OrderBridge — Architecture

## System Overview

OrderBridge is a **single-process, single-tenant** internal tool. One FastAPI process serves both the JSON API (`/api/*`) and the static frontend (`/`). There is no separate frontend build step, no message queue, no cache layer, and no external services — by design.

```
┌─────────────────────────────────────────────────────────┐
│  Browser (desktop Chrome / Safari)                       │
│  Alpine.js + fetch()                                     │
│  index.html / app.js / styles.css                        │
└──────────────────────────┬──────────────────────────────┘
                           │  HTTP (multipart / JSON)
                           ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI / Uvicorn  (Python 3.11)                        │
│                                                          │
│  routes/orders.py   — upload · apply · download · history│
│  routes/catalogs.py — refresh · search · status · gm    │
│                                                          │
│  services/matching.py   ← core match engine             │
│  services/excel_reader.py                               │
│  services/excel_writer.py                               │
│  services/normalize.py                                  │
│                                                          │
│  db.py — sqlite3 WAL, schema-on-startup                 │
│  config.py — all tunables                               │
└───────┬──────────────────────────┬──────────────────────┘
        │                          │
        ▼                          ▼
┌──────────────────┐   ┌────────────────────────────────┐
│  SQLite           │   │  Filesystem (storage/)          │
│  mappings.db      │   │  templates/onestop_active.xlsx  │
│                   │   │  templates/gm_active.xlsx       │
│  mapping          │   │  runs/<run_id>__<filename>.xlsx │
│  gm_catalog       │   │  runs/gm_order_<ts>_<id>.xlsx  │
│  onestop_template │   └────────────────────────────────┘
│  order_run        │
└──────────────────┘
```

---

## Component Responsibilities

### `main.py` — App Factory
Creates the FastAPI app, calls `config.ensure_dirs()` and `db.init_schema()` on startup, registers routers, and mounts the static frontend directory at `/`. The frontend files are served directly by Starlette's `StaticFiles`.

### `config.py` — Configuration Singleton
All runtime configuration in one place: directory paths, matching thresholds, auth credentials, sheet name constants. The rest of the code imports from here rather than using `os.environ` directly.

### `db.py` — Database Layer
Plain `sqlite3` with WAL mode. Schema is defined as a SQL string and applied via `executescript` on every startup (idempotent `CREATE TABLE IF NOT EXISTS`). No ORM, no migration framework. The `session()` context manager opens and closes a connection per request.

### `auth.py` — HTTP Basic Auth
FastAPI `Depends`-injectable `require_user()` function. Compares credentials using `secrets.compare_digest` to resist timing attacks. Currently **not wired into routers** (routes use `LOCAL_USER = "local"`).

### `schemas.py` — API Contract
Pydantic v2 models for all request/response bodies. Never import these in services — they belong to the HTTP boundary only.

### `routes/orders.py` — Order Processing
- `POST /api/orders/upload` — saves the uploaded file, runs match pipeline, returns three buckets (auto/review/unmatched), stores staging data in memory.
- `POST /api/orders/apply` — takes user resolutions, writes quantities into GM template copy, persists new learned mappings, records run in `order_run`.
- `GET /api/orders/download/{filename}` — serves output files with path-traversal guard.
- `GET /api/orders/history` — last N runs from `order_run`.

### `routes/catalogs.py` — Catalog Management
- `POST /api/catalogs/refresh` — saves both templates, re-indexes `gm_catalog` and `onestop_template`, returns a diff of what changed.
- `GET /api/catalogs/search?q=` — free-text GM catalog search for the command palette.
- `GET /api/catalogs/status` — template presence + modification times (used by the header freshness pills).
- `GET /api/catalogs/gm` — full GM catalog grouped by sheet (loads the right-hand pane).

### `services/normalize.py` — Text Normalization
Single function `normalize(text)`: uppercase → strip punctuation → collapse whitespace. Also exposes `extract_pack_size()` and `pack_sizes_compatible()`. All matching uses this normalization so that OneStop and GM descriptions compare consistently.

### `services/excel_reader.py` — Excel Parsers (Read-Only)
Two parsers:
- `read_onestop(path)` — reads the `Report` sheet. Columns: A=qty, B=description, C=price. Returns `list[OnestopRow]`.
- `read_gm_catalog(path)` — reads all known product sheets (8 named). Each sheet has a two-column side-by-side grid (left: A–D, right: F–I). Returns `list[GmRow]`.

Both use `load_workbook(read_only=True, data_only=True)` — they never hold the file open for modification.

### `services/excel_writer.py` — Excel Writer
`write_quantities(template_path, output_path, writes)`:
1. `shutil.copyfile(template → output)` — start from a fresh copy to keep the template pristine.
2. Load output with `load_workbook()` (NOT read-only).
3. For each `OrderWrite`, sanity-check the item# in the expected cell before writing the ORDER cell.
4. Save. Returns count of cells written.

The ORDER column is C (left block) or H (right block), as defined in `ORDER_COL`.

### `services/matching.py` — Match Engine
`GmIndex` builds three lookup structures from `list[GmRow]`:
- `_by_norm` — description → rows (for exact and fuzzy lookup)
- `_by_item` — item# → rows (for resolution by item# after user picks)
- `_norm_keys` — list of all normalized descriptions (fed to rapidfuzz)

`match_row(row, index, learned)` applies the priority chain (learned → exact → fuzzy + pack-size guard) and returns a `MatchResult` with bucket, picked row, candidates, score, and reason string.

`_prefer_specialized(rows)` — when a match resolves to multiple rows for the same item#, returns the non-`REST LIST` one.

---

## Data Flow: Processing an Order

```
User uploads filled OneStop .xlsx
         │
         ▼
read_onestop() → [OnestopRow, ...]  (only rows with qty > 0)
         │
         ▼
_gm_index() → reads gm_active.xlsx → GmIndex
_load_learned() → reads mapping table → dict[norm → (item_no, sheet)]
         │
         ▼
match_all() → [MatchResult, ...]
  ├── bucket="auto"      (score ≥ 0.92, pack-size OK)
  ├── bucket="review"    (0.75 ≤ score < 0.92, or pack-size mismatch)
  └── bucket="unmatched" (score < 0.75, or learned as OneStop-only)
         │
         ▼
Stored in _RUN_STAGING[run_id] (in-memory)
Response: OrderUploadResponse with three bucketed lists
         │
         ▼ (user reviews and submits resolutions)
         │
POST /api/orders/apply
  ├── auto rows → written directly from staging data
  ├── resolution.choice="accept"/"pick" → written, optionally learned
  └── resolution.choice="onestop_only" → skipped, optionally learned as NULL
         │
         ▼
write_quantities(gm_active.xlsx → gm_order_<ts>_<id>.xlsx)
         │
         ▼
INSERT INTO order_run
INSERT/UPDATE INTO mapping (for remembered resolutions)
         │
         ▼
Response: download_url for the completed GM file
```

---

## Database Schema

### `mapping` — The Learning Table
The most important table. Survives catalog refreshes. Keyed on `onestop_desc_normalized`.

| Column | Type | Notes |
|--------|------|-------|
| `onestop_desc_normalized` | TEXT PK | Normalized OneStop description |
| `onestop_desc` | TEXT | Original description (display only) |
| `gm_item_no` | INT NULL | NULL = "OneStop-exclusive, never write to GM" |
| `gm_sheet` | TEXT NULL | Sheet where this item lives in GM |
| `gm_desc` | TEXT NULL | GM description at time of confirmation |
| `confidence` | REAL | Always 1.0 for user-confirmed rows |
| `confirmed_by` | TEXT | Currently always "local" |
| `confirmed_at` | TEXT | ISO datetime |
| `notes` | TEXT NULL | Unused in v1 |

### `gm_catalog` — Current-Week GM Index
Rebuilt on every catalog refresh. Used for search and fallback fuzzy matching.

| Column | Type | Notes |
|--------|------|-------|
| `item_no` | INT | GM item number |
| `sheet` | TEXT | Sheet name (one of 8 known sheets) |
| `side` | TEXT | "left" or "right" (two-column grid) |
| `row_index` | INT | 1-based row number in the sheet |
| `description` | TEXT | Original description text |
| `description_normalized` | TEXT | Indexed for fast lookup |
| `price` | REAL NULL | |
| `available` | INT | 0 if ORDER cell is "NA" |

### `onestop_template` — Current-Week OneStop Catalog
Rebuilt on every catalog refresh. Used for diff computation.

| Column | Type | Notes |
|--------|------|-------|
| `row_index` | INT PK | |
| `description` | TEXT | |
| `description_normalized` | TEXT | |
| `price` | REAL NULL | |
| `is_header` | INT | 1 for category header rows |

### `order_run` — Operational Log
One row per completed order. `output_path` enables re-download from history.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INT PK AUTOINCREMENT | |
| `uploaded_at` | TEXT | ISO datetime |
| `uploaded_by` | TEXT | Always "local" in v1 |
| `filename` | TEXT | Original OneStop filename |
| `lines_auto` | INT | |
| `lines_reviewed` | INT | |
| `lines_unmatched` | INT | |
| `output_path` | TEXT NULL | Absolute path to GM output file |

---

## Key Design Patterns

**Service / Route separation** — routes do orchestration (load, call, persist, respond); services contain all business logic with no HTTP concerns. A service never imports from `fastapi`.

**Config as the only tunable surface** — `config.py` is the single file an operator touches to change thresholds, paths, or credentials. No environment variable is read anywhere else.

**Read/write split in Excel handling** — `excel_reader.py` is always read-only; `excel_writer.py` always starts from a `copyfile` of the template. This prevents accidental template mutation.

**In-memory ephemeral staging** — run state between upload and apply lives in a module-level dict. Acceptable for a single-tenant tool; a restart requires re-upload.

**Idempotent schema init** — `db.py` uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` throughout. Running `init_schema()` twice is safe.

---

## External Services / Integrations

| Service | Purpose | Notes |
|---------|---------|-------|
| Render.com | Hosting | `render.yaml` configures web service + 1 GB persistent disk |
| Google Fonts CDN | Fraunces, Bricolage Grotesque, JetBrains Mono | Loaded in `index.html` |
| Tailwind CSS CDN | Utility classes | No build step; `tailwind.config` block in `index.html` |
| Alpine.js CDN | Reactive UI | `unpkg.com/alpinejs@3.14.1` |

No external APIs are called at runtime. No email, no Slack, no third-party order submission.
