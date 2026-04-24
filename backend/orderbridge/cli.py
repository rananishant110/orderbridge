"""Phase-1 CLI — run matching end-to-end without the web layer.

Usage:
    python -m orderbridge.cli match \
        --onestop path/to/filled.xlsx \
        --gm      path/to/gm_template.xlsx \
        --out     path/to/output.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .services.excel_reader import read_gm_catalog, read_onestop
from .services.excel_writer import OrderWrite, write_quantities
from .services.matching import GmIndex, match_all


def _load_learned() -> dict[str, tuple[int | None, str | None]]:
    try:
        with db.session() as conn:
            cur = conn.execute(
                "SELECT onestop_desc_normalized, gm_item_no, gm_sheet FROM mapping"
            )
            return {r["onestop_desc_normalized"]: (r["gm_item_no"], r["gm_sheet"]) for r in cur}
    except Exception:
        return {}


def cmd_match(args: argparse.Namespace) -> int:
    onestop_rows = read_onestop(args.onestop, only_with_qty=True)
    gm_rows = read_gm_catalog(args.gm)
    index = GmIndex(gm_rows)
    learned = _load_learned()
    results = match_all(onestop_rows, index, learned)

    writes: list[OrderWrite] = []
    auto = review = unmatched = 0
    for r in results:
        if r.bucket == "auto" and r.picked is not None:
            writes.append(OrderWrite(
                sheet=r.picked.sheet, side=r.picked.side,
                row_index=r.picked.row_index, item_no=r.picked.item_no,
                qty=r.onestop.qty,
            ))
            auto += 1
        elif r.bucket == "review":
            review += 1
            print(f"[REVIEW] {r.onestop.description!r} → "
                  f"{r.picked.description!r} (#{r.picked.item_no}, {r.score:.2f})")
        else:
            unmatched += 1
            print(f"[NO-MATCH] {r.onestop.description!r}")

    written = write_quantities(args.gm, args.out, writes)
    print(f"\nauto={auto} review={review} unmatched={unmatched}")
    print(f"wrote {written} cells → {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orderbridge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("match", help="Run OneStop → GM matching once")
    m.add_argument("--onestop", required=True, type=Path)
    m.add_argument("--gm", required=True, type=Path)
    m.add_argument("--out", required=True, type=Path)
    m.set_defaults(func=cmd_match)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
