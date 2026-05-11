"""PDF order form reader — extracts handwritten quantities from scanned OneStop PDFs.

The customer writes quantities in one of three positions on each row:
  1. The designated QTY column (far left)
  2. End of the description text, before the price
  3. Adjacent to / overlapping the printed price (far right)

All pages are sent to a vision LLM (Gemini or Claude) which understands layout
semantically and returns structured JSON. No pixel-based page filter is used
because quantities appear in variable horizontal positions.
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber
from PIL import Image


# ─── data types ───────────────────────────────────────────────────────────────

@dataclass
class PageImage:
    page_num: int   # 1-indexed
    image: Image.Image


@dataclass
class ExtractedLine:
    description: str
    qty: int
    price: Optional[float]
    source_page: int
    warning: Optional[str] = None


@dataclass
class PdfExtractionResult:
    page_images: list[PageImage]
    lines: list[ExtractedLine]
    warnings: list[str]

    @property
    def page_count(self) -> int:
        return len(self.page_images)

    @property
    def first_page_with_results(self) -> int:
        pages = {l.source_page for l in self.lines}
        return min(pages) if pages else 1


# ─── helpers ──────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models add despite being asked not to."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_items(raw: str, page_num: int) -> tuple[list[ExtractedLine], list[str]]:
    """Parse a JSON array from the model response into ExtractedLine objects."""
    lines: list[ExtractedLine] = []
    warnings: list[str] = []

    cleaned = _strip_fences(raw)
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        warnings.append(f"Page {page_num}: could not parse model response as JSON — add items manually")
        return lines, warnings

    if not isinstance(items, list):
        warnings.append(f"Page {page_num}: unexpected response format (expected JSON array)")
        return lines, warnings

    for item in items:
        try:
            desc = str(item.get("description", "")).strip()
            qty_raw = item.get("qty", 0)
            qty = int(float(qty_raw)) if qty_raw is not None else 0
            price_raw = item.get("price")
            price = float(price_raw) if price_raw is not None else None

            if not desc or qty <= 0:
                continue

            lines.append(ExtractedLine(
                description=desc,
                qty=qty,
                price=price,
                source_page=page_num,
            ))
        except (TypeError, ValueError, AttributeError):
            warnings.append(f"Page {page_num}: skipped malformed item {item!r}")

    return lines, warnings


# ─── vision provider calls ────────────────────────────────────────────────────

def _call_gemini(image: Image.Image, prompt: str, api_key: str, model: str) -> str:
    import google.genai as genai  # lazy import — only needed if provider=gemini

    client = genai.Client(api_key=api_key)
    # Strip "models/" prefix if present — the new SDK wants bare model IDs
    model_id = model.removeprefix("models/")
    response = client.models.generate_content(
        model=model_id,
        contents=[image, prompt],
    )
    return response.text


def _call_anthropic(image: Image.Image, prompt: str, api_key: str, model: str) -> str:
    import anthropic  # lazy import — only needed if provider=anthropic

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode()

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text


def _call_vision(image: Image.Image, prompt: str, provider: str, api_key: str, model: str) -> str:
    if provider == "gemini":
        return _call_gemini(image, prompt, api_key, model)
    elif provider == "anthropic":
        return _call_anthropic(image, prompt, api_key, model)
    else:
        raise ValueError(f"Unknown PDF_VISION_PROVIDER: {provider!r}. Use 'gemini' or 'anthropic'.")


# ─── public pipeline ──────────────────────────────────────────────────────────

def has_text_layer(pdf_bytes: bytes) -> bool:
    """Return True if the PDF has a selectable text layer (digital PDF, not a scan)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            return False
        text = pdf.pages[0].extract_text()
        return bool(text and text.strip())


def extract_page_images(pdf_bytes: bytes, resolution: int = 150) -> list[PageImage]:
    """Render every PDF page to a PIL Image via pdfplumber (no poppler required)."""
    images: list[PageImage] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            pil_img = page.to_image(resolution=resolution).original
            images.append(PageImage(page_num=page_num, image=pil_img))
    return images


