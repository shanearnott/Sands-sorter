from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Source, SourceKind
from app.pollers.local_dropbox import LocalDropboxPoller
from app.pollers.local_mailbox import LocalMailboxPoller


def _make_db_with_source(kind: SourceKind, *, body_allowlist=None) -> tuple[Session, Source]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    source = Source(kind=kind, label="demo", enabled=True, body_allowlist=body_allowlist)
    db.add(source)
    db.commit()
    return db, source


def test_local_dropbox_yields_supported_files_and_moves_them(tmp_path: Path):
    db, source = _make_db_with_source(SourceKind.dropbox)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "origin.pdf").write_bytes(b"PDF-bytes")
    (inbox / "screenshot.png").write_bytes(b"PNG-bytes")
    (inbox / "notes.txt").write_text("ignore me")

    poller = LocalDropboxPoller(db, source, tmp_path)
    items = list(poller.fetch())
    assert sorted(i.filename for i in items) == ["origin.pdf", "screenshot.png"]

    # mark_processed moves originals out of the inbox
    for item in items:
        poller.mark_processed(item)
    assert not (inbox / "origin.pdf").exists()
    assert not (inbox / "screenshot.png").exists()
    processed_files = list((tmp_path / "processed").rglob("*"))
    assert any(p.name == "origin.pdf" for p in processed_files)


def test_local_mailbox_yields_attachments(tmp_path: Path):
    db, source = _make_db_with_source(SourceKind.gmail)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    msg = EmailMessage()
    msg["From"] = "billing@example.com"
    msg["Subject"] = "Hello"
    msg["Date"] = "Mon, 01 Sep 2025 12:00:00 +0000"
    msg.set_content("plain text body")
    msg.add_attachment(b"PDF-attachment-bytes", maintype="application", subtype="pdf", filename="bill.pdf")
    eml_path = inbox / "001.eml"
    eml_path.write_bytes(bytes(msg))

    poller = LocalMailboxPoller(db, source, tmp_path)
    items = list(poller.fetch())
    assert len(items) == 1
    assert items[0].filename == "bill.pdf"
    assert items[0].sender == "billing@example.com"
    assert items[0].source_msg_ref == "001.eml"


def test_local_mailbox_emits_body_for_allowlisted_sender(tmp_path: Path):
    db, source = _make_db_with_source(
        SourceKind.gmail, body_allowlist=["airbnb.com"]
    )
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    msg = EmailMessage()
    msg["From"] = "noreply@airbnb.com"
    msg["Subject"] = "Payout summary"
    msg.set_content("Your payout for Beach House: $400")
    eml_path = inbox / "payout.eml"
    eml_path.write_bytes(bytes(msg))

    items = list(LocalMailboxPoller(db, source, tmp_path).fetch())
    assert len(items) == 1
    assert items[0].extra.get("body_only") is True
    assert items[0].mime_type == "text/html"


def test_local_mailbox_skips_non_allowlisted_body(tmp_path: Path):
    db, source = _make_db_with_source(SourceKind.gmail)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    msg = EmailMessage()
    msg["From"] = "random@example.com"
    msg["Subject"] = "FYI"
    msg.set_content("body but no attachment and not allowlisted")
    (inbox / "x.eml").write_bytes(bytes(msg))

    items = list(LocalMailboxPoller(db, source, tmp_path).fetch())
    assert items == []
