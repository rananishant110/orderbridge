# OrderBridge

Automates translating a filled **OneStop Distribution** order form into the
**GrainMarket** supplier order form. Uploads, matches every line to the right
GM item#, lets staff review ambiguous cases, and returns GM's template with
quantities filled in and formatting preserved.

See [PRD.md](./PRD.md) and [PLAN.md](./PLAN.md) for the product + engineering
design. This README covers running it.

## Layout

```
OrderBridge/
├── backend/            FastAPI app + CLI + tests
├── frontend/           Static HTML/JS UI, served by FastAPI
├── storage/            Active templates, run outputs, SQLite (gitignored)
├── PRD.md              Product requirements
├── PLAN.md             Engineering plan
└── README.md           This file
```

## Run locally

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Set credentials (or accept the default onestop / changeme)
export OB_USER=onestop
export OB_PASS=your-password-here

# Start the server
.venv/bin/uvicorn orderbridge.main:app --reload --port 8000
```

Then open <http://localhost:8000>. The browser will prompt for the Basic auth
credentials you set.

## First use

1. Go to **Refresh Catalogs**.
2. Upload the current-week OneStop master template **and** the GM master template.
3. The app stores both as the "active" templates and seeds the catalog diff.
4. Switch to **Process Order**, upload a filled OneStop order, review and apply.

## CLI (for Phase-1 smoke testing)

```bash
cd backend
.venv/bin/python -m orderbridge.cli match \
  --onestop ../samples/filled_order.xlsx \
  --gm      ../samples/gm_template.xlsx \
  --out     /tmp/gm_output.xlsx
```

## Tests

```bash
cd backend
.venv/bin/pytest -q
```

## Deployment (Render)

```bash
docker build -f backend/Dockerfile -t orderbridge .
# Push image; on Render, add env vars OB_USER + OB_PASS, mount a 1GB
# persistent disk at /app/storage, point a CNAME at the service.
```

## Environment variables

| Var                | Default                 | Purpose                                |
|--------------------|-------------------------|----------------------------------------|
| `OB_USER`          | `onestop`               | HTTP Basic username                    |
| `OB_PASS`          | `changeme`              | HTTP Basic password                    |
| `OB_STORAGE_DIR`   | `<repo>/storage`        | Where templates, runs, and DB live     |
