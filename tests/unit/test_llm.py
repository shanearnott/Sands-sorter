import json

from app.classifier.llm import parse_response
from app.models import Direction


def test_parse_response_existing_scope_and_category():
    text = json.dumps(
        {
            "scope_name": "Beach House",
            "scope_kind_hint": None,
            "category_name": "Electricity",
            "direction": "expense",
            "counterparty": "Origin Energy",
            "confidence": 0.9,
            "reasoning": "Origin Energy invoice for Beach House.",
        }
    )
    s = parse_response(text)
    assert s.scope_name == "Beach House"
    assert s.category_name == "Electricity"
    assert s.direction == Direction.expense
    assert s.confidence == 0.9
    assert not s.new_scope
    assert not s.new_category


def test_parse_response_new_scope_unwraps_prefix():
    text = json.dumps(
        {
            "scope_name": "NEW: Mountain Cabin",
            "scope_kind_hint": "property",
            "category_name": "NEW: HOA fees",
            "direction": "expense",
            "counterparty": "Mountain HOA",
            "confidence": 0.55,
            "reasoning": "Looks like a new property and category.",
        }
    )
    s = parse_response(text)
    assert s.scope_name == "Mountain Cabin"
    assert s.scope_kind_hint == "property"
    assert s.new_scope
    assert s.category_name == "HOA fees"
    assert s.new_category


def test_parse_response_income_direction():
    text = json.dumps(
        {
            "scope_name": "Beach House",
            "scope_kind_hint": None,
            "category_name": "Rent received",
            "direction": "income",
            "counterparty": "Airbnb",
            "confidence": 0.85,
            "reasoning": "Airbnb payout for Beach House.",
        }
    )
    s = parse_response(text)
    assert s.direction == Direction.income
    assert s.counterparty == "Airbnb"


def test_parse_response_handles_invalid_json():
    s = parse_response("not json")
    assert s.confidence == 0.0
    assert s.direction is None
    assert "Failed to parse" in s.reasoning


def test_parse_response_handles_unknown_direction():
    text = json.dumps(
        {
            "scope_name": None,
            "scope_kind_hint": None,
            "category_name": None,
            "direction": "weird",
            "counterparty": None,
            "confidence": 0.1,
            "reasoning": "no idea",
        }
    )
    s = parse_response(text)
    assert s.direction is None
