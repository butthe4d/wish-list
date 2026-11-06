"""Storage of processed item pictures under ``DATA_DIR/images``.

File names are random (``secrets.token_urlsafe(16) + ".jpg"``); anything else is
refused by ``path_for``/``temp_path`` so a name can never escape the directory.
Previews of fetched pictures live in ``images/tmp`` until they are promoted.
"""

import os
import re
import secrets
import time

from flask import current_app

from .pictures import PictureError

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{22}\.jpg$")


def _images_dir() -> str:
    return current_app.config["WISHLIST"].images_dir


def _tmp_dir() -> str:
    return os.path.join(_images_dir(), "tmp")


def _new_name() -> str:
    return secrets.token_urlsafe(16) + ".jpg"


def _write(directory: str, data: bytes) -> str:
    os.makedirs(directory, exist_ok=True)
    name = _new_name()
    with open(os.path.join(directory, name), "wb") as f:
        f.write(data)
    return name


def _existing(directory: str, name: str | None) -> str | None:
    if not name or not _NAME_RE.fullmatch(name):
        return None
    path = os.path.join(directory, name)
    return path if os.path.isfile(path) else None


def store(data: bytes) -> str:
    return _write(_images_dir(), data)


def delete(name: str | None) -> None:
    path = _existing(_images_dir(), name)
    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def path_for(name: str | None) -> str | None:
    return _existing(_images_dir(), name)


def store_temp(data: bytes) -> str:
    cleanup_temp()
    return _write(_tmp_dir(), data)


def temp_path(token: str | None) -> str | None:
    return _existing(_tmp_dir(), token)


def promote_temp(token: str) -> str:
    src = temp_path(token)
    if src is None:
        raise PictureError("temporary picture not found")
    os.makedirs(_images_dir(), exist_ok=True)
    try:
        os.replace(src, os.path.join(_images_dir(), token))
    except FileNotFoundError as exc:
        raise PictureError("temporary picture not found") from exc
    return token


def cleanup_temp(max_age_seconds: int = 3600) -> int:
    directory = _tmp_dir()
    if not os.path.isdir(directory):
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in os.scandir(directory):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
                removed += 1
        except FileNotFoundError:
            pass
    return removed
