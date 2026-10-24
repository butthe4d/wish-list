# Household Wishlist App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted Flask app where household members keep wishlists, share them by email/SMS, and guests reserve items without the owner finding out — shipped as one small Docker image.

**Architecture:** One Flask app (`wishlist/` package) with server-rendered Jinja2 pages, SQLAlchemy 2.0 over a single SQLite file, Alembic migrations, Flask-Babel for gettext translations. Domain logic lives in plain functions (`catalog.py`, `reservations.py`, `sharing.py`, `members.py`) that take a SQLAlchemy `Session`; blueprints in `wishlist/views/` are thin. Email and SMS go through two small sender interfaces so tests use recording fakes.

**Tech Stack:** Python ≥ 3.12 (Docker: `python:3.14-slim`), Flask ≥ 3.1, SQLAlchemy ≥ 2.0, Alembic ≥ 1.14, Flask-Babel ≥ 4.0, Flask-WTF ≥ 1.2 (CSRF only), gunicorn ≥ 23, `gsmservice-gateway` (SzybkiSMS/GSMService SDK), pytest. Frontend: vendored Pico CSS (classless) + vendored htmx used only as `hx-boost` on `<body>`; no JS build.

**Spec:** `docs/superpowers/specs/2026-10-24-wishlist-app-design.md`

## Global Constraints

- License Apache-2.0; no secrets in code; all config from environment variables (exact names in Task 1).
- Supported languages: `en`, `pl`. **Every task that adds user-visible text** (templates, flashes, emails, SMS) wraps it in `_()` / `gettext`, then runs `make i18n-update`, fills the Polish `msgstr` in `wishlist/translations/pl/LC_MESSAGES/messages.po`, and the translation-completeness test (Task 4) must pass.
- Personal keys: `secrets.token_urlsafe(32)`, stored only as SHA-256 hex. Personal link format `<BASE_URL>/u/<key>`. Share link format `<BASE_URL>/s/<key>`.
- **Share keys deviate from the spec's "share-key hash" field:** a share key is derived, not stored: `f"{wishlist_id}.{hmac}"` with HMAC-SHA256 over `f"share:{wishlist_id}:{share_version}"` keyed by `SECRET_KEY`. Reason: a stored hash can't be re-sent, so every new share would kill earlier recipients' links. Still meets "the database alone can't be used to log in". Regenerating = incrementing `share_version`. Changing `SECRET_KEY` invalidates all share links and sessions (document in README).
- A list owner must never see reservation state of their own items, in any member view or share view. Admin sees it for their own items only on a page loaded with `?reveal=1` from the explicit "Show reservations" button.
- All external links built from `BASE_URL`, never from the request host.
- Responses send `Referrer-Policy: no-referrer`; store links render with `rel="noopener noreferrer" target="_blank"`.
- SMS is optional: when `GSMSERVICE_TOKEN` is unset, no SMS UI is rendered and SMS routes return 404. Email is optional: when `SMTP_HOST` is unset, links are shown on screen/logged instead.
- Every POST form includes the CSRF token (`{{ csrf_token() }}`).

## Review Focus

1. **Two guests reserve the same item** (second has a stale page) → second sees "Someone already reserved this", no 500, first reservation untouched. Test in Task 7.
2. **ID tampering**: a member requesting another member's item/list id, or a guest posting a reservation for an item not in the shared list or with a share session for a different list → 404. Tests in Tasks 5, 6, 7.
3. **Store link input** `javascript:alert(1)` → rejected with a form error; `example.com/x` → saved as `https://example.com/x`. Test in Task 5.
4. **Mail/SMS provider failure** (SMTP down, bad credentials, gsmservice error) during member creation, key change, or sharing → action still completes, user sees "Couldn't send…" and, for member links, the link on screen. Tests in Tasks 8 and 9.
5. **Admin locks themselves out**: deleting their own account or removing admin from the last admin → refused with a message. Test in Task 9.

---

## File Structure

```
pyproject.toml            deps, pytest config
Makefile                  test, i18n-update, i18n-compile
babel.cfg                 extraction config (python + jinja2)
alembic.ini               script_location = wishlist/migrations
Dockerfile, docker-compose.yml, docker/entrypoint.sh
.github/workflows/ci.yml
README.md, CLAUDE.md, .gitignore, .dockerignore
wishlist/
  __init__.py             create_app()
  config.py               Config, ConfigError, SUPPORTED_LANGUAGES
  db.py                   Base, engine/session setup, upgrade_db()
  models.py               User, Item, Wishlist, wishlist_items, Reservation, ShareLog
  keys.py                 key generation, hashing, share-key HMAC
  auth.py                 session login, current_user, member_required, admin_required
  i18n.py                 locale selection, /lang route
  notify.py               Mailer/SmsSender protocols, SmtpMailer, GsmServiceSms, DeliveryError
  messages.py             localized email/SMS bodies
  catalog.py              items & wishlists domain logic
  reservations.py         reserve/cancel/state/orphans
  sharing.py              share link open/regenerate, share by email/SMS
  members.py              admin member management, key rotation
  bootstrap.py            first-admin creation (python -m wishlist.bootstrap)
  views/{__init__,member,shared,admin}.py
  templates/…, static/{pico.classless.min.css,htmx.min.js,app.css}
  translations/{en,pl}/LC_MESSAGES/messages.po, messages.pot
  migrations/{env.py,script.py.mako,versions/0001_initial.py}
tests/
  conftest.py, fakes.py, test_*.py
```

---

### Task 1: Project skeleton, config, app factory

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `wishlist/__init__.py`, `wishlist/config.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_app.py`

