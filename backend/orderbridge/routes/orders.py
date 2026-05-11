"""Order processing endpoints — upload → review → apply → download."""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import config, db
from ..auth import verify_session
from ..schemas import (
    ApplyRequest,
    ApplyResponse,
    GmCandidate,
    OrderLine,
    OrderUploadResponse,
    PdfConfirmRequest,
    PdfExtractedLine,
    PdfUploadResponse,
)
from ..services.excel_reader import read_gm_catalog, read_onestop
from ..services.excel_writer import OrderWrite, write_quantities
from ..services.matching import GmIndex, match_all
from ..services.pdf_order_reader import read_pdf_order, to_onestop_rows

router = APIRouter(prefix="/api/orders", tags=["orders"])


# In-memory staging for runs between upload and apply. A restart loses them,
# which is acceptable — the UI reuploads. For persistence we'd use SQLite,
# but per PLAN §6.6 runs are ephemeral by design.
_RUN_STAGING: dict[str, dict] = {}


def _gm_index() -> GmIndex:
    if not config.GM_TEMPLATE_PATH.exists():
        raise HTTPException(400, "No active GM template — upload one via /api/catalogs/refresh")
    return GmIndex(read_gm_catalog(config.GM_TEMPLATE_PATH))


def _load_learned() -> dict[str, tuple[int | None, str | None]]:
    with db.session() as conn:
        cur = conn.execute(
            "SELECT onestop_desc_normalized, gm_item_no, gm_sheet FROM mapping"
        )
        return {r["onestop_desc_normalized"]: (r["gm_item_no"], r["gm_sheet"]) for r in cur}


@router.post("/upload", response_model=OrderUploadResponse)
async def upload(file: UploadFile = File(...), user: str = Depends(verify_session)):
    _user = user
    run_id = uuid.uuid4().hex[:12]
    tmp_path = config.RUNS_DIR / f"{run_id}__{file.filename}"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(await file.read())

    rows = read_onestop(tmp_path, only_with_qty=True)
    index = _gm_index()
    learned = _load_learned()
    results = match_all(rows, index, learned)

    def line(r) -> OrderLine:
        picked = None
        if r.picked is not None:
            picked = GmCandidate(
                item_no=r.picked.item_no, sheet=r.picked.sheet,
                description=r.picked.description, price=r.picked.price,
                score=r.score,
            )
        cands = [
            GmCandidate(item_no=c.item_no, sheet=c.sheet,
                        description=c.description, price=c.price, score=s)
            for c, s in r.candidates
        ]
        return OrderLine(
            row_index=r.onestop.row_index,
            onestop_desc=r.onestop.description,
            qty=r.onestop.qty,
            bucket=r.bucket,
            picked=picked,
            candidates=cands,
            reason=r.reason,
        )

    auto = [line(r) for r in results if r.bucket == "auto"]
    review = [line(r) for r in results if r.bucket == "review"]
    unmatched = [line(r) for r in results if r.bucket == "unmatched"]

    _RUN_STAGING[run_id] = {
        "filename": file.filename,
        "uploaded_at": datetime.utcnow().isoformat(),
        "uploaded_by": _user,
        "upload_path": str(tmp_path),
        "lines": {r.onestop.row_index: r for r in results},
    }
    return OrderUploadResponse(run_id=run_id, auto=auto, review=review, unmatched=unmatched)


