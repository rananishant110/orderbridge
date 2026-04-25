# Contributing to OrderBridge

OrderBridge is a single-tenant internal tool. This guide covers local dev setup, where new code goes, and the conventions observed in the existing codebase.

---

## Dev Environment Setup

**Requirements:** Python 3.11+. No Node.js, no Docker, no build tools needed.

```bash
# Clone and enter the repo
git clone <repo-url>
cd OrderBridge

# Create the virtualenv inside backend/
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Optional: install ruff for linting
.venv/bin/pip install ruff
```

**First-run storage setup:**
```bash
mkdir -p storage/templates storage/runs
```

**Start the dev server with hot-reload:**
```bash
cd backend
.venv/bin/uvicorn orderbridge.main:app --reload --port 8000
```

Navigate to http://localhost:8000. It will prompt for HTTP Basic credentials (defaults: `onestop` / `changeme`).

The app will show "No active GM template" until you upload both templates via Refresh Catalogs.

---

## Environment Variables for Local Dev

```bash
export OB_USER=onestop       # Basic auth username
export OB_PASS=changeme      # Basic auth password
export OB_STORAGE_DIR=./storage   # optional; defaults to <repo-root>/storage
```

All config is in `backend/orderbridge/config.py` — change matching thresholds or paths there.

---

## Running Tests

```bash
cd backend
.venv/bin/pytest -q
```

Tests are in `backend/tests/`. They run without any external files or a running server — the Excel round-trip test builds synthetic workbooks in-memory.

---

## Where New Code Goes

| What you're adding | Where |
|--------------------|-------|
| New API endpoint | `backend/orderbridge/routes/` — add to `orders.py`, `catalogs.py`, or create a new router file |
| New business logic | `backend/orderbridge/services/` — keep free of FastAPI imports |
| New Pydantic schema | `backend/orderbridge/schemas.py` |
| New config constant | `backend/orderbridge/config.py` |
| New DB table or column | `backend/orderbridge/db.py` `SCHEMA` string |
| New frontend feature | `frontend/app.js` (Alpine component) + `frontend/index.html` (template) + `frontend/styles.css` if new component classes needed |
| New tests | `backend/tests/test_<module>.py` |
| Feature documentation | `docs/FEATURES.md` |

---

## Code Conventions

### Python

**Module-level docstring** — every module has a one-paragraph docstring at the top explaining its purpose and any non-obvious constraints.

**`from __future__ import annotations`** — present on every module. Required for forward references in type hints on Python < 3.10.

**Frozen dataclasses for data objects** — row types (`OnestopRow`, `GmRow`, `MatchResult`, `OrderWrite`) are `@dataclass(frozen=True)`. Pydantic `BaseModel` is reserved for the API boundary only.

**Single config source** — never read `os.environ` outside of `config.py`. If you need a new env var, add it to `config.py` following the pattern:
```python
MY_VAR = os.environ.get("OB_MY_VAR", "default_value")
```

**Services are HTTP-free** — never import from `fastapi` in `services/`. If a service needs to signal an error, raise a plain Python exception and let the route handler catch it.

**Reader/writer split** — `excel_reader.py` is read-only (`load_workbook(read_only=True, data_only=True)`). `excel_writer.py` always starts from a `shutil.copyfile` of the template. Never mix.

**DB access pattern:**
```python
with db.session() as conn:
    rows = conn.execute("SELECT ...", (params,)).fetchall()
```
`isolation_level=None` means autocommit — no `conn.commit()` needed.

**No magic numbers** — matching thresholds, sheet names, column letters go in `config.py` or as module-level constants.

### Frontend (Alpine.js)

**All state in `orderBridge()`** — the Alpine component in `app.js` is the single source of truth for all UI state. No global variables outside it.

**Computed properties over methods** — use `get` property syntax (`get filteredLines()`, `get counts()`) for derived state so Alpine can track reactivity.

**`$nextTick` for DOM after state change** — when state changes trigger DOM updates that require scrolling, use `this.$nextTick(() => { ... })`.

**No inline `fetch` in templates** — all network calls go through `app.js` methods.

---

## Adding a New API Route

1. Decide which router it belongs to (`orders.py` or `catalogs.py`) or create a new file.
2. Add the Pydantic schema(s) in `schemas.py` if the endpoint has a structured request/response body.
3. Implement the route function. Call services, not inline logic.
4. Register the new router in `main.py` if you created a new file (`app.include_router(newrouter.router)`).
5. Add tests in `backend/tests/test_<feature>.py`.
6. Document the feature in `docs/FEATURES.md`.

---

## Adding a New DB Table

Add the `CREATE TABLE IF NOT EXISTS` and any `CREATE INDEX IF NOT EXISTS` to the `SCHEMA` string in `backend/orderbridge/db.py`. The schema is applied idempotently on every startup — no migration runner needed for additive changes.

For destructive changes (renaming columns, changing types), you'll need to manually migrate the existing `mappings.db` file on the server and update the schema string.

---

## Branching and Commit Conventions

The repo uses a single `main` branch. There is one commit in history (`bcd4854`).

**Commit message style** observed: short imperative summary + version/context context, e.g.:
```
Initial commit — OrderBridge v0.1
```

For ongoing work, a conventional-commit-like style is reasonable:
```
feat: add quantity anomaly warning on apply
fix: pack-size guard false-negative for ML vs ml
refactor: extract catalog diff into service layer
```

---

## Testing Requirements

- **New business logic in services/** → must have unit tests.
- **New API routes** → manual `curl` test is acceptable for v1; automated tests preferred.
- **Excel write changes** → must include an `excel_roundtrip` test asserting that sibling cells are untouched.
- Tests must not require a running server or a real `.xlsx` file — build synthetic fixtures inline.

```bash
# Run full suite
cd backend && .venv/bin/pytest -q

# Run a single file
cd backend && .venv/bin/pytest tests/test_matching.py -v
```

---

## Linting (Optional)

```bash
cd backend
.venv/bin/ruff check orderbridge/
.venv/bin/ruff format orderbridge/
```

Ruff is not enforced in CI but is listed as optional in the engineering plan.

---

## Deployment Checklist

Before deploying to Render:

1. Set `OB_USER` and `OB_PASS` to non-default values in Render env vars.
2. Confirm the persistent disk is mounted at `/app/storage`.
3. Verify `storage/templates/` is populated after first deploy by running the Refresh Catalogs flow.
4. Check the health endpoint: `GET /api/catalogs/status` should return 200.
5. Back up `mappings.db` before any schema change — the `mapping` table is the durable artifact.