**Interfaces:**
- Produces:
  - `SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "pl")`
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Config` with fields `base_url: str, secret_key: str, database_url: str = "sqlite:////data/wishlist.db", admin_name: str | None = None, admin_email: str | None = None, default_language: str = "en", smtp_host: str | None = None, smtp_port: int = 587, smtp_user: str | None = None, smtp_password: str | None = None, smtp_from: str | None = None, smtp_security: str = "starttls", gsmservice_token: str | None = None, gsmservice_sender: str | None = None`; properties `email_enabled` (smtp_host set) and `sms_enabled` (gsmservice_token set); `classmethod from_env(env: Mapping[str, str]) -> Config`.
  - Env names: `BASE_URL, SECRET_KEY, DATABASE_URL, ADMIN_NAME, ADMIN_EMAIL, DEFAULT_LANGUAGE, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY, GSMSERVICE_TOKEN, GSMSERVICE_SENDER`. Empty string = unset.
  - `create_app(config: Config | None = None, *, mailer=None, sms=None) -> Flask` (config `None` → `Config.from_env(os.environ)`); stores config at `app.config["WISHLIST"]`, sets `SECRET_KEY`, wraps WSGI in `ProxyFix(x_for=1, x_proto=1, x_host=1)`, adds `Referrer-Policy: no-referrer` header, route `GET /healthz` → `"ok"`.
  - Test fixture `app_config(tmp_path) -> Config` (base_url `"https://wish.example"`, sqlite in tmp_path), `app`, `client`.

- [ ] **Step 1: Write `pyproject.toml`** (project `wishlist`, deps from Tech Stack, `[project.optional-dependencies] dev = ["pytest>=8"]`, `[tool.pytest.ini_options] testpaths=["tests"]`), `.gitignore` (`__pycache__/`, `*.mo`, `.venv/`, `*.db`, `.pytest_cache/`), `Makefile` with `test: pytest -q`. Create venv: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`. Expected: install succeeds (if `gsmservice-gateway` fails on 3.14, note it and use a 3.13 venv/base image instead).

- [ ] **Step 2: Write failing tests** `tests/test_config.py`:

```python
def test_requires_base_url_and_secret():
    with pytest.raises(ConfigError) as e:
        Config.from_env({})
    assert "BASE_URL" in str(e.value) and "SECRET_KEY" in str(e.value)

def test_defaults_and_trailing_slash():
    c = Config.from_env({"BASE_URL": "https://w.example/", "SECRET_KEY": "s"})
    assert c.base_url == "https://w.example"
    assert c.database_url == "sqlite:////data/wishlist.db"
    assert c.default_language == "en"
    assert not c.email_enabled and not c.sms_enabled

def test_smtp_from_defaults_to_user_and_port_parsed():
    c = Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": "s", "SMTP_HOST": "smtp.gmail.com",
                         "SMTP_PORT": "465", "SMTP_USER": "me@gmail.com", "SMTP_SECURITY": "ssl"})
    assert c.email_enabled and c.smtp_port == 465 and c.smtp_from == "me@gmail.com"

@pytest.mark.parametrize("key,val", [("SMTP_SECURITY", "tls"), ("DEFAULT_LANGUAGE", "de"), ("SMTP_PORT", "abc")])
def test_rejects_invalid_values(key, val):
    with pytest.raises(ConfigError):
        Config.from_env({"BASE_URL": "https://w", "SECRET_KEY": "s", key: val})
```

`tests/test_app.py`: `client.get("/healthz")` → 200, body `b"ok"`, header `Referrer-Policy == "no-referrer"`.

- [ ] **Step 3: Run** `.venv/bin/pytest -q` → FAIL (import errors).
- [ ] **Step 4: Implement** `wishlist/config.py` and `wishlist/__init__.py` per Interfaces.
- [ ] **Step 5: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 6: Commit** `feat: project skeleton, config and app factory`.

---

### Task 2: Database, models, initial migration

**Files:**
- Create: `wishlist/db.py`, `wishlist/models.py`, `alembic.ini`, `wishlist/migrations/env.py`, `wishlist/migrations/script.py.mako`, `wishlist/migrations/versions/0001_initial.py`, `tests/test_models.py`
- Modify: `wishlist/__init__.py` (init db on app), `tests/conftest.py` (run `upgrade_db` per test, provide `db` session fixture)

**Interfaces:**
- Produces:
  - `class Base(DeclarativeBase)`; `init_db(app: Flask) -> None` creates engine from `database_url` (SQLite: `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL` on connect) and a scoped session removed on teardown; `get_session() -> Session` (current request/app-context session); `upgrade_db(database_url: str) -> None` runs Alembic `upgrade head` programmatically.
  - Models (all timestamps UTC `datetime`):
    - `User`: `id, name: str(100), email: str(254) unique, language: str(5), is_admin: bool, key_hash: str(64) unique, key_version: int = 1, created_at`; relationships `items`, `wishlists` (cascade delete-orphan).
    - `Item`: `id, owner_id FK(user, ondelete CASCADE), name: str(200), description: str(2000)|None, link: str(2000)|None, created_at, updated_at, deleted_at|None`; `wishlists` (secondary), `reservation` (uselist=False).
    - `wishlist_items` table: `wishlist_id FK CASCADE, item_id FK CASCADE`, composite PK.
    - `Wishlist`: `id, owner_id FK CASCADE, name: str(100), description: str(500)|None, share_version: int = 1, created_at, updated_at`; `items` (secondary), `share_logs`.
    - `Reservation`: `id, item_id FK(item, CASCADE) UNIQUE, reserver_name: str(100), guest_token_hash: str(64), via_wishlist_id FK(wishlist, SET NULL)|None, via_wishlist_name: str(100), created_at, warning_dismissed_at|None`.
    - `ShareLog`: `id, wishlist_id FK CASCADE, channel: str ("email"|"sms"), recipient: str(254), sent_at`.
