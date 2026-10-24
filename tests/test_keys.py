from wishlist.keys import (
    hash_key,
    new_key,
    parse_share_key,
    personal_link,
    share_key,
    share_link,
    verify_share_key,
)


def test_share_key_roundtrip():
    k = share_key("s", 7, 1)
    assert parse_share_key(k) == 7
    assert verify_share_key("s", k, 7, 1)
    assert not verify_share_key("s", k, 7, 2)
    assert not verify_share_key("other", k, 7, 1)
    assert parse_share_key("garbage") is None and parse_share_key("x.y") is None


def test_new_key_and_hash():
    k = new_key()
    assert len(k) >= 43 and new_key() != k
    assert len(hash_key(k)) == 64 and hash_key(k) == hash_key(k)


def test_links():
    assert personal_link("https://w.example", "abc") == "https://w.example/u/abc"
    assert share_link("https://w.example", "abc") == "https://w.example/s/abc"
