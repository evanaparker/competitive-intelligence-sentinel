import hashlib

from hashing import compute_hash


def test_same_input_produces_same_hash():
    assert compute_hash("hello world") == compute_hash("hello world")


def test_different_input_produces_different_hash():
    assert compute_hash("hello world") != compute_hash("hello there")


def test_returns_sha256_hex_digest():
    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert compute_hash("hello world") == expected
