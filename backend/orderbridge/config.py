"""Runtime configuration — paths, thresholds, auth credentials.

All tunables live here so the rest of the code reads as pure logic.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # repo root .env
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # backend/.env
except ImportError:
    pass

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


# ─── PDF Order Intake ────────────────────────────────────────────────────────
# Provider: "gemini" (default, uses GOOGLE_API_KEY) or "anthropic" (uses ANTHROPIC_API_KEY)
PDF_VISION_PROVIDER = os.environ.get("PDF_VISION_PROVIDER", "gemini")

# API key — checked in priority order: explicit override, then provider-specific var
PDF_VISION_API_KEY = (
    os.environ.get("PDF_VISION_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("ANTHROPIC_API_KEY")
    or ""
)

# Model defaults per provider:
#   gemini   → gemini-2.5-flash   (fast, cheap, strong vision)
#   anthropic → claude-haiku-4-5-20251001
PDF_VISION_MODEL    = os.environ.get("PDF_VISION_MODEL", "gemini-2.5-flash")
PDF_PAGE_RESOLUTION = int(os.environ.get("PDF_PAGE_RESOLUTION", "150"))  # DPI
PDF_MAX_PAGES       = int(os.environ.get("PDF_MAX_PAGES", "60"))

PDF_EXTRACTION_PROMPT = """\
You are a data extraction assistant for a food wholesale company.
You will be shown a scanned page from a printed order catalog.

The page has printed product rows. Each row contains a product description and a price.
The customer has handwritten an integer quantity ONLY on rows they want to order.

CRITICAL RULE — only include a row if you can VISUALLY SEE a handwritten number on it.
Bold text, highlighted text, larger text, or visually emphasized printed text is NOT
a quantity. A printed "1" or "12" inside a pack-size description (e.g. "12X100ML",
"500ML (12)") is NOT a handwritten quantity. If there is no clearly handwritten digit
visible on the row, OMIT that row entirely. Do not infer, guess, or default to qty=1.

Handwriting characteristics — handwritten quantities are:
  - Written in pen or marker ink that looks distinctly different from printed catalog text
  - Often messy, slanted, or irregular in size compared to the printed font
  - Usually a single digit or two digits

Handwritten quantities may appear in any of these positions on a row:
  1. In the narrow QTY column on the far left of the row (the intended location).
  2. At the end of the description text, written in the gap before the price.
  3. Adjacent to or overlapping the printed price in the far-right column.

For each row that has a CLEAR handwritten quantity, extract:
  - description: the PRINTED product name from that row (verbatim, include pack size)
  - qty_seen: the literal handwritten characters you see, as a string (e.g. "3", "10", "1.5")
  - qty: the parsed integer value of qty_seen (positive integer)
  - price: the printed dollar price as a float (e.g. 42.00), or null if not legible

Rules:
  - SKIP rows with no handwritten ink anywhere — these are NOT ordered.
  - SKIP category header rows — bold text on dark/shaded background bands
    (e.g. "SPECIAL ITEMS", "PRODUCE BAGS", "ESSENTIALS") with no price.
  - SKIP rows where the only marking is a tick, cross, circle, or underline (no digit).
  - SKIP rows where the only number on the row is part of the printed pack size or price.
  - If a digit is ambiguous (1 vs 7, 0 vs 6, 3 vs 8), prefer the reading that makes
    sense as a small order quantity in context.
  - Return ONLY valid JSON — no explanation, no markdown, no code fences.
  - If no items were ordered on this page, return an empty array: []

Return format:
[{"description": "PRODUCT NAME WITH PACK SIZE", "qty_seen": "2", "qty": 2, "price": 42.00}, ...]
"""


def ensure_dirs() -> None:
    for d in (STORAGE_DIR, TEMPLATES_DIR, RUNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
