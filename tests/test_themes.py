import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from wishlist.catalog import create_wishlist, update_wishlist
from wishlist.errors import ValidationError
from wishlist.models import Wishlist
from wishlist.themes import DESIGNS, THEMES, get_settings, update_settings, validate_theme

pytestmark = pytest.mark.usefixtures("catalog_mailer")


def test_registry_keys():
    assert list(THEMES) == ["classic", "christmas", "birthday"]
    assert list(DESIGNS) == ["a"]


def test_settings_defaults_and_update(db):
    st = get_settings(db)
    assert (st.design, st.default_theme) == ("a", "classic")
    update_settings(db, design="a", default_theme="christmas")
    assert get_settings(db).default_theme == "christmas"


@pytest.mark.parametrize(
    "field,kw",
    [
        ("design", {"design": "b", "default_theme": "classic"}),
        ("default_theme", {"design": "a", "default_theme": "neon"}),
    ],
)
def test_settings_reject_unknown(db, field, kw):
    with pytest.raises(ValidationError) as e:
        update_settings(db, **kw)
    assert e.value.field == field


def test_validate_theme():
    assert validate_theme("birthday") == "birthday"
    with pytest.raises(ValidationError) as e:
        validate_theme("neon")
    assert e.value.field == "theme"


def test_new_list_defaults_to_classic(db, user):
    assert create_wishlist(db, user, name="X", description=None).theme == "classic"


def test_new_list_uses_admin_default_theme(db, user):
    update_settings(db, design="a", default_theme="birthday")
    assert create_wishlist(db, user, name="B-day", description=None).theme == "birthday"


def test_create_wishlist_explicit_theme(db, user):
    assert create_wishlist(db, user, name="X", description=None, theme="christmas").theme == "christmas"
    with pytest.raises(ValidationError):
        create_wishlist(db, user, name="Y", description=None, theme="neon")


def test_update_wishlist_theme_validated(app, db, user):
    fresh = sessionmaker(bind=app.extensions["wishlist_engine"], expire_on_commit=False)()
    wl = create_wishlist(db, user, name="X", description=None)
    with pytest.raises(ValidationError):
        update_wishlist(db, wl, name="X", description=None, theme="neon")
    update_wishlist(db, wl, name="X", description=None, theme="christmas")
    assert fresh.get(Wishlist, wl.id).theme == "christmas"
    fresh.close()


def test_get_settings_is_read_only_on_seeded_db(app, db):
    st = get_settings(db)
    assert (st.id, st.design, st.default_theme) == (1, "a", "classic")
    assert not db.new and not db.dirty
    assert db.execute(text("SELECT count(*) FROM app_settings")).scalar() == 1


def test_get_settings_missing_row_returns_unsaved_default(app, db):
    db.execute(text("DELETE FROM app_settings"))
    db.commit()
    st = get_settings(db)
    assert (st.design, st.default_theme) == ("a", "classic")
    assert not db.new
    assert db.execute(text("SELECT count(*) FROM app_settings")).scalar() == 0
    update_settings(db, design="a", default_theme="birthday")
    assert get_settings(db).default_theme == "birthday"
    assert db.execute(text("SELECT count(*) FROM app_settings")).scalar() == 1
