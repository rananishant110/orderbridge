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
