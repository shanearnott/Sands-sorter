from datetime import datetime
from pathlib import PurePosixPath

from app.drive.paths import build_path, folder_chain, safe_segment


def test_safe_segment_strips_unsafe_chars():
    assert safe_segment("Property/A:bad*chars") == "PropertyAbadchars"


def test_safe_segment_collapses_whitespace():
    assert safe_segment("  Beach   House  ") == "Beach House"


def test_safe_segment_falls_back_to_unknown():
    assert safe_segment("///") == "Unknown"


def test_build_path_full():
    path = build_path(
        property_name="Beach House",
        category_name="Electricity",
        year=2026,
        filename="bill.pdf",
    )
    assert path == PurePosixPath("Properties/Beach House/Electricity/2026/bill.pdf")


def test_build_path_unsorted_when_missing_property():
    path = build_path(
        property_name=None,
        category_name="Electricity",
        year=2026,
        filename="x.pdf",
    )
    assert path == PurePosixPath("Properties/Unsorted/2026/x.pdf")


def test_build_path_uses_now_year():
    path = build_path(
        property_name=None,
        category_name=None,
        year=None,
        filename="x.pdf",
        now=datetime(2030, 6, 1),
    )
    assert path.parts[-2] == "2030"


def test_folder_chain_excludes_filename():
    chain = folder_chain(PurePosixPath("Properties/A/Electricity/2026/bill.pdf"))
    assert chain == ["Properties", "A", "Electricity", "2026"]
