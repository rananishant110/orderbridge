"""Auth — cookie session for the custom login page.

Flow:
  POST /api/login  → validates credentials → sets ob_session cookie (30 days)
  POST /api/logout → clears the cookie
  verify_session   → FastAPI Depends used on all protected routes

The session token is a deterministic SHA-256 of the credentials, so it
invalidates automatically when OB_USER or OB_PASS changes in env.
No WWW-Authenticate header is ever sent, so the browser's native Basic-auth
dialog never appears.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import Cookie, HTTPException, Response, status

from . import config


def _session_token() -> str:
    raw = f"{config.AUTH_USER}:{config.AUTH_PASS}"
    return hashlib.sha256(raw.encode()).hexdigest()


def create_session(username: str, password: str, response: Response) -> bool:
    """Validate credentials and set the session cookie. Returns True on success."""
    ok_user = secrets.compare_digest(username, config.AUTH_USER)
    ok_pass = secrets.compare_digest(password, config.AUTH_PASS)
    if not (ok_user and ok_pass):
        return False
    response.set_cookie(
        "ob_session",
        _session_token(),
        httponly=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 30,   # 30 days
    )
    return True


def clear_session(response: Response) -> None:
    response.delete_cookie("ob_session", samesite="strict")


def verify_session(ob_session: str | None = Cookie(default=None)) -> str:
    """FastAPI dependency — validates the session cookie.

    Returns the username on success. Raises 401 without a WWW-Authenticate
    header so the browser redirects to /login instead of showing its own prompt.
    """
    if ob_session != _session_token():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — please log in.",
        )
    return config.AUTH_USER
