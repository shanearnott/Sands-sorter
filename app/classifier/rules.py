from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchType, Rule


@dataclass(frozen=True)
class RuleHit:
    rule_id: int
    scope_id: int
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
                scope_id=rule.scope_id,
                category_id=rule.category_id,
                confidence=rule.confidence,
            )
    return None


def evaluate_all(db: Session, inp: RuleInput) -> list[tuple[Rule, bool]]:
    """Evaluate **every** rule (enabled or not) against `inp`. Used by the
    `/rules/test` page so the user can see which rules would match given
    sample text, in priority order. The first hit among enabled rules is
    the one that fires in production.
    """
    stmt = select(Rule).order_by(Rule.priority.asc(), Rule.id.asc())
    return [(rule, _matches(rule, inp)) for rule in db.scalars(stmt)]


def matches(rule: Rule, inp: RuleInput) -> bool:
    """Public re-export of the match check for callers that already have a
    `Rule` instance and just want to know if it would fire."""
    return _matches(rule, inp)
