"""OneStop-description → GM-item# matching.

Priority order (see PRD §8):
  1. Learned mapping (user-confirmed) — confidence 1.0
  2. Exact normalized match             — confidence 1.0
  3. Fuzzy match (rapidfuzz token_set_ratio)
        score >= 0.92 → auto
        0.75–0.92     → review
        <0.75         → unmatched
  Pack-size guard: if OneStop has a pack size, GM candidate must match or it's
  demoted to review regardless of raw score.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

from rapidfuzz import fuzz, process

from .. import config
from .excel_reader import GmRow, OnestopRow
from .normalize import pack_sizes_compatible


@dataclass
class MatchResult:
    onestop: OnestopRow
    bucket: str              # "auto" | "review" | "unmatched"
    picked: Optional[GmRow] = None
    candidates: list[tuple[GmRow, float]] = None  # sorted desc by score
    score: float = 0.0
    reason: str = ""

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


class GmIndex:
    """Search index over the full GM catalog.

    Implements the specialized-sheet-over-REST-LIST preference from PRD §8.
    """

    def __init__(self, rows: Iterable[GmRow]):
        self.rows: list[GmRow] = list(rows)
        self._by_norm: dict[str, list[GmRow]] = defaultdict(list)
        self._by_item: dict[int, list[GmRow]] = defaultdict(list)
        for r in self.rows:
            self._by_norm[r.description_normalized].append(r)
            self._by_item[r.item_no].append(r)
        self._norm_keys: list[str] = list(self._by_norm.keys())

    # ---- lookup helpers ---------------------------------------------------

    def by_item(self, item_no: int, sheet: Optional[str] = None) -> Optional[GmRow]:
        rows = self._by_item.get(item_no, [])
        if not rows:
            return None
        if sheet:
            for r in rows:
                if r.sheet == sheet:
                    return r
        return self._prefer_specialized(rows)

    def exact(self, norm: str) -> Optional[GmRow]:
        rows = self._by_norm.get(norm)
        return self._prefer_specialized(rows) if rows else None

    def fuzzy(self, norm: str, limit: int = 5) -> list[tuple[GmRow, float]]:
        if not norm:
            return []
        hits = process.extract(
            norm, self._norm_keys, scorer=fuzz.token_set_ratio, limit=limit
        )
        out: list[tuple[GmRow, float]] = []
        for key, score, _ in hits:
            row = self._prefer_specialized(self._by_norm[key])
            if row is not None:
                out.append((row, score / 100.0))
        return out

    def search(self, query: str, limit: int = 10) -> list[GmRow]:
        """Free-text search for the UI item picker."""
        if not query:
            return []
        hits = process.extract(
            query.upper(), self._norm_keys, scorer=fuzz.WRatio, limit=limit
        )
        return [self._prefer_specialized(self._by_norm[k]) for k, _, _ in hits if self._by_norm[k]]

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _prefer_specialized(rows: list[GmRow]) -> Optional[GmRow]:
        if not rows:
            return None
        for r in rows:
            if r.sheet != config.REST_LIST_SHEET:
                return r
        return rows[0]


# ---------- matching loop --------------------------------------------------

def match_row(
    row: OnestopRow,
    index: GmIndex,
    learned: dict[str, tuple[Optional[int], Optional[str]]],
) -> MatchResult:
    """Classify a single OneStop row into auto / review / unmatched."""

    # 1) learned mapping
    mapped = learned.get(row.description_normalized)
    if mapped is not None:
        item_no, sheet = mapped
        if item_no is None:
            return MatchResult(onestop=row, bucket="unmatched",
                               reason="marked OneStop-exclusive previously")
        gm = index.by_item(item_no, sheet)
        if gm is not None:
            return MatchResult(onestop=row, bucket="auto", picked=gm,
                               score=1.0, reason="learned mapping")
        # The learned item# is no longer in the GM catalog — fall through to fuzzy
        # and flag for review.

    # 2) exact normalized
    gm = index.exact(row.description_normalized)
    if gm is not None:
        return MatchResult(onestop=row, bucket="auto", picked=gm,
                           score=1.0, reason="exact match")

    # 3) fuzzy
    candidates = index.fuzzy(row.description_normalized, limit=5)
    if not candidates:
        return MatchResult(onestop=row, bucket="unmatched",
                           reason="no similar item in GM catalog")

    top, top_score = candidates[0]
    pack_ok = pack_sizes_compatible(row.description, top.description)

    if top_score >= config.AUTO_ACCEPT_SCORE and pack_ok:
        return MatchResult(
            onestop=row, bucket="auto", picked=top,
            candidates=candidates, score=top_score,
            reason=f"fuzzy {top_score:.2f}",
        )
    if top_score >= config.REVIEW_FLOOR_SCORE:
        reason = f"fuzzy {top_score:.2f}"
        if not pack_ok:
            reason += " — pack size mismatch"
        return MatchResult(
            onestop=row, bucket="review", picked=top,
            candidates=candidates, score=top_score, reason=reason,
        )
    return MatchResult(
        onestop=row, bucket="unmatched", candidates=candidates,
        score=top_score, reason=f"best fuzzy only {top_score:.2f}",
    )


def match_all(
    rows: Iterable[OnestopRow],
    index: GmIndex,
    learned: dict[str, tuple[Optional[int], Optional[str]]],
) -> list[MatchResult]:
    return [match_row(r, index, learned) for r in rows if r.qty > 0 and not r.is_header]
