"""FreshBooks OAuth2 + invoice endpoints.

OAuth flow (single-tenant):
  1. GET  /api/freshbooks/connect   → redirect browser to FreshBooks auth page
  2. GET  /api/freshbooks/callback  → exchange code → store tokens in SQLite → back to app
  3. GET  /api/freshbooks/status    → is connected? account info?
  4. POST /api/freshbooks/disconnect → wipe stored tokens

Invoice flow:
  5. POST /api/freshbooks/parse     → upload OSD PDF → structured line items
  6. POST /api/freshbooks/invoice   → confirmed items → create FreshBooks invoice
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import config, db
from ..auth import verify_session
from ..schemas import (FbAppendRequest, FbInvoiceDetail, FbInvoiceLine,
                       FbInvoiceListItem, FbInvoiceListResponse,
                       FbInvoiceRequest, FbInvoiceResponse, FbLineItem, FbParseResponse)
from ..services.pdf_parser import parse_pdf

router = APIRouter(prefix="/api/freshbooks", tags=["freshbooks"])

_MAX_PDF_BYTES = 20 * 1024 * 1024   # 20 MB


# ─── token storage helpers ────────────────────────────────────────────────

def _save_tokens(access_token: str, refresh_token: str,
                 account_id: str, expires_in: int) -> None:
    expires_at = time.time() + expires_in - 60   # 60 s safety margin
    with db.session() as conn:
        conn.execute(
            """INSERT INTO freshbooks_tokens (id, access_token, refresh_token, account_id, expires_at)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 access_token  = excluded.access_token,
                 refresh_token = excluded.refresh_token,
                 account_id    = excluded.account_id,
                 expires_at    = excluded.expires_at""",
            (access_token, refresh_token, account_id, expires_at),
        )


def _load_tokens() -> dict | None:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM freshbooks_tokens WHERE id = 1").fetchone()
    return dict(row) if row else None


def _clear_tokens() -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM freshbooks_tokens WHERE id = 1")


async def _get_valid_access_token() -> str:
    """Return a fresh access token, refreshing first if needed. Raises 401 if not connected."""
    tokens = _load_tokens()
    if not tokens:
        raise HTTPException(401, detail="FreshBooks not connected — visit /api/freshbooks/connect")

    if time.time() < tokens["expires_at"]:
        return tokens["access_token"]

    # Token expired — refresh it.
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            config.FRESHBOOKS_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": config.FRESHBOOKS_CLIENT_ID,
                "client_secret": config.FRESHBOOKS_CLIENT_SECRET,
                "redirect_uri": config.FRESHBOOKS_REDIRECT_URI,
                "refresh_token": tokens["refresh_token"],
            },
        )

    if resp.status_code != 200:
        _clear_tokens()   # refresh token is invalid — force reconnect
        raise HTTPException(
            401,
            detail=f"FreshBooks token refresh failed ({resp.status_code}) — reconnect at /api/freshbooks/connect",
        )

    data = resp.json()
    _save_tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", tokens["refresh_token"]),
        account_id=tokens["account_id"],
        expires_in=data.get("expires_in", 3600),
    )
    return data["access_token"]


async def _fb_get(path: str, params: dict | None = None) -> dict:
    token = await _get_valid_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{config.FRESHBOOKS_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Api-Version": "alpha"},
            params=params,
        )
    if not resp.is_success:
        raise HTTPException(502, f"FreshBooks API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def _fb_post(path: str, payload: dict) -> dict:
    token = await _get_valid_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{config.FRESHBOOKS_API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Api-Version": "alpha",
            },
            content=json.dumps(payload),
        )
    if not resp.is_success:
        raise HTTPException(502, f"FreshBooks API error {resp.status_code}: {resp.text[:400]}")
    return resp.json()


async def _fb_put(path: str, payload: dict) -> dict:
    token = await _get_valid_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{config.FRESHBOOKS_API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Api-Version": "alpha",
            },
            content=json.dumps(payload),
        )
    if not resp.is_success:
        raise HTTPException(502, f"FreshBooks API error {resp.status_code}: {resp.text[:400]}")
    return resp.json()


# ─── OAuth routes (no session auth — browser navigates here directly) ──────

@router.get("/connect", include_in_schema=False)
def fb_connect():
    """Redirect browser to FreshBooks OAuth consent screen."""
    if not config.FRESHBOOKS_CLIENT_ID:
        raise HTTPException(503, "FRESHBOOKS_CLIENT_ID env var not set")

    params = urlencode({
        "response_type": "code",
        "client_id": config.FRESHBOOKS_CLIENT_ID,
        "redirect_uri": config.FRESHBOOKS_REDIRECT_URI,
        "scope": "user:profile:read user:invoices:read user:invoices:write user:clients:read",
    })
    return RedirectResponse(f"{config.FRESHBOOKS_AUTH_URL}?{params}")


@router.get("/callback", include_in_schema=False)
async def fb_callback(code: str = "", error: str = ""):
    """FreshBooks redirects here after user authorises (or denies)."""
    if error or not code:
        # Redirect back to app with an error flag the UI can pick up
        return RedirectResponse("/?fb_error=access_denied#freshbooks")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            config.FRESHBOOKS_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": config.FRESHBOOKS_CLIENT_ID,
                "client_secret": config.FRESHBOOKS_CLIENT_SECRET,
                "redirect_uri": config.FRESHBOOKS_REDIRECT_URI,
                "code": code,
            },
        )

    if resp.status_code != 200:
        return RedirectResponse(f"/?fb_error=token_exchange#{resp.status_code}#freshbooks")

    data = resp.json()
    access_token  = data["access_token"]
    refresh_token = data["refresh_token"]
    expires_in    = data.get("expires_in", 3600)

    # Fetch account_id from the FreshBooks /me endpoint
    async with httpx.AsyncClient(timeout=10) as client:
        me_resp = await client.get(
            f"{config.FRESHBOOKS_API_BASE}/auth/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    account_id = ""
    if me_resp.is_success:
        me = me_resp.json()
        resp_body = me.get("response", {})
        # Primary path: business_memberships[0].business.account_id  (e.g. "61wqkw")
        memberships = resp_body.get("business_memberships", [])
        if memberships:
            account_id = str(memberships[0].get("business", {}).get("account_id", ""))
        # Fallback: older API shape uses roles[0].accountid
        if not account_id:
            roles = resp_body.get("roles", [])
            if roles:
                account_id = str(roles[0].get("accountid", ""))
    # Final fallback: use the known account ID from config
    if not account_id:
        account_id = config.FRESHBOOKS_ACCOUNT_ID

    _save_tokens(access_token, refresh_token, account_id, expires_in)

    # Redirect back to the app on the FreshBooks tab
    return RedirectResponse("/?fb_connected=1#freshbooks")


# ─── API routes (session-authenticated) ───────────────────────────────────

@router.get("/status")
async def fb_status(_user: str = Depends(verify_session)) -> dict:
    """Returns connection state so the UI can show Connect / Connected."""
    tokens = _load_tokens()
    if not tokens:
        return {"connected": False}

    # Check token freshness — refresh silently if needed
    try:
        await _get_valid_access_token()
    except HTTPException:
        return {"connected": False}

    return {
        "connected": True,
        "account_id": tokens["account_id"],
        "expires_at": tokens["expires_at"],
    }


@router.post("/disconnect")
async def fb_disconnect(_user: str = Depends(verify_session)) -> dict:
    _clear_tokens()
    return {"ok": True}


@router.post("/parse", response_model=FbParseResponse)
async def parse_order_pdf(
    file: UploadFile = File(...),
    _user: str = Depends(verify_session),
) -> FbParseResponse:
    """Upload an OSD Sales Order PDF — returns structured line items for review."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    raw = await file.read()
    if len(raw) > _MAX_PDF_BYTES:
        raise HTTPException(400, f"PDF too large (max {_MAX_PDF_BYTES // 1024 // 1024} MB)")

    order = parse_pdf(raw)

    if not order.items:
        raise HTTPException(
            422,
            detail=order.warnings[0] if order.warnings else "No line items found in PDF",
        )

    return FbParseResponse(
        order_number=order.order_number,
        order_date=order.order_date,
        items=[
            FbLineItem(
                item_code=it.item_code,
                description=it.description,
                unit=it.unit,
                qty=it.qty,
                unit_price=it.unit_price,
                amount=it.amount,
                warning=it.warning,
            )
            for it in order.items
        ],
        warnings=order.warnings,
    )