- Alembic `env.py` must use `render_as_batch=True` and read the URL from `config.attributes["database_url"]` or `alembic.ini`.

- [ ] **Step 1: Write failing tests** `tests/test_models.py`:

```python
def test_migration_creates_all_tables(app_config):
    upgrade_db(app_config.database_url)
    names = set(sa.inspect(sa.create_engine(app_config.database_url)).get_table_names())
    assert {"user", "item", "wishlist", "wishlist_items", "reservation", "share_log"} <= names

def test_one_reservation_per_item(db, user):
    item = Item(owner=user, name="Lamp"); db.add(item); db.flush()
    db.add(Reservation(item=item, reserver_name="A", guest_token_hash="x", via_wishlist_name="L"))
    db.flush()
    db.add(Reservation(item=item, reserver_name="B", guest_token_hash="y", via_wishlist_name="L"))
    with pytest.raises(sa.exc.IntegrityError):
        db.flush()

def test_deleting_user_cascades(db, user):
    wl = Wishlist(owner=user, name="B-day"); item = Item(owner=user, name="Lamp", wishlists=[wl])
    db.add_all([wl, item]); db.commit()
    db.delete(user); db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(Item)) == 0

def test_deleting_wishlist_keeps_reservation_name(db, user): ...
    # reservation via wl; delete wl; reservation.via_wishlist_id is None, via_wishlist_name == "B-day"
```

Add a `user` fixture in conftest creating a plain `User(name="Ania", email="ania@example.com", language="en", is_admin=False, key_hash="0"*64)`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_models.py -q` → FAIL.
- [ ] **Step 3: Implement** `db.py` and `models.py`; generate migration with `.venv/bin/alembic revision --autogenerate -m initial` against an empty temp DB, rename to `0001_initial.py`, review it.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: data model and initial migration`.

---

### Task 3: Keys and member sessions

**Files:**
- Create: `wishlist/keys.py`, `wishlist/auth.py`, `tests/test_keys.py`, `tests/test_auth.py`
- Modify: `wishlist/__init__.py` (register auth routes, `PERMANENT_SESSION_LIFETIME = timedelta(days=365)`, `SESSION_COOKIE_SAMESITE="Lax"`, `SESSION_COOKIE_SECURE` when base_url is https), `tests/conftest.py` (helpers)

**Interfaces:**
- Produces:
  - `new_key() -> str` (`secrets.token_urlsafe(32)`); `hash_key(key: str) -> str` (sha256 hex)
  - `share_key(secret: str, wishlist_id: int, version: int) -> str`; `parse_share_key(key: str) -> int | None` (wishlist id or None if malformed); `verify_share_key(secret: str, key: str, wishlist_id: int, version: int) -> bool` (`hmac.compare_digest`)
  - `personal_link(base_url: str, key: str) -> str` → `f"{base_url}/u/{key}"`; `share_link(base_url, key) -> str` → `f"{base_url}/s/{key}"`
  - `auth.py`: `bp` blueprint with `GET /u/<key>`: look up `User` by `hash_key(key)`; found → `session.permanent=True`, `session["uid"]`, `session["kv"]=key_version`, redirect `/`; not found → 404 page "This link is no longer valid. Ask your admin for a new one."
  - `current_user() -> User | None` (None if session kv != user.key_version); `member_required` and `admin_required` decorators (no member → render `welcome.html` with status 401 telling them to use their link; non-admin on admin route → 404).
  - conftest helpers: `make_user(db, name, email, *, is_admin=False, language="en") -> tuple[User, str]` (returns plain key), `login(client, key)`.

- [ ] **Step 1: Write failing tests**:

```python
# test_keys.py
def test_share_key_roundtrip():
    k = share_key("s", 7, 1)
    assert parse_share_key(k) == 7
    assert verify_share_key("s", k, 7, 1)
    assert not verify_share_key("s", k, 7, 2)
    assert not verify_share_key("other", k, 7, 1)
    assert parse_share_key("garbage") is None and parse_share_key("x.y") is None

# test_auth.py
def test_link_logs_in_and_redirects(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    r = client.get(f"/u/{key}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert client.get("/").status_code == 200

def test_unknown_key_404(client): assert client.get("/u/nope").status_code == 404

def test_rotated_key_kills_session(client, db):
    u, key = make_user(db, "Ania", "a@x.pl"); login(client, key)
    u.key_version += 1; u.key_hash = hash_key(new_key()); db.commit()
    assert client.get("/").status_code == 401
    assert client.get(f"/u/{key}").status_code == 404

def test_non_admin_gets_404_on_admin(client, db):
    _, key = make_user(db, "Ania", "a@x.pl"); login(client, key)
    assert client.get("/admin/").status_code == 404
```

(`/` and `/admin/` can be placeholder routes in this task; Tasks 5 and 9 replace them.)

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_keys.py tests/test_auth.py -q` → FAIL.
- [ ] **Step 3: Implement** `keys.py`, `auth.py`, placeholder views.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: personal keys, share-key HMAC and member sessions`.

---

### Task 4: Localization, base layout, language switcher

