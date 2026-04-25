# OrderBridge — AI Context File

OrderBridge is a single-tenant internal web app that converts a filled **OneStop Distribution** Excel order form (`.xlsx`) into a completed **GrainMarket (GM)** supplier order form, automating what was previously 30–60 minutes of manual re-keying per order.

---

## Tech Stack

### Backend
| Component | Version | Role |
|-----------|---------|------|
| Python | 3.11+ | Runtime |
| FastAPI | 0.115.5 | HTTP framework |
| Uvicorn | 0.32.1 | ASGI server |
| openpyxl | 3.1.5 | Excel read/write (preserves cell styles) |
| rapidfuzz | 3.10.1 | Fuzzy description matching (C++ backend) |
| pydantic | 2.9.2 | Request/response schemas |
| python-multipart | 0.0.17 | Multipart file upload support |
| sqlite3 | stdlib | Persistent mapping/catalog storage |

### Frontend
| Component | Version | Role |
|-----------|---------|------|
| Alpine.js | 3.14.1 (CDN) | Reactive UI — no build step |
| Tailwind CSS | CDN | Utility-first styles |
| Vanilla JS/HTML/CSS | — | No framework, no bundler |
| Fonts | Google Fonts CDN | Fraunces (display), Bricolage Grotesque (sans), JetBrains Mono (mono) |

---

## Dev Commands

```bash
# Install (from repo root)
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run dev server (hot-reload, port 8000)
cd backend
.venv/bin/uvicorn orderbridge.main:app --reload --port 8000

# Run tests
cd backend
.venv/bin/pytest -q

# CLI smoke-test (no web layer)
cd backend
.venv/bin/python -m orderbridge.cli match \
  --onestop ../samples/filled_order.xlsx \
  --gm      ../samples/gm_template.xlsx \
  --out     /tmp/gm_output.xlsx

# Docker build
docker build -f backend/Dockerfile -t orderbridge .
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OB_USER` | `onestop` | HTTP Basic username |
| `OB_PASS` | `changeme` | HTTP Basic password — **change in production** |
| `OB_STORAGE_DIR` | `<repo-root>/storage` | Directory for templates, run outputs, and the SQLite DB |

All config lives in `backend/orderbridge/config.py` — matching thresholds (`AUTO_ACCEPT_SCORE`, `REVIEW_FLOOR_SCORE`) are there too.

---

## Folder / File Structure

```
OrderBridge/
├── CLAUDE.md                   ← this file
├── PRD.md                      ← product requirements (read for context)
├── PLAN.md                     ← engineering design (read for design decisions)
├── README.md                   ← setup and usage
├── ARCHITECTURE.md             ← system design and data flow
├── CONTRIBUTING.md             ← dev setup and conventions
├── render.yaml                 ← Render.com deploy config
├── backend/
│   ├── Dockerfile              ← python:3.11-slim; serves both API + frontend
│   ├── requirements.txt        ← pinned prod deps
│   ├── pyproject.toml          ← project metadata + pytest config
│   ├── orderbridge/            ← main Python package
│   │   ├── main.py             ← FastAPI app factory; mounts static frontend
│   │   ├── config.py           ← ALL tunables: paths, thresholds, auth creds
│   │   ├── db.py               ← SQLite schema + connect/session helpers
│   │   ├── auth.py             ← HTTP Basic auth dependency
│   │   ├── schemas.py          ← Pydantic request/response models
│   │   ├── cli.py              ← Phase-1 CLI (match without web layer)
│   │   ├── routes/
│   │   │   ├── orders.py       ← /api/orders/* (upload, apply, download, history)
│   │   │   └── catalogs.py     ← /api/catalogs/* (refresh, search, status, gm)
│   │   └── services/
│   │       ├── normalize.py    ← text normalization + pack-size extraction
│   │       ├── excel_reader.py ← READ-ONLY openpyxl parsers (OnestopRow, GmRow)
│   │       ├── excel_writer.py ← writes ORDER cells into a GM template copy
│   │       └── matching.py     ← GmIndex + match_row + match_all (core logic)
│   └── tests/
│       ├── test_normalize.py       ← unit tests for normalize/pack-size funcs
│       ├── test_matching.py        ← unit tests for matching priority logic
│       └── test_excel_roundtrip.py ← integration: writer preserves sheet metadata
├── frontend/
│   ├── index.html              ← single-page app (upload → review → apply)
│   ├── app.js                  ← Alpine.js controller (all UI state + API calls)
│   └── styles.css              ← raw CSS (no Tailwind build step; extends CDN)
├── storage/                    ← gitignored at runtime
│   ├── templates/
│   │   ├── onestop_active.xlsx ← current-week OneStop master template
│   │   └── gm_active.xlsx      ← current-week GM master template
│   ├── runs/                   ← uploaded OneStop files + generated GM outputs
│   └── mappings.db             ← SQLite — the durable learning artifact
├── docs/
│   └── FEATURES.md
└── GM Order Form_Prices 04.17 (3).xlsx   ← sample GM template (not in git)
    ONE STOP ORDER FORM - APRIL 5TH.xlsx  ← sample OneStop form (not in git)
```

