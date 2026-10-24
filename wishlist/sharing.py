from __future__ import annotations

from sqlalchemy.orm import Session

from .keys import parse_share_key, verify_share_key
from .models import Wishlist


def open_share(s: Session, secret: str, key: str) -> Wishlist | None:
    wl_id = parse_share_key(key)
    if wl_id is None or wl_id > 2**63 - 1:
        return None
    wl = s.get(Wishlist, wl_id)
    if wl is None or not verify_share_key(secret, key, wl.id, wl.share_version):
        return None
    return wl


def regenerate_share_key(s: Session, wl: Wishlist) -> None:
    wl.share_version += 1
    s.commit()
