from orderbridge.services.normalize import (
    extract_pack_size,
    normalize,
    pack_sizes_compatible,
)


def test_normalize_uppercases_and_strips_punct():
    assert normalize("Patanjali AMLA-Murabba, 20x500g!!") == "PATANJALI AMLA MURABBA 20X500G"


def test_normalize_collapses_whitespace():
    assert normalize("  foo    bar  ") == "FOO BAR"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize(None) == ""  # type: ignore[arg-type]


def test_extract_pack_size_basic():
    assert extract_pack_size("Gathering Saffron 12X1G") == "12X1G"
    assert extract_pack_size("Patanjali Amla Murabba 20x500G") == "20X500G"


def test_extract_pack_size_missing():
    assert extract_pack_size("plain description") is None


def test_pack_sizes_compatible_same():
    assert pack_sizes_compatible("foo 12X1G", "bar 12X1G")


def test_pack_sizes_compatible_mismatch():
    assert not pack_sizes_compatible("foo 12X1G", "bar 6X1G")


def test_pack_sizes_onestop_has_none_is_ok():
    assert pack_sizes_compatible("no size here", "bar 12X1G")


def test_pack_sizes_gm_missing_is_not_ok():
    assert not pack_sizes_compatible("foo 12X1G", "no size on gm side")
