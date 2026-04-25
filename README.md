# OrderBridge

Automates converting a filled **OneStop Distribution** Excel order form into a completed **GrainMarket (GM)** supplier order form — turning 30–60 minutes of manual re-keying into a ~30-second upload → review → download flow.

See [PRD.md](./PRD.md) for product requirements, [PLAN.md](./PLAN.md) for engineering design, and [ARCHITECTURE.md](./ARCHITECTURE.md) for system overview.

---

## Prerequisites

- Python 3.11+
- No Node.js or build tools needed — the frontend is plain HTML/JS served by FastAPI

---

## Local Setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Set credentials (or accept the defaults — **change before deploying**):

```bash
export OB_USER=onestop
export OB_PASS=your-password-here
```

Start the dev server:

```bash
.venv/bin/uvicorn orderbridge.main:app --reload --port 8000
```

Open http://localhost:8000. The browser will prompt for the Basic auth credentials.

---

## First Use

1. Click **Refresh Catalogs** (top-right toolbar).
2. Upload the current-week **OneStop master template** (blank, not a filled order) and the **GrainMarket master template**.
3. The app indexes both catalogs and shows a diff of what changed from last week.
4. Close the modal → you're ready to process orders.

---

## Processing an Order

1. On the main screen, drop a **filled OneStop order form** (.xlsx) onto the upload zone or click to browse.
2. The app matches every line with qty > 0 and sorts them into three buckets:
   - **Auto** (jade) — confident match (≥92%), nothing to review.
   - **Review** (amber) — fuzzy match 75–91%; pick a candidate or mark as OneStop-only.
   - **Unmatched** (rose) — no GM equivalent found; stays OneStop-fulfilled.
3. Work through the Review bucket. Use the candidate chips, the search catalog button, or the ⌘K command palette to assign items.
4. Click **Apply & download** (or press ⌘↵). The app fills quantities into a copy of the active GM template and returns the file.
5. Forward the downloaded `.xlsx` to GrainMarket via your normal channel.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j` / `k` | Move selection down / up through lines |
| `r` | Open Refresh Catalogs modal |
| `h` | Toggle Run History drawer |
| `t` | Toggle dark/light theme |
| `/` | Focus line filter input |
| `⌘K` | Open command palette (also ⌃K) |
| `⌘↵` | Apply & download current run |

---

## CLI (Batch / Smoke-Testing)

```bash
cd backend
.venv/bin/python -m orderbridge.cli match \
  --onestop ../samples/filled_order.xlsx \
  --gm      ../samples/gm_template.xlsx \
  --out     /tmp/gm_output.xlsx
```

Prints `[REVIEW]` and `[NO-MATCH]` lines to stdout and writes auto-matched quantities directly to the output file.

---

## Tests

```bash
cd backend
.venv/bin/pytest -q
```

Tests live in `backend/tests/`. Three files: normalize, matching, and an Excel round-trip integration test.

---

## Deployment (Render.com)

The repo includes `render.yaml` for a one-click Render deploy.

**Manual steps:**
1. Push the repo to GitHub/GitLab.
2. Create a new Render **Web Service** from the repo, using `render.yaml` (auto-detected).
3. Add env vars `OB_USER` and `OB_PASS` in the Render dashboard.
4. Render mounts a 1 GB persistent disk at `/app/storage` — templates, runs, and the SQLite DB persist across deploys.

**Docker (manual):**
```bash
docker build -f backend/Dockerfile -t orderbridge .
docker run -p 8000:8000 \
  -e OB_USER=onestop -e OB_PASS=changeme \
  -v $(pwd)/storage:/app/storage \
  orderbridge
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OB_USER` | `onestop` | HTTP Basic auth username |
| `OB_PASS` | `changeme` | HTTP Basic auth password |
| `OB_STORAGE_DIR` | `<repo-root>/storage` | Path to templates, run outputs, and the SQLite DB |

---

## Project Layout

```
OrderBridge/
├── backend/            FastAPI app, matching engine, CLI, tests
│   ├── orderbridge/    Main Python package
│   │   ├── routes/     API route handlers
│   │   └── services/   Business logic (normalize, match, read/write Excel)
│   └── tests/          pytest test suite
├── frontend/           Static HTML/JS/CSS UI served by FastAPI
├── storage/            Runtime data — gitignored (templates, runs, mappings.db)
├── PRD.md              Product requirements
├── PLAN.md             Engineering design and decisions
├── ARCHITECTURE.md     System architecture and data flow
└── render.yaml         Render.com deploy config
```
