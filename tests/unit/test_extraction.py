from datetime import date
from types import SimpleNamespace

from app.extraction.invoice import (
    _parse_amount_cents,
    _parse_date,
    fields_from_documentai,
)


def _entity(type_name: str, mention_text: str | None = None, normalized_text: str | None = None):
    return SimpleNamespace(
        type_=type_name,
        mention_text=mention_text,
        normalized_value=SimpleNamespace(text=normalized_text) if normalized_text else None,
    )


def _doc(text: str, entities: list):
    return SimpleNamespace(text=text, entities=entities)


def test_parse_amount_cents_handles_dollar_signs_and_commas():
    assert _parse_amount_cents("$1,234.56") == 123456
    assert _parse_amount_cents("AUD 89.00") == 8900
    assert _parse_amount_cents("89") == 8900
    assert _parse_amount_cents("") is None
    assert _parse_amount_cents(None) is None


def test_parse_date_iso():
    assert _parse_date("2025-09-01") == date(2025, 9, 1)
    assert _parse_date("not a date") is None
    assert _parse_date(None) is None


def test_fields_from_documentai_picks_known_entities():
    doc = _doc(
        "ORIGIN ENERGY\nInvoice 12345\nTotal $89.00\n",
        [
            _entity("supplier_name", mention_text="Origin Energy"),
            _entity("total_amount", mention_text="$89.00"),
            _entity("currency", mention_text="aud"),
            _entity("invoice_date", normalized_text="2025-09-01"),
            _entity("due_date", normalized_text="2025-09-21"),
            _entity("invoice_id", mention_text="12345"),
        ],
    )
    fields = fields_from_documentai(doc)
    assert fields.counterparty == "Origin Energy"
    assert fields.amount_cents == 8900
    assert fields.currency == "AUD"
    assert fields.doc_date == date(2025, 9, 1)
    assert fields.due_date == date(2025, 9, 21)
    assert fields.account_number == "12345"
    assert "ORIGIN ENERGY" in fields.text


def test_fields_from_documentai_tolerates_missing_entities():
    doc = _doc("blob with no recognised entities", [])
    fields = fields_from_documentai(doc)
    assert fields.counterparty is None
    assert fields.amount_cents is None
    assert fields.doc_date is None
    assert fields.text == "blob with no recognised entities"


def test_fields_first_entity_wins_when_duplicate_type():
    doc = _doc(
        "x",
        [
            _entity("supplier_name", mention_text="First Supplier"),
            _entity("supplier_name", mention_text="Second Supplier"),
        ],
    )
    fields = fields_from_documentai(doc)
    assert fields.counterparty == "First Supplier"