@router.post("/apply", response_model=ApplyResponse)
def apply(req: ApplyRequest, user: str = Depends(verify_session)):
    _user = user
    stage = _RUN_STAGING.get(req.run_id)
    if stage is None:
        raise HTTPException(404, "Unknown run_id — upload again")
    index = _gm_index()

    writes: list[OrderWrite] = []
    lines = stage["lines"]
    resolutions = {r.row_index: r for r in req.resolutions}
    auto_count = 0
    reviewed_count = 0
    unmatched_count = 0
    to_learn: list[tuple[str, str, int | None, str | None, str | None]] = []

    for row_index, result in lines.items():
        res = resolutions.get(row_index)
        if result.bucket == "auto" and result.picked is not None:
            gm = result.picked
            writes.append(OrderWrite(
                sheet=gm.sheet, side=gm.side, row_index=gm.row_index,
                item_no=gm.item_no, qty=result.onestop.qty,
            ))
            auto_count += 1
            continue

        if res is None:
            unmatched_count += 1
            continue

        if res.choice == "accept":
            gm = result.picked
            if gm is None:
                unmatched_count += 1
                continue
            writes.append(OrderWrite(
                sheet=gm.sheet, side=gm.side, row_index=gm.row_index,
                item_no=gm.item_no, qty=result.onestop.qty,
            ))
            reviewed_count += 1
            if res.remember:
                to_learn.append((
                    result.onestop.description_normalized,
                    result.onestop.description,
                    gm.item_no, gm.sheet, gm.description,
                ))
        elif res.choice == "pick":
            gm = index.by_item(res.gm_item_no or -1, res.gm_sheet)
            if gm is None:
                raise HTTPException(400, f"Item#{res.gm_item_no} not in GM catalog")
            writes.append(OrderWrite(
                sheet=gm.sheet, side=gm.side, row_index=gm.row_index,
                item_no=gm.item_no, qty=result.onestop.qty,
            ))
            reviewed_count += 1
            if res.remember:
                to_learn.append((
                    result.onestop.description_normalized,
                    result.onestop.description,
                    gm.item_no, gm.sheet, gm.description,
                ))
        elif res.choice == "onestop_only":
            unmatched_count += 1
            if res.remember:
                to_learn.append((
                    result.onestop.description_normalized,
                    result.onestop.description,
                    None, None, None,
                ))

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_name = f"gm_order_{ts}_{req.run_id}.xlsx"
    out_path = config.RUNS_DIR / out_name
    written = write_quantities(config.GM_TEMPLATE_PATH, out_path, writes)

    if to_learn:
        now = datetime.utcnow().isoformat()
        with db.session() as conn:
            conn.executemany(
                """INSERT INTO mapping(onestop_desc_normalized, onestop_desc,
                                       gm_item_no, gm_sheet, gm_desc,
                                       confidence, confirmed_by, confirmed_at)
                   VALUES(?, ?, ?, ?, ?, 1.0, ?, ?)
                   ON CONFLICT(onestop_desc_normalized) DO UPDATE SET
                     gm_item_no=excluded.gm_item_no,
                     gm_sheet=excluded.gm_sheet,
                     gm_desc=excluded.gm_desc,
                     confidence=1.0,
                     confirmed_by=excluded.confirmed_by,
                     confirmed_at=excluded.confirmed_at
                """,
                [(n, d, i, s, g, _user, now) for (n, d, i, s, g) in to_learn],
            )

    with db.session() as conn:
        conn.execute(
            """INSERT INTO order_run(uploaded_at, uploaded_by, filename,
                                     lines_auto, lines_reviewed, lines_unmatched,
                                     output_path)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (stage["uploaded_at"], _user, stage["filename"],
             auto_count, reviewed_count, unmatched_count, str(out_path)),
        )

    return ApplyResponse(
        run_id=req.run_id,
        download_url=f"/api/orders/download/{out_name}",
        lines_written=written,
    )


@router.post("/upload-pdf", response_model=PdfUploadResponse)
async def upload_pdf(file: UploadFile = File(...), user: str = Depends(verify_session)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted by this endpoint")

    pdf_bytes = await file.read()
    run_id = uuid.uuid4().hex[:12]

    # Persist the PDF so the page-image endpoint can serve it later
    pdf_path = config.RUNS_DIR / f"{run_id}__original.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)

    try:
        result = read_pdf_order(pdf_bytes)
    except ValueError as e:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    except Exception as e:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(502, f"PDF extraction failed: {e}")

    _RUN_STAGING[run_id] = {
        "source": "pdf",
        "filename": file.filename,
        "uploaded_at": datetime.utcnow().isoformat(),
        "uploaded_by": user,
        "pdf_path": str(pdf_path),
        "page_images": result.page_images,   # held in memory for page serving
        "lines": {},                          # populated by confirm-pdf
    }

    return PdfUploadResponse(
        run_id=run_id,
        page_count=result.page_count,
        first_page_with_results=result.first_page_with_results,
        extracted_lines=[
            PdfExtractedLine(
                description=l.description,
                qty=l.qty,
                price=l.price,
                warning=l.warning,
                source_page=l.source_page,
            )
            for l in result.lines
        ],
        warnings=result.warnings,
    )


@router.get("/pdf-page/{run_id}/{page_num}")
def pdf_page_image(run_id: str, page_num: int, user: str = Depends(verify_session)):
    stage = _RUN_STAGING.get(run_id)
    if stage is None or stage.get("source") != "pdf":
        raise HTTPException(404, "Unknown PDF run — upload again")

    page_images = stage.get("page_images", [])
    if page_num < 1 or page_num > len(page_images):
        raise HTTPException(404, f"Page {page_num} out of range 1–{len(page_images)}")

    page_img = page_images[page_num - 1]
    buf = io.BytesIO()
    page_img.image.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.post("/confirm-pdf", response_model=OrderUploadResponse)
def confirm_pdf(req: PdfConfirmRequest, user: str = Depends(verify_session)):
    stage = _RUN_STAGING.get(req.run_id)
    if stage is None or stage.get("source") != "pdf":
        raise HTTPException(404, "Unknown PDF run — upload again")

    rows = to_onestop_rows(req.lines)
    if not rows:
        raise HTTPException(400, "No valid lines provided (need description + qty > 0)")

    index = _gm_index()
    learned = _load_learned()
    results = match_all(rows, index, learned)

    def _line(r) -> OrderLine:
        picked = None
        if r.picked is not None:
            picked = GmCandidate(
                item_no=r.picked.item_no, sheet=r.picked.sheet,
                description=r.picked.description, price=r.picked.price,
                score=r.score,
            )
        cands = [
            GmCandidate(item_no=c.item_no, sheet=c.sheet,
                        description=c.description, price=c.price, score=s)
            for c, s in r.candidates
        ]
        return OrderLine(
            row_index=r.onestop.row_index,
            onestop_desc=r.onestop.description,
            qty=r.onestop.qty,
            bucket=r.bucket,
            picked=picked,
            candidates=cands,
            reason=r.reason,
        )

    auto      = [_line(r) for r in results if r.bucket == "auto"]
    review    = [_line(r) for r in results if r.bucket == "review"]
    unmatched = [_line(r) for r in results if r.bucket == "unmatched"]

    # Populate staging so the existing /apply endpoint works unchanged
    stage["lines"] = {r.onestop.row_index: r for r in results}

    return OrderUploadResponse(run_id=req.run_id, auto=auto, review=review, unmatched=unmatched)


@router.get("/download/{filename}")
def download(filename: str, user: str = Depends(verify_session)):
    # Prevent path traversal: filename must be a bare name, not a path.
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    path = config.RUNS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@router.get("/history")
def history(limit: int = 25, user: str = Depends(verify_session)):
    with db.session() as conn:
        rows = conn.execute(
            """SELECT uploaded_at, uploaded_by, filename,
                      lines_auto, lines_reviewed, lines_unmatched, output_path
               FROM order_run
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        out_path = Path(r["output_path"]) if r["output_path"] else None
        out.append({
            "uploaded_at": r["uploaded_at"],
            "uploaded_by": r["uploaded_by"],
            "filename": r["filename"],
            "lines_auto": r["lines_auto"],
            "lines_reviewed": r["lines_reviewed"],
            "lines_unmatched": r["lines_unmatched"],
            "download_url": (
                f"/api/orders/download/{out_path.name}"
                if out_path and out_path.exists() else None
            ),
        })
    return out
