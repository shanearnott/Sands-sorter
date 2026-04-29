from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classifier.rules import RuleInput, evaluate
from app.models import Base, Category, MatchType, Property, Rule


def _setup() -> tuple[Session, Property, Category]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    prop = Property(name="Beach House")
    cat = Category(name="Electricity")
    db.add_all([prop, cat])
    db.commit()
    return db, prop, cat


def test_filename_match_wins():
    db, prop, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.filename,
            pattern="origin",
            property_id=prop.id,
            category_id=cat.id,
            priority=10,
        )
    )
    db.commit()
    hit = evaluate(
        db,
        RuleInput(filename="origin_energy_apr.pdf", sender_email=None, ocr_text=None),
    )
    assert hit is not None
    assert hit.property_id == prop.id


def test_priority_lower_wins_first():
    db, prop, cat = _setup()
    db.add_all(
        [
            Rule(
                match_type=MatchType.contains,
                pattern="energy",
                property_id=prop.id,
                category_id=cat.id,
                priority=20,
                confidence=0.5,
            ),
            Rule(
                match_type=MatchType.contains,
                pattern="energy",
                property_id=prop.id,
                category_id=cat.id,
                priority=10,
                confidence=0.95,
            ),
        ]
    )
    db.commit()
    hit = evaluate(db, RuleInput(filename="x", sender_email=None, ocr_text="Origin Energy invoice"))
    assert hit is not None
    assert hit.confidence == 0.95


def test_disabled_rules_are_skipped():
    db, prop, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="energy",
            property_id=prop.id,
            category_id=cat.id,
            priority=10,
            enabled=False,
        )
    )
    db.commit()
    assert (
        evaluate(db, RuleInput(filename="x", sender_email=None, ocr_text="Origin Energy"))
        is None
    )


def test_regex_match_caseless():
    db, prop, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.regex,
            pattern=r"acct\s*#\s*\d+",
            property_id=prop.id,
            category_id=cat.id,
            priority=5,
        )
    )
    db.commit()
    hit = evaluate(
        db, RuleInput(filename="x", sender_email=None, ocr_text="Acct # 12345 due now")
    )
    assert hit is not None


def test_no_match_returns_none():
    db, prop, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="nonsense",
            property_id=prop.id,
            category_id=cat.id,
        )
    )
    db.commit()
    assert (
        evaluate(db, RuleInput(filename="x", sender_email=None, ocr_text="totally different"))
        is None
    )