@router.post("/invoice", response_model=FbInvoiceResponse)
async def create_invoice(
    body: FbInvoiceRequest,
    _user: str = Depends(verify_session),
) -> FbInvoiceResponse:
    """Send confirmed line items to FreshBooks and create a draft invoice."""
    if not body.items:
        raise HTTPException(400, "Cannot create an invoice with 0 line items")

    tokens = _load_tokens()
    if not tokens:
        raise HTTPException(401, "FreshBooks not connected — use the Connect button first")

    account_id = tokens["account_id"] or config.FRESHBOOKS_ACCOUNT_ID
    if not account_id:
        raise HTTPException(503, "FreshBooks account ID not available — set FRESHBOOKS_ACCOUNT_ID env var")

    lines = [
        {
            "type": 0,
            "name": item.description,
            "unit_cost": {"amount": f"{item.unit_price:.2f}", "code": "USD"},
            "qty": str(item.qty),
        }
        for item in body.items
    ]

    import datetime
    payload = {
        "invoice": {
            "customerid": config.FRESHBOOKS_CUSTOMER_ID,
            "create_date": body.order_date or datetime.date.today().isoformat(),
            "po_number": body.order_number,
            "terms": config.FRESHBOOKS_DISCLAIMER,
            "status": 1,   # 1=draft, 2=sent/outstanding — skips draft state so invoice is immediately visible
            "lines": lines,
        }
    }

    data = await _fb_post(
        f"/accounting/account/{account_id}/invoices/invoices",
        payload,
    )

    invoice = data.get("response", {}).get("result", {}).get("invoice", {})
    invoice_id     = str(invoice.get("id", ""))
    invoice_number = str(invoice.get("invoice_number") or invoice.get("number") or "")

    return FbInvoiceResponse(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        status=str(invoice.get("v3_status") or invoice.get("status", "created")),
        freshbooks_url=f"https://my.freshbooks.com/#/invoice/{invoice_id}" if invoice_id else None,
    )


