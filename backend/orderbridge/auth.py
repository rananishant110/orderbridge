"""HTTP Basic auth — one shared credential, set via env."""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config

_security = HTTPBasic()


def require_user(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    ok_user = secrets.compare_digest(credentials.username, config.AUTH_USER)
    ok_pass = secrets.compare_digest(credentials.password, config.AUTH_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
