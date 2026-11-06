import os
import time

import pytest

from wishlist import images
from wishlist.pictures import PictureError


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield app


def test_store_and_delete(ctx):
    name = images.store(b"jpegdata")
    path = images.path_for(name)
    assert path and open(path, "rb").read() == b"jpegdata"
    assert path.startswith(ctx.config["WISHLIST"].images_dir)
    images.delete(name)
    assert images.path_for(name) is None and not os.path.exists(path)
    images.delete(name)
    images.delete(None)


def test_names_are_random_jpg(ctx):
    a, b = images.store(b"1"), images.store(b"2")
    assert a != b and len(a) == 26 and a.endswith(".jpg")


@pytest.mark.parametrize("bad", ["../wishlist.db", "x.jpg", "a" * 22 + ".png", "", None, "../" + "a" * 22 + ".jpg"])
def test_path_for_rejects_traversal(ctx, bad):
    assert images.path_for(bad) is None
    assert images.temp_path(bad) is None
    images.delete(bad)


def test_path_for_missing_file(ctx):
    assert images.path_for("a" * 22 + ".jpg") is None


def test_temp_promote_and_cleanup(ctx):
    token = images.store_temp(b"preview")
    assert images.temp_path(token) and images.path_for(token) is None
    assert images.promote_temp(token) == token
    assert images.temp_path(token) is None and images.path_for(token)

    old = images.store_temp(b"old")
    fresh = images.store_temp(b"fresh")
    stale = time.time() - 7200
    os.utime(images.temp_path(old), (stale, stale))
    assert images.cleanup_temp() == 1
    assert images.temp_path(old) is None and images.temp_path(fresh)


def test_store_temp_cleans_up_old_files(ctx):
    old = images.store_temp(b"old")
    stale = time.time() - 7200
    os.utime(images.temp_path(old), (stale, stale))
    images.store_temp(b"new")
    assert images.temp_path(old) is None


def test_promote_missing_raises(ctx):
    with pytest.raises(PictureError):
        images.promote_temp("a" * 22 + ".jpg")
    with pytest.raises(PictureError):
        images.promote_temp("../x")


def test_cleanup_without_tmp_dir(ctx):
    assert images.cleanup_temp() == 0
