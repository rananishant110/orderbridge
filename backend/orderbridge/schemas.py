"""Pydantic request / response schemas."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GmCandidate(BaseModel):
    item_no: int
    sheet: str
    description: str
    price: Optional[float] = None
    score: float = Field(..., description="0–1 similarity; 1.0 for exact / learned")


class OrderLine(BaseModel):
    row_index: int
    onestop_desc: str
    qty: int
    bucket: Literal["auto", "review", "unmatched"]
    picked: Optional[GmCandidate] = None
    candidates: list[GmCandidate] = []
    reason: Optional[str] = None


class OrderUploadResponse(BaseModel):
    run_id: str
    auto: list[OrderLine]
    review: list[OrderLine]
    unmatched: list[OrderLine]


class Resolution(BaseModel):
    row_index: int
    choice: Literal["accept", "pick", "onestop_only"]
    gm_item_no: Optional[int] = None
    gm_sheet: Optional[str] = None
    remember: bool = True


class ApplyRequest(BaseModel):
    run_id: str
    resolutions: list[Resolution] = []


class ApplyResponse(BaseModel):
    run_id: str
    download_url: str
    lines_written: int


class CatalogDiff(BaseModel):
    new_onestop: list[str] = []
    removed_onestop: list[str] = []
    changed_gm_match: list[str] = []
    price_changed: list[str] = []


class SearchResult(BaseModel):
    item_no: int
    sheet: str
    description: str
    price: Optional[float] = None


# ─── FreshBooks invoice schemas ───────────────────────────────────────────

class FbLineItem(BaseModel):
    item_code: str
    description: str
    unit: str
    qty: float
    unit_price: float
    amount: float
    warning: Optional[str] = None


class FbParseResponse(BaseModel):
    order_number: str
    order_date: str
    items: list[FbLineItem]
    warnings: list[str] = []


class FbInvoiceRequest(BaseModel):
    order_number: str
    order_date: str          # YYYY-MM-DD
    items: list[FbLineItem]


class FbInvoiceResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    status: str
    freshbooks_url: Optional[str] = None


class FbInvoiceListItem(BaseModel):
    invoice_id: str
    invoice_number: str
    po_number: str
    create_date: str
    status: str
    total: str
    lines_count: int


class FbInvoiceListResponse(BaseModel):
    invoices: list[FbInvoiceListItem]
    total: int


class FbInvoiceLine(BaseModel):
    lineid: int | None = None
    name: str
    description: str = ""
    qty: str
    unit_cost: str   # dollar amount string e.g. "12.50"
    amount: str      # line total


class FbInvoiceDetail(BaseModel):
    invoice_id: str
    invoice_number: str
    po_number: str
    create_date: str
    status: str
    total: str
    lines: list[FbInvoiceLine]


class FbAppendRequest(BaseModel):
    items: list[FbLineItem]
    order_number: str = ""
