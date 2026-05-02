"""Build and emit the periodic summary digest.

Renders an HTML email per-scope with three sections (Income / Expenses /
Net) and a "needs review" pin at the top for Unsorted documents from the
period. In live mode it sends the email via Gmail; in demo mode it writes
the rendered HTML to `DEMO_DIGEST_DIR/digest-<timestamp>.html` so the user
can open it locally.
"""
from __future__ import annotations

import base64
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Category,
    Direction,
    DocStatus,
    ProcessedDocument,
    Scope,
    SummaryRun,
)

logger = logging.getLogger(__name__)


@dataclass
class ScopeSection:
    scope: Scope
    income_docs: list[ProcessedDocument]
    expense_docs: list[ProcessedDocument]
    income_total_cents: int
    expense_total_cents: int

    @property
    def net_cents(self) -> int:
        return self.income_total_cents - self.expense_total_cents


def build_and_emit(db: Session, *, cadence: str | None = None) -> dict:
    settings = get_settings()
    cadence = cadence or settings.summary_cadence
    period_end = datetime.now(tz=timezone.utc)
    days = 1 if cadence == "daily" else 7
    period_start = period_end - timedelta(days=days)

    docs = list(
        db.scalars(
            select(ProcessedDocument)
            .where(ProcessedDocument.created_at >= period_start)
            .where(ProcessedDocument.created_at <= period_end)
            .order_by(ProcessedDocument.created_at.asc())
        )
    )

    unsorted = [d for d in docs if d.status == DocStatus.unsorted]
    by_scope: dict[int | None, list[ProcessedDocument]] = defaultdict(list)
    for d in docs:
        by_scope[d.scope_id].append(d)

    cat_lookup = {c.id: c for c in db.scalars(select(Category))}
    scope_lookup = {s.id: s for s in db.scalars(select(Scope))}

    sections: list[ScopeSection] = []
    for scope_id, group in by_scope.items():
        if scope_id is None:
            continue
        scope = scope_lookup.get(scope_id)
        if scope is None:
            continue
        income = [d for d in group if d.direction == Direction.income]
        expense = [d for d in group if d.direction == Direction.expense]
        sections.append(
            ScopeSection(
                scope=scope,
                income_docs=income,
                expense_docs=expense,
                income_total_cents=_sum_amount_cents(income),
                expense_total_cents=_sum_amount_cents(expense),
            )
        )
    sections.sort(key=lambda s: s.scope.name)

    html = _render_html(
        cadence=cadence,
        period_start=period_start,
        period_end=period_end,
        sections=sections,
        unsorted=unsorted,
        cat_lookup=cat_lookup,
        scope_lookup=scope_lookup,
    )

    record = SummaryRun(
        cadence=cadence,
        period_start=period_start,
        period_end=period_end,
        document_count=len(docs),
        unsorted_count=len(unsorted),
        income_total_cents=sum(s.income_total_cents for s in sections),
        expense_total_cents=sum(s.expense_total_cents for s in sections),
    )
    db.add(record)
    db.flush()

    if settings.summary_recipients_list and _gmail_send_available(settings):
        message_id = _send_via_gmail(
            settings, html=html, recipients=settings.summary_recipients_list, cadence=cadence
        )
        record.message_id = message_id
        outcome = {"mode": "live", "message_id": message_id}
    else:
        path = _write_to_disk(settings, html=html, cadence=cadence)
        record.message_id = f"file:{path}" if path else None
        outcome = {"mode": "demo", "path": str(path) if path else None}

    db.commit()
    return {
        "summary_run_id": record.id,
        "cadence": cadence,
        "document_count": record.document_count,
        "unsorted_count": record.unsorted_count,
        "income_total_cents": record.income_total_cents,
        "expense_total_cents": record.expense_total_cents,
        **outcome,
    }


# --- rendering --------------------------------------------------------------

def _render_html(
    *,
    cadence: str,
    period_start: datetime,
    period_end: datetime,
    sections: list[ScopeSection],
    unsorted: list[ProcessedDocument],
    cat_lookup: dict[int, Category],
    scope_lookup: dict[int, Scope],
) -> str:
    head = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; color: #111; }}
