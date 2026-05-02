from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, PdfPassword
from app.passwords.vault import is_encrypted, try_decrypt


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_is_encrypted_detects_encrypt_dict():
    payload = b"%PDF-1.5\n...\n/Encrypt 12 0 R\n..."
    assert is_encrypted(payload)


def test_is_encrypted_false_for_plain_pdf():
    payload = b"%PDF-1.5\n...\nplain content\n..."
    assert not is_encrypted(payload)


def test_is_encrypted_false_for_non_pdf():
    assert not is_encrypted(b"PNG")


def test_try_decrypt_returns_unchanged_for_unencrypted_data():
    db = _db()
    plain = b"%PDF-1.5\nhello\n"
    result = try_decrypt(db, data=plain)
    assert not result.was_encrypted
    assert result.data is plain


def test_ranking_prefers_matchers_then_priority():
    from app.passwords.vault import _ranked_candidates

    db = _db()
    # Three passwords: priority order is 10, 50, 100;
    # but the one with sender_match='anz' should win for ANZ docs.
    a = PdfPassword(label="Default", password="def", priority=10)
    b = PdfPassword(label="ANZ", password="anz", priority=100, sender_match="anz")
    c = PdfPassword(label="Mid", password="mid", priority=50)
    db.add_all([a, b, c])
    db.commit()

    ranked = _ranked_candidates(db, sender="alerts@anz.com.au", filename="statement.pdf")
    assert [r.label for r in ranked] == ["ANZ", "Default", "Mid"]

    # No matcher hit -> pure priority order
    ranked = _ranked_candidates(db, sender="other@example.com", filename="x.pdf")
    assert [r.label for r in ranked] == ["Default", "Mid", "ANZ"]
