"""Catalog refresh endpoints — weekly template ingestion and diffing."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config, db
from ..auth import verify_session
from ..schemas import CatalogDiff, SearchResult
from ..services.excel_reader import read_gm_catalog, read_onestop
from ..services.matching import GmIndex

router = APIRouter(prefix="/api/catalogs", tags=["catalogs"])


@router.post("/refresh", response_model=CatalogDiff)
async def refresh(onestop: UploadFile = File(...), gm: UploadFile = File(...), user: str = Depends(verify_session)):
    config.ensure_dirs()

    os_path = config.ONESTOP_TEMPLATE_PATH
    gm_path = config.GM_TEMPLATE_PATH
    os_bytes = await onestop.read()
    gm_bytes = await gm.read()
    os_path.write_bytes(os_bytes)
    gm_path.write_bytes(gm_bytes)

    new_gm = read_gm_catalog(gm_path)
    new_os = read_onestop(os_path)

    with db.session() as conn:
        prev_norms = {r["description_normalized"] for r in conn.execute(
            "SELECT description_normalized FROM onestop_template"
        )}

        conn.execute("DELETE FROM onestop_template")
        conn.executemany(
            "INSERT INTO onestop_template(row_index, description, description_normalized, price, is_header) VALUES(?,?,?,?,?)",
            [(r.row_index, r.description, r.description_normalized, r.price, int(r.is_header)) for r in new_os],
        )

        conn.execute("DELETE FROM gm_catalog")
        conn.executemany(
            """INSERT OR IGNORE INTO gm_catalog(item_no, sheet, side, row_index, description,
                                       description_normalized, price, available)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(r.item_no, r.sheet, r.side, r.row_index, r.description,
              r.description_normalized, r.price, int(r.available)) for r in new_gm],
        )

    new_norms = {r.description_normalized for r in new_os if not r.is_header}
    new_in_onestop = sorted(new_norms - prev_norms)
    removed_from_onestop = sorted(prev_norms - new_norms)

    index = GmIndex(new_gm)
    with db.session() as conn:
        mappings = list(conn.execute(
            "SELECT onestop_desc_normalized, gm_item_no, gm_sheet FROM mapping WHERE gm_item_no IS NOT NULL"
        ))
    changed_gm: list[str] = []
    for m in mappings:
        row = index.by_item(m["gm_item_no"], m["gm_sheet"])
        if row is None:
            changed_gm.append(m["onestop_desc_normalized"])

    return CatalogDiff(
        new_onestop=new_in_onestop,
        removed_onestop=removed_from_onestop,
        changed_gm_match=changed_gm,
        price_changed=[],
    )


@router.get("/search", response_model=list[SearchResult])
def search(q: str, limit: int = 10, user: str = Depends(verify_session)):
    if not config.GM_TEMPLATE_PATH.exists():
        raise HTTPException(400, "No active GM template")
    rows = read_gm_catalog(config.GM_TEMPLATE_PATH)
    idx = GmIndex(rows)
    hits = idx.search(q, limit=limit)
    return [SearchResult(item_no=h.item_no, sheet=h.sheet,
                         description=h.description, price=h.price) for h in hits]


@router.get("/status")
def status():
    return {
        "onestop_template_present": config.ONESTOP_TEMPLATE_PATH.exists(),
        "gm_template_present": config.GM_TEMPLATE_PATH.exists(),
        "onestop_uploaded_at": _mtime(config.ONESTOP_TEMPLATE_PATH),
        "gm_uploaded_at": _mtime(config.GM_TEMPLATE_PATH),
    }


@router.get("/gm")
def gm_listing(user: str = Depends(verify_session)):
    """All GM catalog items grouped by sheet — used by the right-hand pane
    in the side-by-side reconciliation view."""
    if not config.GM_TEMPLATE_PATH.exists():
        raise HTTPException(400, "No active GM template — upload one first")
    rows = read_gm_catalog(config.GM_TEMPLATE_PATH)
    sheets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sheets[r.sheet].append({
            "item_no": r.item_no,
            "description": r.description,
            "price": r.price,
            "side": r.side,
            "row_index": r.row_index,
        })
    # Sort inside each sheet by item number for stable display.
    for items in sheets.values():
        items.sort(key=lambda x: (x["item_no"] is None, x["item_no"] or 0))
    return [
        {"sheet": sheet, "items": items}
        for sheet, items in sorted(sheets.items(), key=lambda kv: kv[0])
    ]


def _mtime(path) -> str | None:
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime).isoformat()
    except FileNotFoundError:
        return None
