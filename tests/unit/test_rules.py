from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classifier.rules import RuleInput, evaluate, evaluate_all, matches
from app.models import Base, Category, MatchType, Rule, Scope, ScopeKind


def _setup() -> tuple[Session, Scope, Category]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    scope = Scope(kind=ScopeKind.property, name="Beach House")
    cat = Category(name="Electricity")
    db.add_all([scope, cat])
    db.commit()
    return db, scope, cat


def test_filename_match_wins():
    db, scope, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.filename,
            pattern="origin",
            scope_id=scope.id,
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
    assert hit.scope_id == scope.id


def test_priority_lower_wins_first():
    db, scope, cat = _setup()
    db.add_all(
        [
            Rule(
                match_type=MatchType.contains,
                pattern="energy",
                scope_id=scope.id,
                category_id=cat.id,
                priority=20,
                confidence=0.5,
            ),
            Rule(
                match_type=MatchType.contains,
                pattern="energy",
                scope_id=scope.id,
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
    db, scope, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="energy",
            scope_id=scope.id,
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
    db, scope, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.regex,
            pattern=r"acct\s*#\s*\d+",
            scope_id=scope.id,
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
    db, scope, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="nonsense",
            scope_id=scope.id,
            category_id=cat.id,
        )
    )
    db.commit()
    assert (
        evaluate(db, RuleInput(filename="x", sender_email=None, ocr_text="totally different"))
        is None
    )


def test_evaluate_all_returns_every_rule_with_match_status():
    db, scope, cat = _setup()
    db.add_all(
        [
            Rule(
                match_type=MatchType.contains,
                pattern="energy",
                scope_id=scope.id,
                category_id=cat.id,
                priority=10,
            ),
            Rule(
                match_type=MatchType.contains,
                pattern="water",
                scope_id=scope.id,
                category_id=cat.id,
                priority=20,
            ),
            Rule(
                match_type=MatchType.contains,
                pattern="origin",
                scope_id=scope.id,
                category_id=cat.id,
                priority=30,
                enabled=False,
            ),
        ]
    )
    db.commit()
    results = evaluate_all(
        db, RuleInput(filename="x", sender_email=None, ocr_text="Origin Energy invoice")
    )
    # Three rules, ordered by priority asc; all enabled flag respected in payload
    patterns = [(r.pattern, hit, r.enabled) for r, hit in results]
    assert patterns == [
        ("energy", True, True),
        ("water", False, True),
        ("origin", True, False),
    ]


def test_matches_public_helper():
    db, scope, cat = _setup()
    rule = Rule(
        match_type=MatchType.sender_email,
        pattern="airbnb.com",
        scope_id=scope.id,
        category_id=cat.id,
    )
    db.add(rule)
    db.commit()
    assert matches(rule, RuleInput(filename="x", sender_email="noreply@airbnb.com", ocr_text=None))
    assert not matches(rule, RuleInput(filename="x", sender_email="other@example.com", ocr_text=None))
