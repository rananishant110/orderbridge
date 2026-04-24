"""String normalization and pack-size extraction.

One place for these rules so matching and diffing agree on what "the same
description" means. Keep it minimal — every abbreviation rule you add is a
potential match miss."""
from __future__ import annotations

import re

_PACK_SIZE_RE = re.compile(r"\b(\d+)\s*[Xx*]\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\b")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Parsed pack size: (count, quantity, unit) e.g. (20, 907.0, 'G')
_ParsedPack = tuple[int, float, str]


def normalize(text: str) -> str:
    if not text:
        return ""
    out = text.upper()
    out = _PUNCT_RE.sub(" ", out)
    out = _WS_RE.sub(" ", out).strip()
    return out


def _parse_pack_size(text: str) -> _ParsedPack | None:
    """Return (count, qty, unit) for the last pack-size token, or None."""
    matches = _PACK_SIZE_RE.findall(text or "")
    if not matches:
        return None
    cnt, qty, unit = matches[-1]
    return int(cnt), float(qty), unit.upper()


def extract_pack_size(text: str) -> str | None:
    """Return the last pack-size-looking token as a canonical string, or None."""
    p = _parse_pack_size(text)
    return f"{p[0]}X{p[1]:.0f}{p[2]}" if p else None


def pack_sizes_compatible(onestop_desc: str, gm_desc: str) -> bool:
    """True when the pack sizes are equivalent (tolerates ±2% weight rounding)."""
    a = _parse_pack_size(onestop_desc)
    if a is None:
        return True
    b = _parse_pack_size(gm_desc)
    if b is None:
        return False
    a_cnt, a_qty, a_unit = a
    b_cnt, b_qty, b_unit = b
    if a_cnt != b_cnt or a_unit != b_unit:
        return False
    # Allow ±2% on the numeric weight to absorb catalog rounding (e.g. 907g vs 908g)
    if a_qty == 0:
        return b_qty == 0
    return abs(a_qty - b_qty) / a_qty <= 0.02
