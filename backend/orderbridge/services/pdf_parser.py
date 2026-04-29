"""PDF parser for OSD (OneStop Distribution) Sales Order PDFs.

pdfplumber's text layout for these PDFs differs from n8n's pdf-parse output:

  n8n:        23738 CS 180.0036.005.0000LITTLE INDIA EXTRA LONG BASMATI RICE 4X10LB
  pdfplumber: 23738 LITTLE INDIA EXTRA LONG BASMATI RICE 4X10LB CS 5.0000 36.00 180.00

So column order in pdfplumber text is:
  ITEM_CODE  DESCRIPTION  UNIT  QTY  UNIT_PRICE  AMOUNT

Multi-page PDFs repeat the full header block on every page — stripped here.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber


@dataclass
class ParsedItem:
    item_code: str
    unit: str
    description: str
    qty: float
    unit_price: float
    amount: float
    parse_ok: bool = True
    warning: Optional[str] = None


@dataclass
class ParsedOrder:
    order_number: str
    order_date: str          # YYYY-MM-DD or "" if not found
    items: list[ParsedItem]
    warnings: list[str]


# ─── regex patterns ────────────────────────────────────────────────────────

# Strips "Continued → full page header → column banner" between pages.
# pdfplumber inserts a \n between pages; "Continued" appears at page-bottom.
_PAGE_BREAK_RE = re.compile(
    r"Continued\s*\n[\s\S]*?Item Code\s+Unit(?:\s+Ordered)?\s+Price\s+Amount[ \t]*",
    re.IGNORECASE,
)

# Fallback: strip any *repeated* column banner (in case "Continued" is absent).
_COL_BANNER_RE = re.compile(
    r"Item Code\s+Unit(?:\s+Ordered)?\s+Price\s+Amount[ \t]*",
    re.IGNORECASE,
)

# Column header (used to locate the start of the items section).
_COL_HEADER_RE = re.compile(
    r"Item Code\s+Unit(?:\s+Ordered)?\s+Price\s+Amount",
    re.IGNORECASE,
)

# Order metadata
_ORDER_NUM_RE  = re.compile(r"Order\s*Number[:\s]*(\d+)", re.IGNORECASE)
_ORDER_DATE_RE = re.compile(r"Order Date[:\s]+(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)

# Item line: ITEM_CODE  DESCRIPTION  UNIT  QTY  UNIT_PRICE  AMOUNT
# Description is everything between code and the trailing UNIT + numbers block.
# .*? is non-greedy so it stops at the *last* valid UNIT+numbers sequence.
_ITEM_LINE_RE = re.compile(
    r"^(\d{3,})\s+(.*?)\s+([A-Z]{2,3})\s+([\d,]+\.[\d]+)\s+([\d,]+\.[\d]+)\s+([\d,]+\.[\d]+)\s*$"
)


# ─── helpers ───────────────────────────────────────────────────────────────

def _clean(n: str) -> float:
    return float(n.replace(",", ""))


# ─── public API ────────────────────────────────────────────────────────────

def parse_pdf(pdf_bytes: bytes) -> ParsedOrder:
    """Extract and parse line items from an OSD Sales Order PDF."""

    # 1. Extract text from all pages.
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # 2. Strip repeated page headers (multi-page PDFs).
    text = _PAGE_BREAK_RE.sub("\n", raw_text)

    # 2b. Fallback strip for any remaining duplicate column banners.
    first_banner = True

    def _keep_first(m: re.Match) -> str:
        nonlocal first_banner
        if first_banner:
            first_banner = False
            return m.group()
        return "\n"

    text = _COL_BANNER_RE.sub(_keep_first, text)

    # 3. Pull order metadata.
    order_number = ""
    m = _ORDER_NUM_RE.search(text)
    if m:
        order_number = m.group(1)

    order_date = ""
    m = _ORDER_DATE_RE.search(text)
    if m:
        order_date = f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"

    # 4. Locate the items section.
    # pdfplumber reads the PDF footer as two columns on one line, so
    # "TERMS AND CONDITIONS: …" can appear BEFORE "Net Order:" in the string.
    # Use whichever end-sentinel comes first.
    col_hdr = _COL_HEADER_RE.search(text)

    _candidates = [
        i for i in (text.find("Net Order:"), text.find("TERMS AND CONDITIONS:"))
        if i != -1
    ]
    end_idx = min(_candidates) if _candidates else -1

    if col_hdr is None or end_idx == -1 or end_idx <= col_hdr.end():
        return ParsedOrder(
            order_number=order_number,
            order_date=order_date,
            items=[],
            warnings=["Could not locate line items section in PDF"],
        )

    section = text[col_hdr.end(): end_idx].strip()
    section = re.sub(r"[ \t]+", " ", section)
    section = re.sub(r"\n+", "\n", section).strip()
    lines = [l.strip() for l in section.split("\n") if l.strip()]

    # 5. Parse items.
    items: list[ParsedItem] = []
    warnings: list[str] = []
    current: Optional[ParsedItem] = None

    for line in lines:
        # Skip the standalone "Ordered" header remnant that appears after each
        # page-break strip (pdfplumber keeps it on its own line).
        if re.match(r"^Ordered$", line, re.IGNORECASE):
            continue

        m = _ITEM_LINE_RE.match(line)
        if m:
            if current is not None:
                current.description = current.description.strip()
                items.append(current)

            item_code = m.group(1)
            description = m.group(2).strip()
            unit = m.group(3)
            qty        = _clean(m.group(4))
            unit_price = _clean(m.group(5))
            amount     = _clean(m.group(6))

            # Sanity-check: amount should ≈ qty × unit_price
            expected = round(unit_price * qty, 2)
            ok = abs(expected - amount) < 0.02

            w = None
            if not ok:
                w = f"[{item_code}] amount {amount} ≠ {qty} × {unit_price} (expected {expected})"
                warnings.append(w)

            current = ParsedItem(
                item_code=item_code,
                unit=unit,
                description=description,
                qty=qty,
                unit_price=unit_price,
                amount=amount,
                parse_ok=ok,
                warning=w,
            )
        elif current is not None:
            # Continuation line — append to description.
            sep = " " if current.description else ""
            current.description += sep + line

    if current is not None:
        current.description = current.description.strip()
        items.append(current)

    if not items:
        warnings.append("Parser found 0 line items — check PDF format")

    return ParsedOrder(
        order_number=order_number,
        order_date=order_date,
        items=items,
        warnings=warnings,
    )
