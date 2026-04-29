from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchType, Rule


@dataclass(frozen=True)
class RuleHit:
    rule_id: int
    property_id: int
    category_id: int
    confidence: float


@dataclass(frozen=True)
class RuleInput:
    """Inputs the rules engine evaluates against, mirroring `MatchType` variants."""

    filename: str
    sender_email: str | None
    ocr_text: str | None


def _matches(rule: Rule, inp: RuleInput) -> bool:
    pattern = rule.pattern or ""
    if rule.match_type == MatchType.filename:
        return pattern.lower() in (inp.filename or "").lower()
    if rule.match_type == MatchType.sender_email:
        return pattern.lower() in (inp.sender_email or "").lower()
    if rule.match_type == MatchType.contains:
        return pattern.lower() in (inp.ocr_text or "").lower()
    if rule.match_type == MatchType.regex:
        try:
            return re.search(pattern, inp.ocr_text or "", flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def evaluate(db: Session, inp: RuleInput) -> RuleHit | None:
    """Return the first matching rule, ordered by priority asc then id asc."""
    stmt = (
        select(Rule)
        .where(Rule.enabled.is_(True))
        .order_by(Rule.priority.asc(), Rule.id.asc())
    )
    for rule in db.scalars(stmt):
        if _matches(rule, inp):
            return RuleHit(
                rule_id=rule.id,
                property_id=rule.property_id,
                category_id=rule.category_id,
                confidence=rule.confidence,
            )
    return None
