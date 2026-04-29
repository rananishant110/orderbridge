"""Runtime configuration — paths, thresholds, auth credentials.

All tunables live here so the rest of the code reads as pure logic.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

STORAGE_DIR = Path(os.environ.get("OB_STORAGE_DIR", PROJECT_ROOT / "storage"))
TEMPLATES_DIR = STORAGE_DIR / "templates"
RUNS_DIR = STORAGE_DIR / "runs"
DB_PATH = STORAGE_DIR / "mappings.db"

ONESTOP_TEMPLATE_PATH = TEMPLATES_DIR / "onestop_active.xlsx"
GM_TEMPLATE_PATH = TEMPLATES_DIR / "gm_active.xlsx"

FRONTEND_DIR = PROJECT_ROOT / "frontend"

# Matching thresholds — see PLAN.md §6.5
AUTO_ACCEPT_SCORE = 0.92
REVIEW_FLOOR_SCORE = 0.75

# Sheet preference — see PRD §8
# When an item# appears on REST LIST *and* a specialized sheet, we write to the
# specialized one. REST LIST is informational only.
REST_LIST_SHEET = "REST LIST"

# HTTP Basic auth — override in production via env
AUTH_USER = os.environ.get("OB_USER", "onestop")
AUTH_PASS = os.environ.get("OB_PASS", "changeme")

# FreshBooks OAuth2 integration
# Set FRESHBOOKS_CLIENT_ID and FRESHBOOKS_CLIENT_SECRET via env vars (or docker-compose).
# FRESHBOOKS_REDIRECT_URI must match the redirect URI registered in your FreshBooks app.
FRESHBOOKS_CLIENT_ID     = os.environ.get("FRESHBOOKS_CLIENT_ID", "")
FRESHBOOKS_CLIENT_SECRET = os.environ.get("FRESHBOOKS_CLIENT_SECRET", "")
FRESHBOOKS_REDIRECT_URI  = os.environ.get(
    "FRESHBOOKS_REDIRECT_URI", "http://localhost:8000/api/freshbooks/callback"
)
FRESHBOOKS_CUSTOMER_ID   = os.environ.get("FRESHBOOKS_CUSTOMER_ID", "151069")
# Known account ID — used as fallback if /me extraction fails
FRESHBOOKS_ACCOUNT_ID    = os.environ.get("FRESHBOOKS_ACCOUNT_ID", "61wqkw")

FRESHBOOKS_AUTH_URL    = "https://auth.freshbooks.com/service/auth/oauth/authorize"
FRESHBOOKS_TOKEN_URL   = "https://auth.freshbooks.com/service/auth/oauth/token"
FRESHBOOKS_API_BASE    = "https://api.freshbooks.com"

FRESHBOOKS_DISCLAIMER = (
    "Disclaimer:\n"
    "*Seller has the ownership of all unpaid merchandise.\n"
    "*All returns must be pre authorized & are on buyers' expense.\n"
    "*Buyers are responsible for their state taxes and issues.\n"
    "*If a check is returned for any reason, buyer will pay an additional charge of $50.00."
)


def ensure_dirs() -> None:
    for d in (STORAGE_DIR, TEMPLATES_DIR, RUNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