**Files:**
- Create: `wishlist/i18n.py`, `babel.cfg`, `wishlist/translations/{en,pl}/LC_MESSAGES/messages.po`, `wishlist/templates/base.html`, `wishlist/templates/welcome.html`, `wishlist/static/{pico.classless.min.css,htmx.min.js,app.css}`, `tests/test_i18n.py`, `tests/test_translations.py`
- Modify: `Makefile` (`i18n-update`: `pybabel extract -F babel.cfg -o wishlist/translations/messages.pot . && pybabel update -i wishlist/translations/messages.pot -d wishlist/translations`; `i18n-compile`: `pybabel compile -d wishlist/translations`), `wishlist/__init__.py`, `tests/conftest.py` (session-scoped autouse fixture running `pybabel compile -d wishlist/translations`)

**Interfaces:**
- Produces:
  - `select_locale() -> str`: `current_user().language` if member; else cookie `lang` if in `SUPPORTED_LANGUAGES`; else `request.accept_languages.best_match(SUPPORTED_LANGUAGES)`; else config `default_language`.
  - `POST /lang` form field `lang` (+ hidden `next`, only relative paths accepted): sets cookie `lang` (1 year); if member, saves `user.language`; redirects to `next` or `/`. Unsupported → 400.
  - `base.html`: `<html lang="{{ get_locale() }}">`, Pico + app.css, `<body hx-boost="true">`, nav block, flashed messages, footer with language switcher form (`English`, `Polski`). All later templates extend it.
  - Vendored files: Pico CSS v2 classless minified, htmx 2.x minified (download once, commit).

- [ ] **Step 1: Write failing tests**:

