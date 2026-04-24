"""FastAPI entry point.

One app:
  - /api/*   JSON endpoints (Basic-authed)
  - /        static frontend
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routes import catalogs, orders


def create_app() -> FastAPI:
    config.ensure_dirs()
    db.init_schema()

    app = FastAPI(title="OrderBridge", version="0.1.0")
    app.include_router(orders.router)
    app.include_router(catalogs.router)

    if config.FRONTEND_DIR.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
            name="frontend",
        )
    return app


app = create_app()
