from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier import rules
from app.classifier.llm import ClaudeFallback
from app.classifier.rules import RuleInput
from app.models import (
    Category,
    Classifier,
    Direction,
    Rule,
    Scope,
    ScopeKind,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Classification:
    """Pipeline output. The caller (importer wizard, live poller) decides
    whether to auto-apply (high confidence) or park as `awaiting` for review.

    When the LLM proposes a *new* scope or category, the corresponding
    `*_proposal_name` field is populated (and `_id` left None). Existing
    matches resolve to `_id` directly.
    """

    classifier: Classifier
    rule_id: int | None
    scope_id: int | None
    scope_proposal_name: str | None
    scope_proposal_kind: ScopeKind | None
    category_id: int | None
    category_proposal_name: str | None
    direction: Direction
    counterparty: str | None
    confidence: float
    reasoning: str


def classify(
    db: Session,
    *,
    filename: str,
    sender_email: str | None,
    ocr_text: str | None,
    llm: ClaudeFallback | None = None,
) -> Classification:
    rule_input = RuleInput(filename=filename, sender_email=sender_email, ocr_text=ocr_text)
    hit = rules.evaluate(db, rule_input)
    if hit:
        rule = db.get(Rule, hit.rule_id)
        return Classification(
            classifier=Classifier.rule,
            rule_id=hit.rule_id,
            scope_id=hit.scope_id,
            scope_proposal_name=None,
            scope_proposal_kind=None,
            category_id=hit.category_id,
            category_proposal_name=None,
            direction=(rule.direction if rule and rule.direction else Direction.expense),
            counterparty=None,
            confidence=hit.confidence,
            reasoning=f"matched rule #{hit.rule_id}",
        )

    if llm is None:
        return _empty_unsorted("no rule matched; LLM disabled")

    scopes = list(
        db.scalars(select(Scope).where(Scope.active.is_(True)).order_by(Scope.name))
    )
    categories = list(db.scalars(select(Category).order_by(Category.name)))

    suggestion = llm.classify(
        scopes=scopes,
        categories=categories,
        ocr_text=ocr_text or "",
        sender_email=sender_email,
        filename=filename,
    )

    scope_id, scope_proposal, scope_kind = _resolve_scope(db, suggestion)
    category_id, category_proposal = _resolve_category(db, suggestion)

    return Classification(
        classifier=Classifier.llm,
        rule_id=None,
        scope_id=scope_id,
        scope_proposal_name=scope_proposal,
        scope_proposal_kind=scope_kind,
        category_id=category_id,
        category_proposal_name=category_proposal,
        direction=suggestion.direction or Direction.expense,
        counterparty=suggestion.counterparty,
        confidence=suggestion.confidence,
        reasoning=suggestion.reasoning,
    )


def _empty_unsorted(reason: str) -> Classification:
    return Classification(
        classifier=Classifier.unsorted,
        rule_id=None,
        scope_id=None,
        scope_proposal_name=None,
        scope_proposal_kind=None,
        category_id=None,
        category_proposal_name=None,
        direction=Direction.expense,
        counterparty=None,
        confidence=0.0,
        reasoning=reason,
    )


def _resolve_scope(db: Session, suggestion) -> tuple[int | None, str | None, ScopeKind | None]:
    name = suggestion.scope_name
    if not name:
        return None, None, None

    if suggestion.new_scope:
        kind: ScopeKind | None = None
        if suggestion.scope_kind_hint:
            try:
                kind = ScopeKind(suggestion.scope_kind_hint)
            except ValueError:
                kind = None
        return None, name, kind

    scope = db.scalar(
        select(Scope).where(Scope.name == name).where(Scope.active.is_(True))
    )
    if scope:
        return scope.id, None, None
    # LLM named a scope that no longer exists; treat as proposal.
    return None, name, None


def _resolve_category(db: Session, suggestion) -> tuple[int | None, str | None]:
    name = suggestion.category_name
    if not name:
        return None, None
    if suggestion.new_category:
        return None, name
    cat = db.scalar(select(Category).where(Category.name == name))
    if cat:
        return cat.id, None
    return None, name