def extract_via_vision(
    images: list[PageImage],
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
) -> tuple[list[ExtractedLine], list[str]]:
    """Send every page to the vision LLM and aggregate extracted lines."""
    all_lines: list[ExtractedLine] = []
    all_warnings: list[str] = []

    # Validate provider before processing any pages
    if provider not in ("gemini", "anthropic"):
        raise ValueError(f"Unknown PDF_VISION_PROVIDER: {provider!r}. Use 'gemini' or 'anthropic'.")

    for page_img in images:
        try:
            raw = _call_vision(page_img.image, prompt, provider, api_key, model)
        except Exception as e:
            all_warnings.append(f"Page {page_img.page_num}: API error — {e}")
            continue

        # First parse attempt
        lines, warnings = _parse_items(raw, page_img.page_num)
        if warnings and not lines:
            # Retry once with an explicit format nudge
            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Return ONLY a JSON array, nothing else."
            )
            try:
                raw2 = _call_vision(page_img.image, retry_prompt, provider, api_key, model)
                lines, warnings = _parse_items(raw2, page_img.page_num)
            except Exception as e:
                all_warnings.append(f"Page {page_img.page_num}: retry failed — {e}")
                continue

        all_lines.extend(lines)
        all_warnings.extend(warnings)

    return all_lines, all_warnings


def to_onestop_rows(lines: list) -> list:
    """Convert confirmed PDF lines (dicts or PdfConfirmLine) to OnestopRow objects."""
    from .excel_reader import OnestopRow
    from .normalize import normalize

    rows = []
    for idx, line in enumerate(lines, start=1):
        if hasattr(line, "description"):
            desc, qty, price = line.description, line.qty, line.price
        else:
            desc = str(line.get("description", "")).strip()
            qty = int(line.get("qty", 0))
            price = line.get("price")

        if not desc or qty <= 0:
            continue

        rows.append(OnestopRow(
            row_index=idx,
            qty=qty,
            description=desc,
            description_normalized=normalize(desc),
            price=float(price) if price is not None else None,
            is_header=False,
        ))
    return rows


def read_pdf_order(pdf_bytes: bytes) -> PdfExtractionResult:
    """Full extraction pipeline. Raises ValueError on configuration errors."""
    from .. import config as cfg

    if not cfg.PDF_VISION_API_KEY:
        provider_hint = (
            "Set GOOGLE_API_KEY (get one free at aistudio.google.com)"
            if cfg.PDF_VISION_PROVIDER == "gemini"
            else "Set ANTHROPIC_API_KEY"
        )
        raise ValueError(
            f"No API key configured for PDF extraction. {provider_hint}."
        )

    # Fast path: digital PDF with selectable text (future proofing)
    if has_text_layer(pdf_bytes):
        # Text layer exists — fall through for now (v1 uses image path for all PDFs)
        # A future version can branch here to the pdfplumber text parser.
        pass

    images = extract_page_images(pdf_bytes, resolution=cfg.PDF_PAGE_RESOLUTION)

    if len(images) > cfg.PDF_MAX_PAGES:
        raise ValueError(
            f"PDF has {len(images)} pages; maximum is {cfg.PDF_MAX_PAGES}. "
            "Split the file or contact support."
        )

    warnings: list[str] = []

    # Send all pages — no column-specific filter; quantities appear anywhere on the row
    lines, extraction_warnings = extract_via_vision(
        images,
        provider=cfg.PDF_VISION_PROVIDER,
        api_key=cfg.PDF_VISION_API_KEY,
        model=cfg.PDF_VISION_MODEL,
        prompt=cfg.PDF_EXTRACTION_PROMPT,
    )
    warnings.extend(extraction_warnings)

    if not lines:
        warnings.append(
            "No ordered items were detected in this PDF. "
            "If items were ordered, add them manually in the review step."
        )

    return PdfExtractionResult(page_images=images, lines=lines, warnings=warnings)
