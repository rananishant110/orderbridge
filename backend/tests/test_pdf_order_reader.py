"""Unit tests for services/pdf_order_reader.py.

Vision API calls are monkeypatched — no real API key required to run the suite.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from orderbridge.services.pdf_order_reader import (
    ExtractedLine,
    PageImage,
    PdfExtractionResult,
    _parse_items,
    _strip_fences,
    extract_via_vision,
    has_text_layer,
    to_onestop_rows,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _white_image(w: int = 400, h: int = 600) -> Image.Image:
    return Image.new("RGB", (w, h), color=(255, 255, 255))


def _make_page(page_num: int = 1) -> PageImage:
    return PageImage(page_num=page_num, image=_white_image())


def _minimal_image_pdf() -> bytes:
    """Build a minimal single-page PDF whose only content is a white JPEG image (no text)."""
    img = _white_image(100, 100)
    img_buf = io.BytesIO()
    img.save(img_buf, format="JPEG")
    img_data = img_buf.getvalue()
    img_len = len(img_data)

    xobject = (
        b"1 0 obj\n<</Type /XObject /Subtype /Image /Width 100 /Height 100"
        b" /ColorSpace /DeviceRGB /BitsPerComponent 8"
        b" /Filter /DCTDecode /Length " + str(img_len).encode() + b">>\nstream\n"
        + img_data + b"\nendstream\nendobj\n"
    )
    content_stream = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    content = (
        b"2 0 obj\n<</Length " + str(len(content_stream)).encode() + b">>\nstream\n"
        + content_stream + b"\nendstream\nendobj\n"
    )
    page = (b"3 0 obj\n<</Type /Page /Parent 4 0 R /MediaBox [0 0 100 100]"
            b" /Contents 2 0 R /Resources <</XObject <</Im1 1 0 R>>>>>>\nendobj\n")
    pages = b"4 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
    catalog = b"5 0 obj\n<</Type /Catalog /Pages 4 0 R>>\nendobj\n"

    body = xobject + content + page + pages + catalog
    xref_offset = len(b"%PDF-1.4\n") + len(body)
    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
    )
    trailer = (
        b"trailer\n<</Size 6 /Root 5 0 R>>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    )
    return b"%PDF-1.4\n" + body + xref + trailer


# ─── _strip_fences ────────────────────────────────────────────────────────────

def test_strip_fences_removes_json_block():
    assert _strip_fences("```json\n[]\n```") == "[]"


def test_strip_fences_removes_plain_block():
    assert _strip_fences("```\n[]\n```") == "[]"


def test_strip_fences_passthrough_clean_json():
    raw = '[{"description":"X","qty":1,"price":null}]'
    assert _strip_fences(raw) == raw


# ─── _parse_items ─────────────────────────────────────────────────────────────

def test_parse_items_valid():
    raw = '[{"description":"BASMATI RICE 20LB","qty":3,"price":42.0}]'
    lines, warns = _parse_items(raw, page_num=1)
    assert len(lines) == 1
    assert lines[0].description == "BASMATI RICE 20LB"
    assert lines[0].qty == 3
    assert lines[0].price == 42.0
    assert lines[0].source_page == 1
    assert warns == []


def test_parse_items_empty_array():
    lines, warns = _parse_items("[]", page_num=2)
    assert lines == []
    assert warns == []


def test_parse_items_skips_zero_qty():
    raw = '[{"description":"ITEM A","qty":0,"price":null}]'
    lines, warns = _parse_items(raw, page_num=1)
    assert lines == []


def test_parse_items_skips_negative_qty():
    raw = '[{"description":"ITEM A","qty":-1,"price":null}]'
    lines, warns = _parse_items(raw, page_num=1)
    assert lines == []


def test_parse_items_skips_empty_description():
    raw = '[{"description":"","qty":2,"price":10.0}]'
    lines, warns = _parse_items(raw, page_num=1)
    assert lines == []


def test_parse_items_bad_json_returns_warning():
    lines, warns = _parse_items("not valid json", page_num=3)
    assert lines == []
    assert len(warns) == 1
    assert "3" in warns[0]  # page number mentioned


def test_parse_items_strips_code_fences():
    raw = "```json\n[{\"description\":\"OIL 5L\",\"qty\":1,\"price\":55.0}]\n```"
    lines, warns = _parse_items(raw, page_num=1)
    assert len(lines) == 1
    assert lines[0].description == "OIL 5L"


def test_parse_items_null_price():
    raw = '[{"description":"FLOUR 20LB","qty":2,"price":null}]'
    lines, warns = _parse_items(raw, page_num=1)
    assert lines[0].price is None


def test_parse_items_multiple_rows():
    raw = (
        '[{"description":"RICE 20LB","qty":2,"price":40.0},'
        '{"description":"OIL 5L","qty":1,"price":55.0}]'
    )
    lines, warns = _parse_items(raw, page_num=5)
    assert len(lines) == 2
    assert lines[0].source_page == 5
    assert lines[1].source_page == 5


# ─── to_onestop_rows ──────────────────────────────────────────────────────────

def test_to_onestop_rows_from_confirm_lines():
    from orderbridge.schemas import PdfConfirmLine
    rows = to_onestop_rows([
        PdfConfirmLine(description="BASMATI RICE 20LB", qty=3, price=42.0),
    ])
    assert len(rows) == 1
    assert rows[0].qty == 3
    assert rows[0].description == "BASMATI RICE 20LB"
    assert rows[0].price == 42.0
    assert rows[0].is_header is False
    assert rows[0].description_normalized  # non-empty after normalize()


def test_to_onestop_rows_from_dicts():
    rows = to_onestop_rows([{"description": "OIL 5L", "qty": 2, "price": None}])
    assert len(rows) == 1
    assert rows[0].qty == 2
    assert rows[0].price is None


def test_to_onestop_rows_skips_zero_qty():
    from orderbridge.schemas import PdfConfirmLine
    rows = to_onestop_rows([
        PdfConfirmLine(description="ITEM A", qty=0, price=None),
        PdfConfirmLine(description="ITEM B", qty=2, price=None),
    ])
    assert len(rows) == 1
    assert rows[0].description == "ITEM B"


def test_to_onestop_rows_skips_empty_description():
    rows = to_onestop_rows([{"description": "", "qty": 5, "price": None}])
    assert rows == []


def test_to_onestop_rows_sequential_row_index():
    from orderbridge.schemas import PdfConfirmLine
    rows = to_onestop_rows([
        PdfConfirmLine(description="A", qty=1, price=None),
        PdfConfirmLine(description="B", qty=2, price=None),
        PdfConfirmLine(description="C", qty=3, price=None),
    ])
    assert [r.row_index for r in rows] == [1, 2, 3]


# ─── PdfExtractionResult properties ───────────────────────────────────────────

def test_extraction_result_page_count():
    result = PdfExtractionResult(
        page_images=[_make_page(1), _make_page(2)],
        lines=[],
        warnings=[],
    )
    assert result.page_count == 2


def test_extraction_result_first_page_with_results():
    result = PdfExtractionResult(
        page_images=[_make_page(i) for i in range(1, 6)],
        lines=[
            ExtractedLine("RICE", 2, None, source_page=3),
            ExtractedLine("OIL", 1, None, source_page=5),
        ],
        warnings=[],
    )
    assert result.first_page_with_results == 3


def test_extraction_result_first_page_defaults_to_1_when_no_lines():
    result = PdfExtractionResult(page_images=[_make_page(1)], lines=[], warnings=[])
    assert result.first_page_with_results == 1


# ─── has_text_layer ───────────────────────────────────────────────────────────

def test_has_text_layer_false_for_scanned(tmp_path):
    # Use the sample PDF which is confirmed to have no text layer.
    import os
    sample = "/Users/neelam/Desktop/apps/OrderBridge/One Stop.pdf"
    if not os.path.exists(sample):
        pytest.skip("Sample PDF not available")
    pdf_bytes = open(sample, "rb").read()
    assert has_text_layer(pdf_bytes) is False


# ─── extract_via_vision ───────────────────────────────────────────────────────

def _make_mock_gemini_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def test_extract_via_vision_gemini_parses_valid_json():
    pages = [_make_page(1)]
    mock_resp = _make_mock_gemini_response(
        '[{"description":"COCONUT OIL 5L","qty":2,"price":55.0}]'
    )
    with patch("orderbridge.services.pdf_order_reader._call_gemini", return_value=mock_resp.text):
        lines, warns = extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")
    assert len(lines) == 1
    assert lines[0].description == "COCONUT OIL 5L"
    assert lines[0].qty == 2
    assert warns == []


def test_extract_via_vision_gemini_empty_page():
    pages = [_make_page(1)]
    with patch("orderbridge.services.pdf_order_reader._call_gemini", return_value="[]"):
        lines, warns = extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")
    assert lines == []
    assert warns == []


def test_extract_via_vision_api_error_produces_warning_not_exception():
    pages = [_make_page(1)]
    with patch("orderbridge.services.pdf_order_reader._call_gemini", side_effect=Exception("timeout")):
        lines, warns = extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")
    assert lines == []
    assert len(warns) == 1
    assert "API error" in warns[0]


def test_extract_via_vision_retries_on_bad_json():
    pages = [_make_page(1)]
    call_count = 0

    def fake_call(image, prompt, api_key, model):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "this is not json"
        return '[{"description":"RICE","qty":1,"price":null}]'

    with patch("orderbridge.services.pdf_order_reader._call_gemini", side_effect=fake_call):
        lines, warns = extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")

    assert call_count == 2
    assert len(lines) == 1
    assert lines[0].description == "RICE"


def test_extract_via_vision_unknown_provider_raises():
    pages = [_make_page(1)]
    with pytest.raises(ValueError, match="Unknown PDF_VISION_PROVIDER"):
        extract_via_vision(pages, "azure", "key", "model", "prompt")


def test_extract_via_vision_sends_all_pages():
    """No page filter — all pages must be sent to the vision API."""
    pages = [_make_page(i) for i in range(1, 6)]
    received = []

    def fake_call(image, prompt, api_key, model):
        received.append(image)
        return "[]"

    with patch("orderbridge.services.pdf_order_reader._call_gemini", side_effect=fake_call):
        extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")

    assert len(received) == 5  # all 5 pages sent, no filter


def test_extract_via_vision_multi_page_aggregates():
    pages = [_make_page(1), _make_page(2), _make_page(3)]
    responses = [
        '[{"description":"RICE","qty":2,"price":40.0}]',
        "[]",
        '[{"description":"OIL","qty":1,"price":55.0}]',
    ]
    call_idx = 0

    def fake_call(image, prompt, api_key, model):
        nonlocal call_idx
        r = responses[call_idx]
        call_idx += 1
        return r

    with patch("orderbridge.services.pdf_order_reader._call_gemini", side_effect=fake_call):
        lines, warns = extract_via_vision(pages, "gemini", "fake-key", "gemini-1.5-flash", "prompt")

    assert len(lines) == 2
    assert lines[0].source_page == 1
    assert lines[1].source_page == 3


# ─── read_pdf_order ───────────────────────────────────────────────────────────

def test_read_pdf_order_raises_if_no_api_key(monkeypatch):
    import orderbridge.config as cfg
    monkeypatch.setattr(cfg, "PDF_VISION_API_KEY", "")
    from orderbridge.services.pdf_order_reader import read_pdf_order
    with pytest.raises(ValueError, match="No API key"):
        read_pdf_order(b"fake")


def test_read_pdf_order_raises_if_too_many_pages(monkeypatch):
    import orderbridge.config as cfg
    monkeypatch.setattr(cfg, "PDF_VISION_API_KEY", "fake-key")
    monkeypatch.setattr(cfg, "PDF_MAX_PAGES", 2)

    fake_images = [_make_page(i) for i in range(1, 4)]  # 3 pages > limit of 2
    from orderbridge.services.pdf_order_reader import read_pdf_order
    with patch("orderbridge.services.pdf_order_reader.extract_page_images", return_value=fake_images):
        with patch("orderbridge.services.pdf_order_reader.has_text_layer", return_value=False):
            with pytest.raises(ValueError, match="maximum"):
                read_pdf_order(b"fake")
