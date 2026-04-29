from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classifier.llm import LLMSuggestion
from app.classifier.pipeline import classify
from app.models import (
    Base,
    Category,
    Classifier,
    Direction,
    MatchType,
    Rule,
    Scope,
    ScopeKind,
)


class FakeLLM:
    def __init__(self, suggestion: LLMSuggestion):
        self._s = suggestion
        self.calls = 0

    def classify(self, **kwargs):
        self.calls += 1
        return self._s


def _setup() -> tuple[Session, Scope, Category]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    scope = Scope(kind=ScopeKind.property, name="Beach House")
    cat = Category(name="Electricity")
    db.add_all([scope, cat])
    db.commit()
    return db, scope, cat


def test_pipeline_returns_rule_match_without_calling_llm():
    db, scope, cat = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="origin",
            scope_id=scope.id,
            category_id=cat.id,
            direction=Direction.expense,
            priority=10,
            confidence=0.95,
        )
    )
    db.commit()

    llm = FakeLLM(LLMSuggestion(None, None, None, None, None, 0.0, "", False, False))
    result = classify(
        db,
        filename="bill.pdf",
        sender_email=None,
        ocr_text="ORIGIN ENERGY invoice",
        llm=llm,
    )
    assert result.classifier == Classifier.rule
    assert result.scope_id == scope.id
    assert result.category_id == cat.id
    assert result.direction == Direction.expense
    assert llm.calls == 0


def test_pipeline_falls_back_to_llm_for_existing_match():
    db, scope, cat = _setup()
    llm = FakeLLM(
        LLMSuggestion(
            scope_name="Beach House",
            scope_kind_hint=None,
            category_name="Electricity",
            direction=Direction.expense,
            counterparty="Origin Energy",
            confidence=0.85,
            reasoning="OCR text mentions Origin Energy.",
            new_scope=False,
            new_category=False,
        )
    )
    result = classify(
        db, filename="x.pdf", sender_email=None, ocr_text="Origin Energy", llm=llm
    )
    assert result.classifier == Classifier.llm
    assert result.scope_id == scope.id
    assert result.category_id == cat.id
    assert result.confidence == 0.85
    assert result.counterparty == "Origin Energy"


def test_pipeline_proposes_new_scope_and_category_from_llm():
    db, _scope, _cat = _setup()
    llm = FakeLLM(
        LLMSuggestion(
            scope_name="Mountain Cabin",
            scope_kind_hint="property",
            category_name="HOA fees",
            direction=Direction.expense,
            counterparty="Mountain HOA",
            confidence=0.55,
            reasoning="Looks like a new property.",
            new_scope=True,
            new_category=True,
        )
    )
    result = classify(db, filename="x.pdf", sender_email=None, ocr_text="HOA dues", llm=llm)
    assert result.classifier == Classifier.llm
    assert result.scope_id is None
    assert result.scope_proposal_name == "Mountain Cabin"
    assert result.scope_proposal_kind == ScopeKind.property
    assert result.category_id is None
    assert result.category_proposal_name == "HOA fees"


def test_pipeline_routes_unsorted_when_llm_disabled():
    db, _scope, _cat = _setup()
    result = classify(
        db, filename="x.pdf", sender_email=None, ocr_text="random", llm=None
    )
    assert result.classifier == Classifier.unsorted
    assert result.confidence == 0.0


def test_pipeline_treats_unknown_existing_scope_as_proposal():
    db, _scope, _cat = _setup()
    llm = FakeLLM(
        LLMSuggestion(
            scope_name="Lake House",  # not in DB but flagged as existing
            scope_kind_hint=None,
            category_name="Electricity",
            direction=Direction.expense,
            counterparty=None,
            confidence=0.6,
            reasoning="LLM thought this matched an existing scope.",
            new_scope=False,
            new_category=False,
        )
    )
    result = classify(db, filename="x.pdf", sender_email=None, ocr_text="bill", llm=llm)
    assert result.scope_id is None
    assert result.scope_proposal_name == "Lake House"