```python
# test_i18n.py
def test_guest_gets_accept_language(client):
    r = client.get("/", headers={"Accept-Language": "pl-PL,pl;q=0.9"})
    assert 'lang="pl"' in r.text

def test_switcher_sets_cookie_and_member_pref(client, db):
    u, key = make_user(db, "Ania", "a@x.pl", language="en"); login(client, key)
    r = client.post("/lang", data={"lang": "pl", "next": "/"})
    assert r.status_code == 302; db.refresh(u); assert u.language == "pl"
    assert 'lang="pl"' in client.get("/").text

def test_switcher_rejects_unknown_and_external_next(client):
    assert client.post("/lang", data={"lang": "de"}).status_code == 400
    r = client.post("/lang", data={"lang": "pl", "next": "https://evil.example/"})
    assert r.headers["Location"] == "/"

# test_translations.py
def test_every_message_translated_to_polish(tmp_path):
    # run `pybabel extract -F babel.cfg -o <tmp>/m.pot .`, load with babel.messages.pofile.read_po
    # assert every msgid in the pot exists in pl messages.po, with non-empty msgstr and not fuzzy
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_i18n.py tests/test_translations.py -q` → FAIL.
- [ ] **Step 3: Implement** i18n, templates, vendored static files; `make i18n-update`, translate `pl`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: Flask-Babel localization (en, pl), base layout, language switcher`.

---

### Task 5: Items (member)

**Files:**
- Create: `wishlist/catalog.py`, `wishlist/views/__init__.py`, `wishlist/views/member.py`, templates `home.html`, `item_form.html`, `tests/test_catalog_items.py`, `tests/test_member_items_views.py`

**Interfaces:**
- Consumes: models (Task 2), `member_required`, `current_user` (Task 3).
- Produces (`catalog.py`; all raise `ValidationError(field: str, message: str)` on bad input):
  - `normalize_link(raw: str | None) -> str | None`: blank → None; no scheme → prefix `https://`; scheme not `http`/`https` → `ValidationError("link", …)`; must have a host.
  - `create_item(s: Session, owner: User, *, name: str, description: str | None, link: str | None, wishlist_ids: Iterable[int] = ()) -> Item` (name stripped, required, ≤200; description ≤2000).
  - `update_item(s, item: Item, *, name, description, link, wishlist_ids) -> Item` (replaces list membership; keeps reservation).
  - `delete_item(s, item: Item) -> None`: if `item.reservation` exists → set `deleted_at`, remove from all wishlists; else hard delete.
  - `get_owned_item(s, user: User, item_id: int) -> Item`: `abort(404)` if missing, not owned, or deleted.
  - `set_item_wishlists(s, item, wishlist_ids)`: every id must be a wishlist owned by `item.owner` else `abort(404)`.
  - Routes: `GET /` (home: user's lists with item counts + all non-deleted items), `GET|POST /items/new`, `GET|POST /items/<int:item_id>/edit`, `POST /items/<int:item_id>/delete`. Item form has name, description, link, and one checkbox per own wishlist. No reservation info anywhere.

- [ ] **Step 1: Write failing tests** (`test_catalog_items.py`):

```python
@pytest.mark.parametrize("raw,expected", [("", None), ("example.com/x", "https://example.com/x"),
                                          ("http://shop.pl/a?b=1", "http://shop.pl/a?b=1")])
def test_normalize_link(raw, expected): assert normalize_link(raw) == expected

@pytest.mark.parametrize("raw", ["javascript:alert(1)", "ftp://x.pl/a", "https://"])
def test_normalize_link_rejects(raw):
    with pytest.raises(ValidationError): normalize_link(raw)

def test_delete_unreserved_item_is_hard(db, user): ...   # item gone from DB
def test_delete_reserved_item_is_soft(db, user): ...     # deleted_at set, wishlists == [], reservation kept
def test_cannot_put_item_in_foreign_list(db, user, other_user): ...  # set_item_wishlists → werkzeug NotFound
def test_update_keeps_reservation(db, user): ...         # rename item, reservation still attached
```

`test_member_items_views.py`: create via POST `/items/new` → appears on `/`; empty name → 200 with error text, nothing created; `javascript:` link → error shown; other member's `/items/<id>/edit` → 404; soft-deleted item not on `/`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_catalog_items.py tests/test_member_items_views.py -q` → FAIL.
- [ ] **Step 3: Implement** catalog item functions, member views, templates; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: member item management`.

---

### Task 6: Wishlists (member, owner view)

**Files:**
- Modify: `wishlist/catalog.py`, `wishlist/views/member.py`, `home.html`
- Create: `wishlist/templates/list_owner.html`, `tests/test_catalog_lists.py`, `tests/test_member_lists_views.py`

**Interfaces:**
- Consumes: Task 5 functions; `share_key`, `share_link` (Task 3).
- Produces:
  - `create_wishlist(s, owner: User, *, name: str, description: str | None) -> Wishlist` (name required ≤100, description ≤500)
  - `update_wishlist(s, wl, *, name, description) -> Wishlist`
  - `delete_wishlist(s, wl) -> None` (items stay; membership rows cascade)
  - `add_items_to_wishlist(s, wl, item_ids: Iterable[int])` (ids must be owner's non-deleted items else 404); `remove_item_from_wishlist(s, wl, item: Item)`
  - `get_owned_wishlist(s, user, wishlist_id) -> Wishlist` (404 otherwise)
  - Routes: `POST /lists`, `GET /lists/<int:wl_id>`, `POST /lists/<int:wl_id>/edit`, `POST /lists/<int:wl_id>/delete`, `POST /lists/<int:wl_id>/items` (field `item_id`, multi), `POST /lists/<int:wl_id>/items/<int:item_id>/remove`.
  - `list_owner.html` shows items, an "add items" picker of own items not in the list, rename/delete, and the share link (`share_link(base_url, share_key(secret, wl.id, wl.share_version))`) with a copy field. Share-by-email/SMS forms and "new share link" button are added in Task 8 — leave a `{% block share %}` placeholder.

- [ ] **Step 1: Write failing tests**: create list → on home; add/remove item; deleting a list keeps its items; other member's `/lists/<id>` → 404; adding another member's item id → 404; owner page HTML contains `"/s/"` link and does **not** contain the reserver name or the word for "reserved" when the item is reserved (assert `"Mom" not in r.text` and `"reserved" not in r.text.lower()`).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_catalog_lists.py tests/test_member_lists_views.py -q` → FAIL.
- [ ] **Step 3: Implement**; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: member wishlists`.

---

### Task 7: Share links, guest view, reservations

**Files:**
- Create: `wishlist/reservations.py`, `wishlist/sharing.py`, `wishlist/views/shared.py`, templates `shared_list.html`, `link_invalid.html`, `tests/test_reservations.py`, `tests/test_shared_views.py`

**Interfaces:**
- Consumes: keys (Task 3), catalog (Tasks 5–6).
- Produces:
  - `sharing.open_share(s, secret: str, key: str) -> Wishlist | None`; `sharing.regenerate_share_key(s, wl) -> None` (increments `share_version`).
  - `class AlreadyReserved(Exception)`
  - `reservations.reserve(s, item: Item, via: Wishlist, *, reserver_name: str, guest_token: str) -> Reservation` (name stripped, required, ≤100 → `ValidationError`; existing reservation or IntegrityError → `AlreadyReserved`; stores `hash_key(guest_token)` and `via.name`).
  - `reservations.cancel_by_guest(s, item, guest_token: str) -> bool` (only if token hash matches).
  - `reservations.state_for_guest(item, guest_token: str | None) -> Literal["available", "reserved", "mine"]`.
  - Guest cookie `guest` = `new_key()`, set on first share visit, 1 year, HttpOnly, SameSite=Lax.
  - Routes: `GET /s/<key>` → invalid → `link_invalid.html` 404; if `current_user()` owns the list → redirect `/lists/<id>`; else `session["shares"][str(wl.id)] = wl.share_version`, redirect `/shared/<id>`. `GET /shared/<int:wl_id>` (requires matching share session version, else 404; owner session → redirect to owner view). `POST /shared/<int:wl_id>/items/<int:item_id>/reserve` (field `name`), `POST /shared/<int:wl_id>/items/<int:item_id>/cancel`. Item must be in that list and not deleted, else 404.
  - `shared_list.html`: list name, owner's first name (`owner.name.split()[0]`), items (name, description, store link), per-item state: available → name field + "Reserve"; reserved → "Reserved" (no name); mine → "Reserved by you" + "Cancel".

- [ ] **Step 1: Write failing tests**:

```python
def test_second_reservation_rejected(db, item_in_list):
    item, wl = item_in_list
    reserve(db, item, wl, reserver_name="Mom", guest_token="g1")
    with pytest.raises(AlreadyReserved):
        reserve(db, item, wl, reserver_name="Dad", guest_token="g2")
    assert item.reservation.reserver_name == "Mom"

def test_state_for_guest(db, item_in_list): ...  # available / mine for g1 / reserved for g2 / reserved for None
def test_cancel_only_by_same_guest(db, item_in_list): ...
def test_reservation_visible_in_every_list(db, user): ...  # item in lists A and B; reserve via A; state via B is "reserved"
```

`test_shared_views.py`:
- guest opens `/s/<key>` → 302 → `/shared/<id>` shows item; reserve with name "Mom" → page shows "Reserved by you"; a second client sees "Reserved" and not "Mom".
- second client with stale page POSTs reserve → 200/302 with "already reserved" message, no 500.
- after `regenerate_share_key`, old `/s/<key>` → 404 and the existing guest session's `/shared/<id>` → 404.
- guest with share session for list A posts reserve for an item only in list B (`/shared/A/items/<B-item>/reserve`) → 404; `/shared/B` → 404.
- logged-in owner opening own `/s/<key>` → redirect to `/lists/<id>`; owner's `/shared/<id>` → redirect too.
- blank reserver name → error, no reservation.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_reservations.py tests/test_shared_views.py -q` → FAIL.
- [ ] **Step 3: Implement**; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: share links, guest view and reservations`.

---

### Task 8: Email/SMS senders and sharing by email/SMS

**Files:**
- Create: `wishlist/notify.py`, `wishlist/messages.py`, `tests/fakes.py`, `tests/test_notify.py`, `tests/test_share_send.py`
- Modify: `wishlist/__init__.py` (build senders from config unless injected: `app.extensions["mailer"]`, `app.extensions["sms"]`, `None` when disabled), `wishlist/sharing.py`, `wishlist/views/member.py`, `list_owner.html`

**Interfaces:**
- Produces:
  - `class DeliveryError(Exception)`; `Protocol Mailer.send(to: str, subject: str, body: str) -> None`; `Protocol SmsSender.send(to: str, text: str) -> None` — both raise only `DeliveryError`.
  - `SmtpMailer(config: Config)`: stdlib `smtplib` (`SMTP_SSL` for `ssl`, `starttls()` for `starttls`, plain for `none`), login if user set, timeout 15 s, `email.message.EmailMessage` plain text UTF-8; wraps `smtplib.SMTPException`/`OSError` in `DeliveryError`.
  - `GsmServiceSms(token: str, sender: str | None)`: `gsmservice_gateway.Client(bearer=token).outgoing.sms.send(request={"recipients": to, "message": text, "sender": sender, "type": 1, "unicode": True, "flash": False, "date_": None})`; any exception → `DeliveryError`.
  - `get_mailer() -> Mailer | None`, `get_sms() -> SmsSender | None` (from `current_app.extensions`).
  - `messages.py` (each renders under `flask_babel.force_locale(lang)`): `invite_email(user: User, link: str) -> tuple[str, str]` (user.language), `share_email(sharer: User, wl: Wishlist, link: str) -> tuple[str, str]` and `share_sms(sharer, wl, link) -> str` (sharer.language), `orphan_email(admin: User, r: Reservation) -> tuple[str, str]` (admin.language; used in Task 10).
  - `sharing.parse_emails(text: str) -> list[str]` (split on commas/whitespace/semicolons, dedupe, each must match `^[^@\s]+@[^@\s]+\.[^@\s]+$`, 1–10 addresses, else `ValidationError`); `sharing.normalize_phone(text: str) -> str` (strip spaces/dashes/parens; must match `^\+\d{8,15}$`, else `ValidationError` with hint "use international format, e.g. +48…").
  - `sharing.share_by_email(s, wl, sharer, recipients: list[str], mailer: Mailer) -> list[str]` (returns addresses that failed; logs `ShareLog` for successes); `sharing.share_by_sms(s, wl, sharer, phone: str, sms: SmsSender) -> None` (raises `DeliveryError`; logs on success).
  - Routes: `POST /lists/<id>/share/email` (field `emails`), `POST /lists/<id>/share/sms` (field `phone`; 404 if SMS disabled), `POST /lists/<id>/share/regenerate`. Owner page shows email form when mailer configured, SMS form only when SMS configured, "Create new share link (old one stops working)" button, and the share log (channel, recipient, time).
  - `tests/fakes.py`: `RecordingMailer` (`.sent: list[tuple[to, subject, body]]`), `RecordingSms` (`.sent: list[tuple[to, text]]`), `FailingMailer`, `FailingSms` (always raise `DeliveryError`). conftest `app` fixture injects `RecordingMailer()` and `RecordingSms()` by default; fixtures `mailer`, `sms` expose them.

- [ ] **Step 1: Write failing tests**:

```python
# test_notify.py
def test_smtp_mailer_starttls(monkeypatch, app_config): ...   # monkeypatch smtplib.SMTP with fake; assert starttls+login+send_message called, To/Subject set
def test_smtp_failure_becomes_delivery_error(monkeypatch, app_config): ...  # fake raises ConnectionRefusedError → DeliveryError
def test_gsmservice_payload(monkeypatch): ...  # monkeypatch gsmservice_gateway.Client; assert request["recipients"] == "+48600100200" and unicode True
def test_share_sms_polish(app, db): ...  # sharer.language="pl" → text differs from en version and contains link

# test_share_send.py
@pytest.mark.parametrize("raw,expected", [("a@x.pl, b@y.com", ["a@x.pl", "b@y.com"]), ("a@x.pl;a@x.pl", ["a@x.pl"])])
def test_parse_emails(raw, expected): assert parse_emails(raw) == expected
def test_parse_emails_rejects_bad(): ...   # "a@x.pl, nope" → ValidationError
@pytest.mark.parametrize("raw,expected", [("+48 600-100-200", "+48600100200")])
def test_normalize_phone(raw, expected): ...
def test_normalize_phone_rejects(): ...   # "600100200" → ValidationError
def test_share_email_sends_link_and_logs(client, db, mailer): ...   # body contains "https://wish.example/s/", ShareLog row channel "email"
def test_sms_form_hidden_and_route_404_when_disabled(...): ...       # app built with sms=None
def test_share_email_failure_shows_message(...): ...                 # FailingMailer → page shows "Couldn't send", no ShareLog
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_notify.py tests/test_share_send.py -q` → FAIL.
- [ ] **Step 3: Implement**; translate new strings including email/SMS bodies.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: email/SMS senders and sharing`.

---

### Task 9: Admin member management and first-admin bootstrap

**Files:**
- Create: `wishlist/members.py`, `wishlist/bootstrap.py`, `wishlist/views/admin.py`, templates `admin/index.html`, `admin/member_form.html`, `admin/link_once.html`, `tests/test_members.py`, `tests/test_admin_members_views.py`, `tests/test_bootstrap.py`
- Modify: `base.html` (admin nav link for admins)

**Interfaces:**
- Consumes: keys, `get_mailer`, `messages.invite_email`.
- Produces:
  - `members.create_member(s, *, name: str, email: str, language: str, is_admin: bool) -> tuple[User, str]` (email lowercased, unique → `ValidationError("email", …)`; returns plain key).
  - `members.update_member(s, user, *, name, email, language, is_admin, acting_admin: User) -> User` (refuse removing admin flag from the last admin → `ValidationError("is_admin", …)`).
  - `members.delete_member(s, user, acting_admin: User) -> None` (refuse self-delete → `ValidationError`).
  - `members.rotate_key(s, user) -> str` (new key, `key_version += 1`).
  - `members.send_link(user: User, key: str, mailer: Mailer | None) -> bool` (True if emailed; False if no mailer or `DeliveryError`).
  - Routes (all `admin_required`): `GET /admin/` (members table + warnings section placeholder for Task 10), `GET|POST /admin/members/new`, `GET|POST /admin/members/<int:uid>/edit`, `POST /admin/members/<int:uid>/delete`, `POST /admin/members/<int:uid>/rotate-key` (one button: "Change key and send new link"). After create/rotate, render `link_once.html` directly (never flash/store the key) showing the link, whether it was emailed, and a warning that it won't be shown again.
  - `bootstrap.main() -> int` (`python -m wishlist.bootstrap`): runs `upgrade_db`; if no users and `ADMIN_EMAIL` set → create admin (`ADMIN_NAME` or email local part, `DEFAULT_LANGUAGE`), print `Admin link: <link>` to stdout, email it if mailer configured; if no users and no `ADMIN_EMAIL` → print a warning; if users exist → do nothing. Returns 0.

- [ ] **Step 1: Write failing tests**:

```python
def test_create_member_duplicate_email(db): ...  # "A@x.pl" then "a@x.pl" → ValidationError
def test_cannot_demote_last_admin(db): ...
def test_cannot_delete_self(db): ...
def test_rotate_key_changes_hash_and_version(db): ...

# views
def test_admin_creates_member_and_link_emailed(admin_client, mailer): ...  # link_once shows "/u/", mailer.sent[0] to new email, body has same link
def test_link_shown_when_email_fails(...): ...   # FailingMailer → still 200, link shown, "Couldn't send"
def test_link_not_in_session_cookie(admin_client): ...  # after create, decoded session has no key string
def test_rotate_key_invalidates_old_link(admin_client, client, db): ...

# bootstrap
def test_bootstrap_creates_admin_once(monkeypatch, tmp_path, capsys): ...  # env with ADMIN_EMAIL; main() prints "Admin link: https://wish.example/u/"; second main() prints nothing new, user count 1
```

Add conftest fixture `admin_client` (logged-in admin "Maciej").

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_members.py tests/test_admin_members_views.py tests/test_bootstrap.py -q` → FAIL.
- [ ] **Step 3: Implement**; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: admin member management and first-admin bootstrap`.

---

### Task 10: Admin reservation views, warnings and notifications

**Files:**
- Modify: `wishlist/reservations.py`, `wishlist/catalog.py`, `wishlist/views/admin.py`, `admin/index.html`
- Create: `admin/member_lists.html`, `tests/test_orphans.py`, `tests/test_admin_reservations_views.py`

**Interfaces:**
- Consumes: Tasks 5–9.
- Produces:
  - `reservations.is_orphaned(r: Reservation) -> bool`: `r.item.deleted_at is not None or not r.item.wishlists`.
  - `reservations.orphaned_reservations(s, viewer: User, *, reveal_own: bool) -> list[Reservation]`: orphaned, `warning_dismissed_at is None`, excluding `item.owner_id == viewer.id` unless `reveal_own`.
  - `reservations.notify_orphan(s, item: Item, mailer: Mailer | None) -> None`: if item has an orphaned reservation, email `messages.orphan_email` to every admin **except the item's owner**; delivery errors are logged, never raised.
  - `reservations.cancel_by_admin(s, r: Reservation) -> None`: deletes reservation; if item soft-deleted → hard-delete item.
  - `reservations.dismiss_warning(s, r) -> None`: sets `warning_dismissed_at`.
  - `catalog.delete_item`, `update_item`, `remove_item_from_wishlist`, `delete_wishlist` call `notify_orphan` for each affected item after commit (use `get_mailer()`).
  - Routes: `GET /admin/members/<int:uid>` → `member_lists.html`: member's lists with items and reserver name + "via" + "Cancel reservation" button; when `uid == current admin` and no `?reveal=1`, render **no** reservation state, only a "Show reservations" button linking to `?reveal=1`. `POST /admin/reservations/<int:rid>/cancel`, `POST /admin/reservations/<int:rid>/dismiss` (for the admin's own items both require form field `reveal=1`, otherwise 404). `GET /admin/` warnings section lists `orphaned_reservations(viewer=admin, reveal_own="reveal" in request.args)` as "<item> — deleted/removed by <owner>, reserved by <name> via <list>", with dismiss/cancel buttons; its own "Show reservations" button.

