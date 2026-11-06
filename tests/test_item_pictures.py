import io
import os

import pytest
from PIL import Image

from conftest import _send, login, make_user, route
from wishlist import catalog, images, members, pictures, reservations
from wishlist.catalog import create_item, delete_item, set_item_image
from wishlist.errors import ValidationError
from wishlist.models import Item, Reservation


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def ctx(app):
    with app.test_request_context():
        yield


def _item(db, user):
    return create_item(db, user, name="Lamp", description=None, link=None)


def _with_image(db, user):
    item = _item(db, user)
    set_item_image(db, item, ("temp", images.store_temp(_jpeg())))
    return item


def test_keep_replace_none(ctx, db, user):
    item = _item(db, user)
    set_item_image(db, item, ("temp", images.store_temp(_jpeg())))
    first = item.image
    assert images.path_for(first)
    set_item_image(db, item, ("keep",))
    assert item.image == first
    set_item_image(db, item, ("temp", images.store_temp(_jpeg())))
    assert item.image != first
    assert images.path_for(first) is None and images.path_for(item.image)
    last = item.image
    set_item_image(db, item, ("none",))
    assert item.image is None and images.path_for(last) is None


def test_unknown_choice_is_programming_error(ctx, db, user):
    with pytest.raises(ValueError):
        set_item_image(db, _item(db, user), ("bogus",))


def test_missing_temp_is_validation_error(ctx, db, user):
    with pytest.raises(ValidationError) as e:
        set_item_image(db, _item(db, user), ("temp", "A" * 22 + ".jpg"))
    assert e.value.field == "image"


def test_url_choice_downloads(ctx, local_server, allow_loopback, db, user):
    route("/p.jpg")(lambda h: _send(h, _jpeg(), content_type="image/jpeg"))
    item = _item(db, user)
    set_item_image(db, item, ("url", local_server + "/p.jpg"))
    assert images.path_for(item.image)


def test_bad_url_choice_is_validation_error(ctx, db, user):
    item = _item(db, user)
    with pytest.raises(ValidationError) as e:
        set_item_image(db, item, ("url", "http://10.0.0.1/x.jpg"))
    assert e.value.field == "image"
    assert item.image is None


def test_hard_delete_removes_file(ctx, catalog_mailer, db, user):
    item = _with_image(db, user)
    name = item.image
    delete_item(db, item)
    assert images.path_for(name) is None


def test_soft_delete_keeps_file_until_admin_purge(ctx, catalog_mailer, db, user, other_user):
    item = _with_image(db, user)
    name = item.image
    db.add(Reservation(
        item_id=item.id, reserver_name="Basia", guest_token_hash="0" * 64, via_wishlist_name="L"
    ))
    db.commit()
    db.refresh(item)
    delete_item(db, item)
    assert item.deleted_at is not None and images.path_for(name)
    r = db.query(Reservation).filter_by(item_id=item.id).one()
    reservations.cancel_by_admin(db, r)
    assert images.path_for(name) is None


def test_delete_member_removes_files(ctx, db, user, other_user):
    item = _with_image(db, user)
    name = item.image
    members.delete_member(db, user, other_user)
    assert images.path_for(name) is None


def test_image_route_access(app, client, db, user):
    with app.app_context():
        item = _with_image(db, user)
        name = item.image
    r = client.get(f"/images/{name}")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "private, max-age=86400"
    assert client.get("/images/" + "A" * 22 + ".jpg").status_code == 404
    assert client.get("/images/..%2Fwishlist.db").status_code == 404
    item.deleted_at = catalog.utcnow()
    db.commit()
    assert client.get(f"/images/{name}").status_code == 404


def test_temp_route_requires_member(app, client, db):
    with app.app_context():
        token = images.store_temp(_jpeg())
    assert client.get(f"/images/tmp/{token}").status_code == 401
    _, key = make_user(db, "Cara", "cara@example.com")
    login(client, key)
    r = client.get(f"/images/tmp/{token}")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert client.get("/images/tmp/" + "A" * 22 + ".jpg").status_code == 404


def test_rate_limit(monkeypatch):
    pictures._fetch_log.clear()
    now = [1000.0]
    monkeypatch.setattr(pictures.time, "monotonic", lambda: now[0])
    assert all(pictures.allow_fetch(1) for _ in range(30))
    assert not pictures.allow_fetch(1)
    assert pictures.allow_fetch(2)
    now[0] += 3601
    assert pictures.allow_fetch(1)
