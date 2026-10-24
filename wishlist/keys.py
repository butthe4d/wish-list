import hashlib
import hmac
import secrets


def new_key() -> str:
    return secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _mac(secret: str, wishlist_id: int, version: int) -> str:
    msg = f"share:{wishlist_id}:{version}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def share_key(secret: str, wishlist_id: int, version: int) -> str:
    return f"{wishlist_id}.{_mac(secret, wishlist_id, version)}"


def parse_share_key(key: str) -> int | None:
    head, sep, tail = key.partition(".")
    if not sep or not tail or not head.isascii() or not head.isdigit():
        return None
    return int(head)


def verify_share_key(secret: str, key: str, wishlist_id: int, version: int) -> bool:
    # compare bytes: str args raise TypeError on non-ASCII input from the URL
    return hmac.compare_digest(key.encode(), share_key(secret, wishlist_id, version).encode())


def personal_link(base_url: str, key: str) -> str:
    return f"{base_url}/u/{key}"


def share_link(base_url: str, key: str) -> str:
    return f"{base_url}/s/{key}"