- [ ] **Step 1: Write failing tests**:

```python
def test_soft_deleted_reserved_item_warns_and_emails_admin(db, member, admin, mailer): ...
    # member's item reserved by "Mom"; delete_item → orphaned_reservations(admin, reveal_own=False) has it; mailer.sent[0][0] == admin.email, body has "Mom"
def test_removed_from_last_list_warns(db, ...): ...
def test_still_in_other_list_no_warning(db, ...): ...
def test_admin_own_item_hidden_and_no_email(db, admin, mailer): ...
    # admin's item reserved then deleted → not in orphaned_reservations(admin, reveal_own=False), in it with reveal_own=True, mailer.sent == []
def test_cancel_by_admin_purges_soft_deleted(db, ...): ...
def test_dismiss_hides_warning(db, ...): ...

# views
def test_admin_sees_member_reserver(admin_client, ...): ...      # "/admin/members/<member>" contains "Mom"
def test_admin_own_lists_hidden_until_reveal(admin_client, ...): ...
    # "/admin/members/<admin>" → "Mom" not in text, "reserved" not in text.lower(), contains reveal link; with ?reveal=1 → "Mom" in text
def test_admin_index_hides_own_warnings(admin_client, ...): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_orphans.py tests/test_admin_reservations_views.py -q` → FAIL.
- [ ] **Step 3: Implement**; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat: admin reservation views, orphan warnings and notifications`.

---

### Task 11: CSRF, Docker image, CI, docs

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker/entrypoint.sh`, `docker-compose.yml`, `.github/workflows/ci.yml`, `README.md`, `CLAUDE.md`, `tests/test_csrf.py`
- Modify: `wishlist/__init__.py` (enable `CSRFProtect`; conftest sets `WTF_CSRF_ENABLED=False` except in `test_csrf.py`)

