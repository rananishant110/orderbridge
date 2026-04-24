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


def ensure_dirs() -> None:
    for d in (STORAGE_DIR, TEMPLATES_DIR, RUNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