h1, h2 {{ margin: 16px 0 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 16px; }}
th, td {{ padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; font-size: 13px; }}
.amount {{ text-align: right; font-variant-numeric: tabular-nums; }}
.income {{ color: #15803d; }}
.expense {{ color: #555; }}
.net.positive {{ color: #15803d; font-weight: 600; }}
.net.negative {{ color: #b91c1c; font-weight: 600; }}
.review {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }}
small {{ color: #777; }}
</style></head><body>"""

    title = f"Sands-sorter {cadence} digest — {period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}"
    parts = [head, f"<h1>{title}</h1>"]

    if unsorted:
        parts.append("<div class='review'><strong>Please review.</strong> ")
        parts.append(f"{len(unsorted)} document(s) landed in Unsorted this period:")
        parts.append("<ul>")
        for d in unsorted:
            parts.append(
                f"<li>{_esc(d.original_filename)} — "
                f"<small>{_esc(d.error or 'low confidence')}</small></li>"
            )
        parts.append("</ul></div>")

    for section in sections:
        parts.append(f"<h2>{_esc(section.scope.name)} "
                     f"<small>({section.scope.kind.value}, {section.scope.country.value})</small></h2>")
        parts.append("<table>")
        parts.append("<tr><th>Date</th><th>Counterparty</th><th>Category</th>"
                     "<th>Direction</th><th class='amount'>Amount</th></tr>")
        for d in section.income_docs + section.expense_docs:
            cat = cat_lookup.get(d.category_id) if d.category_id else None
            counterparty = (
                d.extraction.counterparty_text if d.extraction else None
            ) or "—"
            amount = ""
            if d.extraction and d.extraction.amount_cents:
                amount = f"{d.extraction.currency or ''} {d.extraction.amount_cents/100:.2f}"
            row_class = "income" if d.direction == Direction.income else "expense"
            parts.append(
                f"<tr class='{row_class}'>"
                f"<td>{d.doc_date.isoformat() if d.doc_date else '—'}</td>"
                f"<td>{_esc(counterparty)}</td>"
                f"<td>{_esc(cat.name) if cat else '—'}</td>"
                f"<td>{d.direction.value}</td>"
                f"<td class='amount'>{amount}</td>"
                "</tr>"
            )
        parts.append("</table>")
        parts.append(
            "<table><tr>"
            f"<td>Income</td><td class='amount income'>{_fmt(section.income_total_cents)}</td>"
            f"<td>Expense</td><td class='amount expense'>{_fmt(section.expense_total_cents)}</td>"
            f"<td>Net</td><td class='amount net {('positive' if section.net_cents >= 0 else 'negative')}'>"
            f"{_fmt(section.net_cents)}</td>"
            "</tr></table>"
        )

    if not sections and not unsorted:
        parts.append("<p>No new documents in this period.</p>")

    parts.append("</body></html>")
    return "".join(parts)


def _esc(text: str | None) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt(cents: int) -> str:
    return f"${cents/100:,.2f}"


def _sum_amount_cents(docs: list[ProcessedDocument]) -> int:
    total = 0
    for d in docs:
        if d.extraction and d.extraction.amount_cents:
            total += d.extraction.amount_cents
    return total


# --- emission ---------------------------------------------------------------

def _write_to_disk(settings, *, html: str, cadence: str) -> Path | None:
    if not settings.demo_digest_dir:
        return None
    out_dir = Path(settings.demo_digest_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"digest-{cadence}-{datetime.now():%Y-%m-%dT%H%M%S}.html"
    path = out_dir / fname
    path.write_text(html, encoding="utf-8")
    logger.info("Digest written to %s", path)
    return path


def _gmail_send_available(settings) -> bool:
    return bool(settings.drive_oauth_token_json)  # reuse the same Google token


def _send_via_gmail(
    settings, *, html: str, recipients: list[str], cadence: str
) -> str | None:
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        logger.warning("googleapiclient not installed; cannot send digest")
        return None
    try:
        creds = Credentials.from_authorized_user_file(settings.drive_oauth_token_json)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        message = (
            f"From: me\r\nTo: {','.join(recipients)}\r\n"
            f"Subject: Sands-sorter {cadence} digest\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            f"{html}"
        )
        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode("ascii")
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return sent.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail send failed: %s", exc)
        return None