**Interfaces:**
- Dockerfile: multi-stage on `python:3.14-slim` (fallback 3.13 if Task 1 found SDK issues): build stage installs the package into a venv and runs `pybabel compile`; final stage copies venv + app, creates user `app` (uid 1000), `VOLUME /data`, `EXPOSE 8000`, `ENV DATABASE_URL=sqlite:////data/wishlist.db`, `HEALTHCHECK` via `python -c` urllib on `/healthz`, `ENTRYPOINT ["/app/docker/entrypoint.sh"]`.
- `entrypoint.sh`: `set -e; python -m wishlist.bootstrap; exec gunicorn -c python:wishlist.gunicorn_conf "wishlist:create_app()"`.
- `wishlist/gunicorn_conf.py` (add to Files): `bind = "0.0.0.0:8000"`, `workers = 2`, `threads = 4`, `accesslog = "-"`, and `logger_class` = a `gunicorn.glogging.Logger` subclass whose `access()` rewrites `/u/<key>` → `/u/***` and `/s/<key>` → `/s/***` in the logged request line and path, so keys never reach logs.
- `docker-compose.yml`: image `ghcr.io/butthe4d/wish-list:latest`, port `8000:8000`, volume `./data:/data`, env vars with Gmail example comments.
- CI: on push/PR → set up Python, `pip install -e '.[dev]'`, `pybabel compile`, `pytest -q`; on push to `main` and tags `v*` → `docker/build-push-action` to `ghcr.io/${{ github.repository }}` (tags `latest`, sha, semver), `permissions: packages: write`.
- README sections: what it is; quick start (compose); configuration table (all env vars); Gmail app-password SMTP example; gsmservice.pl setup (token, sender name, Polish numbers); exposing via Cloudflare Tunnel / Tailscale Funnel (set `BASE_URL` to the public URL); backup (stop container or use `sqlite3 .backup`, copy `data/wishlist.db`); changing `SECRET_KEY` invalidates sessions and share links; adding a language (`make i18n-update`, copy `.po`, add code to `SUPPORTED_LANGUAGES`); development (venv, `make test`).
- `CLAUDE.md`: commands (`make test`, `make i18n-update`, `make i18n-compile`, alembic revision command), architecture summary (domain modules vs thin views, senders injected), the privacy invariants from Global Constraints, the i18n rule for new strings.