---

## Code Conventions

### Python
- `from __future__ import annotations` on every module (deferred evaluation).
- Dataclasses (`@dataclass(frozen=True)`) for row types; Pydantic for API schemas — never mix.
- `config.py` is the single source of all tunables. No magic numbers elsewhere.
- `db.session()` is a context manager that opens and closes a connection. Use it for all DB access.
- SQLite WAL mode is always on; `isolation_level=None` (autocommit) is the session default.
- Services are pure functions / classes — no FastAPI imports, no HTTP concerns.
- Routes do orchestration only: load data → call services → call DB → return schema.
- Reader/writer split is strict: `excel_reader.py` never writes, `excel_writer.py` never reads business logic.

### Frontend
- All UI state lives in the single `orderBridge()` Alpine component in `app.js`.
- No state management library — Alpine reactive properties + computed `get` properties.
- API calls use native `fetch()` with `FormData` for multipart, `JSON.stringify` for JSON.
- Keyboard shortcuts bound in `bindShortcuts()`: `j/k` navigate lines, `r` opens catalog refresh, `h` opens history, `⌘K` opens command palette, `⌘↵` applies run.
- CSS aesthetic: "commodities terminal × agricultural merchant's ledger" — warm dark palette (`ink-*`), wheat-gold accent (`wheat-*`), jade green for auto, saffron for review, rose for unmatched.

### Testing
- Test files are named `test_<module>.py` in `backend/tests/`.
- No fixtures directory used in practice — synthetic xlsx files built inline with openpyxl in test functions.
- `pytest.ini_options` in `pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["."]`.

---

## Matching Logic (the core product promise)

Priority order — first match wins:

1. **Learned mapping** (`mapping` table) — confidence 1.0, always wins.
2. **Exact normalized match** against `gm_catalog.description_normalized` — confidence 1.0.
3. **Fuzzy match** via `rapidfuzz.token_set_ratio`:
   - ≥ 0.92 → `auto` bucket
   - 0.75–0.92 → `review` bucket
   - < 0.75 → `unmatched`
4. **Pack-size guard**: if the OneStop description contains a pack-size token (e.g. `20X500G`), the GM candidate must also match it (±2% weight tolerance). Mismatch demotes to `review` regardless of score.
5. **Sheet preference**: items appearing on both a specialized sheet and `REST LIST` always go to the specialized sheet. `REST LIST` is informational only.

Thresholds are in `config.py` as `AUTO_ACCEPT_SCORE = 0.92` and `REVIEW_FLOOR_SCORE = 0.75`.

---

## Where New Code Goes

| What | Where |
|------|-------|
| New API routes | `backend/orderbridge/routes/` — new file or add to `orders.py`/`catalogs.py` |
| New business logic | `backend/orderbridge/services/` — keep services free of HTTP concerns |
| New DB tables/columns | `backend/orderbridge/db.py` `SCHEMA` string — no migration framework, just `CREATE TABLE IF NOT EXISTS` |
| New config tunables | `backend/orderbridge/config.py` |
| New API request/response types | `backend/orderbridge/schemas.py` |
| Frontend features | `frontend/app.js` (Alpine component) + `frontend/index.html` (templates) + `frontend/styles.css` (component CSS) |
| New tests | `backend/tests/test_<feature>.py` |

---

## Key Constraints and Gotchas

1. **openpyxl formatting preservation** — the writer copies the GM template file via `shutil.copyfile`, then loads and modifies it. It never creates a new workbook from scratch. This is intentional — creating a new workbook discards styles, merged cells, and print settings.

2. **Row-drift sanity check** — `excel_writer.py` verifies the item# in column A/F before writing the ORDER cell. If the catalog has been refreshed and row indices shifted, the write hard-errors rather than silently filling the wrong row.

3. **In-memory run staging** — between upload and apply, runs live in `_RUN_STAGING` dict (in-memory). A server restart loses them; the UI re-uploads. This is by design (see PLAN.md §6.6).

4. **No auth on most routes** — auth was removed in the current implementation (`LOCAL_USER = "local"` constant used). The `auth.py` module exists but is not wired into the routers. If you add auth back, import `require_user` as a FastAPI `Depends`.

5. **GM sheet names are hardcoded** — `GM_PRODUCT_SHEETS` in `excel_reader.py` lists all expected sheet names. If GM adds a new sheet, it must be added there.

6. **SQLite `isolation_level=None`** — all connections run in autocommit. Do not use `conn.commit()` or `conn.rollback()`.

7. **`storage/` is gitignored** — templates and runs are not committed. On a fresh clone, `storage/templates/` is empty; the app raises `400 No active GM template` until catalogs are refreshed via the UI.

8. **Python 3.11+ required** — `pyproject.toml` sets `requires-python = ">=3.11"`. The venv in `backend/.venv` uses Python 3.10 (check local version if tests fail with syntax errors).

---

## Git Workflow

- Single branch: `main`.
- One commit in history: `bcd4854 Initial commit — OrderBridge v0.1`.
- No PR process observed — direct commits to main.
- Commit message style: short imperative summary with version/context suffix.
