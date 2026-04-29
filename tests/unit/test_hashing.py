from app.utils.hashing import sha256_bytes


def test_sha256_is_stable():
    a = sha256_bytes(b"hello")
    b = sha256_bytes(b"hello")
    assert a == b
    assert len(a) == 64


def test_sha256_differs_for_different_input():
    assert sha256_bytes(b"a") != sha256_bytes(b"b")
