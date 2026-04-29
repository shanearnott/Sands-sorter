from datetime import date, datetime
from pathlib import PurePosixPath

from app.drive.paths import build_path, folder_chain, safe_segment
from app.models import ScopeCountry, ScopeKind


def test_safe_segment_strips_unsafe_chars():
    assert safe_segment("Property/A:bad*chars") == "PropertyAbadchars"


def test_safe_segment_collapses_whitespace():
    assert safe_segment("  Beach   House  ") == "Beach House"


def test_safe_segment_falls_back_to_unknown():
    assert safe_segment("///") == "Unknown"


def test_build_path_property_au_after_july_rolls_to_next_fy():
    path, fy = build_path(
        scope_kind=ScopeKind.property,
        scope_name="Beach House",
        scope_country=ScopeCountry.AU,
        category_name="Electricity",
        doc_date=date(2025, 9, 1),
        filename="bill.pdf",
    )
    assert path == PurePosixPath("Properties/Beach House/Electricity/FY2026/bill.pdf")
    assert fy == 2026


def test_build_path_property_au_before_july_stays_in_fy():
    path, fy = build_path(
        scope_kind=ScopeKind.property,
        scope_name="Beach House",
        scope_country=ScopeCountry.AU,
        category_name="Electricity",
        doc_date=date(2025, 6, 30),
        filename="bill.pdf",
    )
    assert path == PurePosixPath("Properties/Beach House/Electricity/FY2025/bill.pdf")
    assert fy == 2025


def test_build_path_property_us_uses_calendar():
    path, fy = build_path(
        scope_kind=ScopeKind.property,
        scope_name="US Cabin",
        scope_country=ScopeCountry.US,
        category_name="Water",
        doc_date=date(2025, 9, 1),
        filename="bill.pdf",
    )
    assert path == PurePosixPath("Properties/US Cabin/Water/FY2025/bill.pdf")
    assert fy == 2025


def test_build_path_car():
    path, fy = build_path(
        scope_kind=ScopeKind.car,
        scope_name="Tesla",
        scope_country=ScopeCountry.AU,
        category_name="Insurance",
        doc_date=date(2026, 3, 1),
        filename="renewal.pdf",
    )
    assert path == PurePosixPath("Cars/Tesla/Insurance/FY2026/renewal.pdf")
    assert fy == 2026


def test_build_path_life_skips_name_segment():
    path, fy = build_path(
        scope_kind=ScopeKind.life,
        scope_name="Life",
        scope_country=ScopeCountry.AU,
        category_name="Credit Card",
        doc_date=date(2026, 1, 15),
        filename="amex.pdf",
    )
    assert path == PurePosixPath("Life/Credit Card/FY2026/amex.pdf")
    assert fy == 2026


def test_build_path_unsorted_when_kind_missing():
    path, fy = build_path(
        scope_kind=None,
        scope_name=None,
        scope_country=None,
        category_name="Anything",
        doc_date=date(2025, 9, 1),
        filename="x.pdf",
    )
    assert path == PurePosixPath("Unsorted/FY2026/x.pdf")
    assert fy == 2026


def test_build_path_unsorted_when_category_missing():
    path, fy = build_path(
        scope_kind=ScopeKind.property,
        scope_name="Beach House",
        scope_country=ScopeCountry.AU,
        category_name=None,
        doc_date=date(2025, 9, 1),
        filename="x.pdf",
    )
    assert path == PurePosixPath("Unsorted/FY2026/x.pdf")
    assert fy == 2026


def test_build_path_uses_now_when_no_doc_date():
    path, fy = build_path(
        scope_kind=None,
        scope_name=None,
        scope_country=None,
        category_name=None,
        doc_date=None,
        filename="x.pdf",
        now=datetime(2030, 6, 1),
    )
    # AU default + 1 June 2030 -> still FY2030 (start month 7)
    assert fy == 2030
    assert path == PurePosixPath("Unsorted/FY2030/x.pdf")


def test_folder_chain_excludes_filename():
    chain = folder_chain(PurePosixPath("Properties/A/Electricity/FY2026/bill.pdf"))
    assert chain == ["Properties", "A", "Electricity", "FY2026"]
