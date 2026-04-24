from orderbridge.services.excel_reader import GmRow, OnestopRow
from orderbridge.services.matching import GmIndex, match_row
from orderbridge.services.normalize import normalize


def mk_gm(item_no, sheet, desc, side="left", row_index=1, price=1.0, available=True):
    return GmRow(
        item_no=item_no, sheet=sheet, side=side, row_index=row_index,
        description=desc, description_normalized=normalize(desc),
        price=price, order_cell_value=None, available=available,
    )


def mk_os(desc, qty=1, row_index=1):
    return OnestopRow(
        row_index=row_index, qty=qty, description=desc,
        description_normalized=normalize(desc), price=None, is_header=False,
    )


def test_exact_match_is_auto():
    gm = [mk_gm(100, "BRANDED PRODUCTS", "PATANJALI AMLA MURABBA 20X500G")]
    idx = GmIndex(gm)
    r = match_row(mk_os("Patanjali Amla Murabba 20x500G"), idx, {})
    assert r.bucket == "auto"
    assert r.picked.item_no == 100


def test_prefers_specialized_sheet_over_rest_list():
    gm = [
        mk_gm(100, "REST LIST", "WIDGET 6X1KG", row_index=5),
        mk_gm(100, "BULK PRODUCTS", "WIDGET 6X1KG", row_index=20),
    ]
    idx = GmIndex(gm)
    r = match_row(mk_os("widget 6X1kg"), idx, {})
    assert r.picked.sheet == "BULK PRODUCTS"


def test_learned_mapping_wins_even_over_exact():
    gm = [
        mk_gm(100, "BRANDED PRODUCTS", "GENERIC WIDGET"),
        mk_gm(200, "BULK PRODUCTS",    "SPECIFIC THING"),
    ]
    idx = GmIndex(gm)
    learned = {normalize("generic widget"): (200, "BULK PRODUCTS")}
    r = match_row(mk_os("generic widget"), idx, learned)
    assert r.picked.item_no == 200
    assert r.reason == "learned mapping"


def test_learned_as_onestop_only():
    gm = [mk_gm(100, "BRANDED PRODUCTS", "SOME ITEM")]
    idx = GmIndex(gm)
    learned = {normalize("some item"): (None, None)}
    r = match_row(mk_os("some item"), idx, learned)
    assert r.bucket == "unmatched"


def test_pack_size_mismatch_demotes_to_review():
    gm = [mk_gm(100, "BRANDED PRODUCTS", "PATANJALI AMLA MURABBA 12X1KG")]
    idx = GmIndex(gm)
    r = match_row(mk_os("Patanjali Amla Murabba 20X500G"), idx, {})
    assert r.bucket in ("review", "unmatched")


def test_no_candidates_is_unmatched():
    gm = [mk_gm(100, "BRANDED PRODUCTS", "COMPLETELY DIFFERENT THING")]
    idx = GmIndex(gm)
    r = match_row(mk_os("xyzzy foo bar"), idx, {})
    assert r.bucket == "unmatched"
