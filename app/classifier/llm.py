from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from app.config import get_settings
from app.models import Category, Direction, Scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMSuggestion:
    """Structured proposal returned by the Claude fallback classifier."""

    scope_name: str | None
    scope_kind_hint: str | None  # property | car | life
    category_name: str | None
    direction: Direction | None
    counterparty: str | None
    confidence: float
    reasoning: str
    new_scope: bool
    new_category: bool


# JSON Schema for structured-output enforcement. Required-everywhere + nullable
# unions per the Claude structured-outputs guidance.
_SCHEMA = {
    "type": "object",
    "properties": {
        "scope_name": {
            "type": ["string", "null"],
            "description": (
                "Name of an existing scope from the catalog, or 'NEW: <name>' to propose a new one. "
                "Null if you cannot tell."
            ),
        },
        "scope_kind_hint": {
            "type": ["string", "null"],
            "description": (
                "Only when proposing a NEW scope: one of 'property', 'car', 'life'. Otherwise null."
            ),
        },
        "category_name": {
            "type": ["string", "null"],
            "description": (
                "Name of an existing category, or 'NEW: <name>' to propose a new one. "
                "Null if you cannot tell."
            ),
        },
        "direction": {
            "type": "string",
            "enum": ["expense", "income"],
            "description": "expense = bill we pay; income = rent/payouts received.",
        },
        "counterparty": {
            "type": ["string", "null"],
            "description": "Vendor or payer name from the document, e.g. 'Origin Energy', 'Airbnb'.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0 to 1.0 confidence in the overall classification.",
        },
        "reasoning": {
            "type": "string",
            "description": "One sentence explaining why.",
        },
    },
    "required": [
        "scope_name",
        "scope_kind_hint",
        "category_name",
        "direction",
        "counterparty",
        "confidence",
        "reasoning",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You are a household-bills classifier for a personal filing app. Given OCR text of a bill or invoice plus catalogs of existing scopes and categories, identify:

1. **Scope** — which property, car, or the singleton 'Life' bucket the document belongs to. Match an existing scope by name when possible. If the document is clearly about a scope not in the catalog, propose it as `NEW: <name>` and set `scope_kind_hint` to `property`, `car`, or `life`.
2. **Category** — which spend category. Match existing where possible; propose `NEW: <name>` only when the document is clearly a new bucket.
3. **Direction** — `expense` for bills we pay; `income` for rent or payouts received (Airbnb, real-estate agent statements, direct-booking deposits).
4. **Counterparty** — the vendor or payer name as it appears in the document.
5. **Confidence** (0.0 to 1.0).

Be conservative. If signals are weak, set confidence below 0.6 and pick the closest existing scope/category with a brief reasoning note rather than inventing one.
"""


def _format_catalog(scopes: list[Scope], categories: list[Category]) -> str:
    lines = ["## Existing scopes\n"]
    if not scopes:
        lines.append("(none yet)")
    for s in scopes:
        lines.append(f"- {s.name} ({s.kind.value}, {s.country.value})")
    lines.append("\n## Existing categories\n")
    if not categories:
        lines.append("(none yet)")
    for c in categories:
        default = (
            f" [default: {c.default_direction.value}]" if c.default_direction else ""
        )
        lines.append(f"- {c.name}{default}")
    return "\n".join(lines)


class ClaudeFallback:
    """Calls Claude with structured outputs. The system prompt + catalog are
    cached so a long import job amortises one cache write across many docs."""

    def __init__(self, client: anthropic.Anthropic | None = None):
        settings = get_settings()
        if client is None:
            if not settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._client = client
        self._model = settings.anthropic_model

    def classify(
        self,
        *,
        scopes: list[Scope],
        categories: list[Category],
        ocr_text: str,
        sender_email: str | None = None,
        filename: str | None = None,
    ) -> LLMSuggestion:
        # Cap OCR to keep prompt tight; first ~4K chars carries the signal.
        excerpt = (ocr_text or "")[:4000]
        catalog = _format_catalog(scopes, categories)

        user_text = (
            f"## OCR text\n```\n{excerpt}\n```\n\n"
            f"Sender email: {sender_email or '-'}\n"
            f"Filename: {filename or '-'}\n"
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=600,
            cache_control={"type": "ephemeral"},  # cache the stable prefix
            system=[
                {"type": "text", "text": SYSTEM_PROMPT},
                {"type": "text", "text": catalog},
            ],
            messages=[{"role": "user", "content": user_text}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        return parse_response(text)


def parse_response(json_text: str) -> LLMSuggestion:
    """Parse the structured JSON returned by Claude into an LLMSuggestion.

    Exposed separately so unit tests can exercise the parsing without an
    Anthropic client.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return LLMSuggestion(
            scope_name=None,
            scope_kind_hint=None,
            category_name=None,
            direction=None,
            counterparty=None,
            confidence=0.0,
            reasoning=f"Failed to parse LLM JSON: {json_text[:200]}",
            new_scope=False,
            new_category=False,
        )

    scope_name = data.get("scope_name")
    category_name = data.get("category_name")
    new_scope = bool(scope_name and scope_name.startswith("NEW:"))
    new_category = bool(category_name and category_name.startswith("NEW:"))
    if new_scope:
        scope_name = scope_name[len("NEW:"):].strip()
    if new_category:
        category_name = category_name[len("NEW:"):].strip()

    direction_value = data.get("direction")
    try:
        direction = Direction(direction_value) if direction_value else None
    except ValueError:
        direction = None

    return LLMSuggestion(
        scope_name=scope_name,
        scope_kind_hint=data.get("scope_kind_hint"),
        category_name=category_name,
        direction=direction,
        counterparty=data.get("counterparty"),
        confidence=float(data.get("confidence") or 0.0),
        reasoning=data.get("reasoning") or "",
        new_scope=new_scope,
        new_category=new_category,
    )