- [ ] **Step 1: Write failing test** `tests/test_csrf.py`: app with CSRF enabled; logged-in member POST `/items/new` without token → 400; with token scraped from the form → 302.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_csrf.py -q` → FAIL.
- [ ] **Step 3: Implement** CSRF, gunicorn key-redacting logger, Docker files, CI, README, CLAUDE.md.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → all pass.
- [ ] **Step 5: Smoke-test the image:**

```bash
docker build -t wishlist:dev .
docker run -d --name wl -p 8000:8000 -e BASE_URL=http://localhost:8000 -e SECRET_KEY=dev \
  -e ADMIN_EMAIL=admin@example.com -v "$PWD/.smoke-data:/data" wishlist:dev
sleep 3; docker logs wl | grep "Admin link: http://localhost:8000/u/"
curl -fsS localhost:8000/healthz   # → ok
LINK=$(docker logs wl | sed -n 's/^Admin link: //p'); curl -s -o /dev/null -w '%{http_code}' "$LINK"   # → 302
docker logs wl | grep -c "/u/[A-Za-z0-9_-]\{20,\}"   # → 1 (only the bootstrap line, access log redacted)
docker images wishlist:dev --format '{{.Size}}'    # expect roughly ≤ 150MB
docker rm -f wl; rm -rf .smoke-data
```

- [ ] **Step 6: Commit** `feat: CSRF, Docker image, CI and documentation`.
