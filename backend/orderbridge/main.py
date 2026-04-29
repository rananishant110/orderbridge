"""FastAPI entry point.

One app:
  - /api/login    POST  — sets session cookie (public)
  - /api/logout   POST  — clears session cookie (public)
  - /api/*        JSON endpoints (session-authed)
  - /             static frontend
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .auth import clear_session, create_session, verify_session
from .routes import catalogs, freshbooks, orders


class _LoginRequest(BaseModel):
    username: str
    password: str


def create_app() -> FastAPI:
    config.ensure_dirs()
    db.init_schema()

    app = FastAPI(title="OrderBridge", version="0.1.0")

    @app.post("/api/login", tags=["auth"])
    def login(body: _LoginRequest, response: Response):
        if not create_session(body.username, body.password, response):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bad credentials",
            )
        return {"ok": True}

    @app.post("/api/logout", tags=["auth"])
    def logout(response: Response):
        clear_session(response)
        return {"ok": True}

    @app.get("/login", include_in_schema=False)
    def login_page():
        return RedirectResponse(url="/login.html")

    from fastapi import Depends

    @app.get("/api/me", tags=["auth"])
    def me(user: str = Depends(verify_session)):
        return {"user": user}

    app.include_router(orders.router)
    app.include_router(catalogs.router)
    app.include_router(freshbooks.router)

    if config.FRONTEND_DIR.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
            name="frontend",
        )
    return app


app = create_app()