@router.get("/invoices", response_model=FbInvoiceListResponse)
async def list_invoices(
    page: int = 1,
    per_page: int = 25,
    search: str = "",
    _user: str = Depends(verify_session),
) -> FbInvoiceListResponse:
    """Return a paginated list of FreshBooks invoices for the connected account."""
    tokens = _load_tokens()
    if not tokens:
        raise HTTPException(401, "FreshBooks not connected")

    account_id = tokens["account_id"] or config.FRESHBOOKS_ACCOUNT_ID
    params: dict[str, str | int] = {
        "page": page,
        "per_page": per_page,
        "customerid": config.FRESHBOOKS_CUSTOMER_ID,
    }
    if search:
        params["search[invoice_number]"] = search

    token = await _get_valid_access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{config.FRESHBOOKS_API_BASE}/accounting/account/{account_id}/invoices/invoices",
            headers={"Authorization": f"Bearer {token}", "Api-Version": "alpha"},
            params=params,
        )
    if not resp.is_success:
        raise HTTPException(502, f"FreshBooks API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    result = data.get("response", {}).get("result", {})
    raw_invoices = result.get("invoices", [])
    total = result.get("total", len(raw_invoices))

    invoices: list[FbInvoiceListItem] = []
    for inv in raw_invoices:
        invoices.append(FbInvoiceListItem(
            invoice_id=str(inv.get("id", "")),
            invoice_number=str(inv.get("invoice_number") or inv.get("number") or ""),
            po_number=str(inv.get("po_number") or ""),
            create_date=str(inv.get("create_date") or ""),
            status=str(inv.get("v3_status") or inv.get("status") or ""),
            total=str(inv.get("amount", {}).get("amount") or inv.get("outstanding", {}).get("amount") or "0.00"),
            lines_count=len(inv.get("lines", [])),
        ))

    return FbInvoiceListResponse(invoices=invoices, total=total)


@router.get("/invoice/{invoice_id}", response_model=FbInvoiceDetail)
async def get_invoice_detail(
    invoice_id: str,
    _user: str = Depends(verify_session),
) -> FbInvoiceDetail:
    """Return full invoice detail including all line items."""
    tokens = _load_tokens()
    if not tokens:
        raise HTTPException(401, "FreshBooks not connected")

    account_id = tokens["account_id"] or config.FRESHBOOKS_ACCOUNT_ID
    data = await _fb_get(
        f"/accounting/account/{account_id}/invoices/invoices/{invoice_id}",
        params={"include[]": "lines"},
    )
    inv = data.get("response", {}).get("result", {}).get("invoice", {})

    lines: list[FbInvoiceLine] = []
    for ln in inv.get("lines", []):
        unit_cost_raw = ln.get("unit_cost", {})
        amount_raw    = ln.get("amount", {})
        lines.append(FbInvoiceLine(
            lineid=ln.get("lineid"),
            name=ln.get("name", ""),
            description=ln.get("description", ""),
            qty=str(ln.get("qty", "")),
            unit_cost=str(unit_cost_raw.get("amount", "0.00") if isinstance(unit_cost_raw, dict) else unit_cost_raw),
            amount=str(amount_raw.get("amount", "0.00") if isinstance(amount_raw, dict) else amount_raw),
        ))

    return FbInvoiceDetail(
        invoice_id=str(inv.get("id", invoice_id)),
        invoice_number=str(inv.get("invoice_number") or inv.get("number") or ""),
        po_number=str(inv.get("po_number") or ""),
        create_date=str(inv.get("create_date") or ""),
        status=str(inv.get("v3_status") or inv.get("status") or ""),
        total=str(inv.get("amount", {}).get("amount") or "0.00"),
        lines=lines,
    )


@router.post("/invoice/{invoice_id}/append", response_model=FbInvoiceResponse)
async def append_to_invoice(
    invoice_id: str,
    body: FbAppendRequest,
    _user: str = Depends(verify_session),
) -> FbInvoiceResponse:
    """Fetch an existing invoice and append new line items to it."""
    if not body.items:
        raise HTTPException(400, "No items to append")

    tokens = _load_tokens()
    if not tokens:
        raise HTTPException(401, "FreshBooks not connected")

    account_id = tokens["account_id"] or config.FRESHBOOKS_ACCOUNT_ID

    # Fetch the existing invoice — include[]=lines ensures line data is returned
    existing = await _fb_get(
        f"/accounting/account/{account_id}/invoices/invoices/{invoice_id}",
        params={"include[]": "lines"},
    )
    inv = existing.get("response", {}).get("result", {}).get("invoice", {})
    existing_lines: list[dict] = inv.get("lines", [])

    # Keep only writable fields; include lineid so FreshBooks preserves each line
    _SKIP = {"subtotal", "amount", "taxes", "updated", "transitional_lineid"}
    clean_lines = []
    for ln in existing_lines:
        kept = {k: v for k, v in ln.items() if k not in _SKIP}
        # qty must be a string for the FreshBooks API
        if "qty" in kept:
            kept["qty"] = str(kept["qty"])
        clean_lines.append(kept)

    # New lines to append (no lineid — FreshBooks will assign one)
    new_lines = [
        {
            "type": 0,
            "name": item.description,
            "unit_cost": {"amount": f"{item.unit_price:.2f}", "code": "USD"},
            "qty": str(item.qty),
        }
        for item in body.items
    ]

    po_number = body.order_number or inv.get("po_number") or ""
    payload = {
        "invoice": {
            "lines": clean_lines + new_lines,
            "po_number": po_number,
        }
    }

    data = await _fb_put(
        f"/accounting/account/{account_id}/invoices/invoices/{invoice_id}",
        payload,
    )

    updated = data.get("response", {}).get("result", {}).get("invoice", {})
    inv_id  = str(updated.get("id", invoice_id))
    inv_num = str(updated.get("invoice_number") or updated.get("number") or "")

    return FbInvoiceResponse(
        invoice_id=inv_id,
        invoice_number=inv_num,
        status=str(updated.get("v3_status") or updated.get("status", "updated")),
        freshbooks_url=f"https://my.freshbooks.com/#/invoice/{inv_id}" if inv_id else None,
    )
