from datetime import date

import pytest

from app.models import ScopeCountry
from app.utils.fy import financial_year, fy_label


@pytest.mark.parametrize(
    ("d", "country", "expected"),
    [
        (date(2025, 9, 1), ScopeCountry.AU, 2026),    # after 1 July -> next FY
        (date(2025, 7, 1), ScopeCountry.AU, 2026),    # exactly 1 July -> next FY
        (date(2025, 6, 30), ScopeCountry.AU, 2025),   # 30 June -> current FY
        (date(2025, 1, 1), ScopeCountry.AU, 2025),    # mid-FY
        (date(2025, 9, 1), ScopeCountry.US, 2025),    # US calendar
        (date(2025, 1, 1), ScopeCountry.US, 2025),
        (date(2025, 12, 31), ScopeCountry.US, 2025),
    ],
)
def test_financial_year(d, country, expected):
    assert financial_year(d, country) == expected


def test_fy_label():
    assert fy_label(2026) == "FY2026"
