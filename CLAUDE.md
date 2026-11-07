# CLAUDE.md

Self-hosted Flask household wishlist app (Apache-2.0). Python 3.12+, SQLAlchemy 2, Alembic, Flask-Babel, Flask-WTF, htmx + hand-written CSS (`design-a.css` + `themes.css`) + Source Sans 3 self-hosted (all vendored, no build step), Pillow, gunicorn.

## Commands

- `make test` - run pytest (`.venv/bin/pytest -q`)
- `make i18n-update` - extract strings and update the `.po` files
- `make i18n-compile` - compile `.mo` files (gitignored; tests and the Docker build compile them)
- New migration: `.venv/bin/alembic revision -m "msg"` (scripts in `wishlist/migrations`, applied by `upgrade_db`)
- Bootstrap / first admin: `python -m wishlist.bootstrap` (runs migrations, prints `Admin link: ...`)
- Image: `docker build -t wishlist:dev .`; entrypoint runs bootstrap, then gunicorn with `wishlist/gunicorn_conf.py`

## Architecture

- `wishlist/__init__.py` `create_app(config=None, *, mailer=None, sms=None)`: config from env (`config.py`), CSRFProtect, ProxyFix, blueprints.
- Domain modules hold the logic and rules: `catalog`, `reservations`, `sharing`, `members`, `notify`, `keys`, `themes` (themes, designs, app settings), `fetch` (SSRF-safe `safe_fetch`), `pictures` (find/process candidates, `allow_fetch` rate limit), `images` (files under `DATA_DIR/images`, temp candidates, cleanup). Views in `wishlist/views/` (`member`, `admin`, `shared`, `images` serving) and `auth.py`/`i18n.py` stay thin.
- Senders (`mailer`, `sms`) are injected into `create_app` or built from config; UI and routes key off sender presence (`get_mailer()`/`get_sms()` not None).
- Mutating domain functions commit their own transaction; views do not commit. `ValidationError(field, message)` is in `errors.py`; `reservations` must not import `catalog`.
- Personal keys: `secrets.token_urlsafe(32)`, only SHA-256 hex stored; link `<BASE_URL>/u/<key>`. Share keys are derived (`f"{wishlist_id}.{hmac}"`, HMAC-SHA256 of `share:{id}:{share_version}` with `SECRET_KEY`), link `<BASE_URL>/s/<key>`; regenerate by incrementing `share_version`.
- Designs differ only by stylesheet: one markup, selected via `data-design` on `<html>`; add a design by adding CSS, not templates. The theme (`classic`/`christmas`/`birthday`) is set per list; `data-theme` goes on the `.page` wrapper in `base.html` (not only `<html>`), so htmx-swapped content keeps the right theme.
- `base.html` has the `htmx-config` meta with `responseHandling` so 4xx/5xx responses (validation errors) are swapped in; 204 does not swap.

## Invariants (do not break)

- A list owner never sees reservation state of their own items, in any member or share view. In admin, every member's reservations (and orphan warnings) appear only on that member's page loaded with `?reveal=1` from the "Show reservations" button; the admin home page shows only a neutral "Needs attention" marker (never for the viewer's own items), and orphan emails name only the member.
- External links are built from `BASE_URL`, never the request host.
- `Referrer-Policy: no-referrer` on all responses; store links use `rel="noopener noreferrer" target="_blank"`.
- Keys never reach logs (gunicorn logger redacts `/u/<key>`, `/s/<key>`).
- Every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Pictures: never hotlinked (guests' browsers load only app-served files); stored as re-encoded JPEG with random names; served only for live items (deleted items and orphan files 404); on hard deletes (item, member) files are removed after the DB commit; an item with a reservation is only soft-deleted, so its file stays (unserved) until the reservation is cancelled or the member is deleted; temp candidates are removed by `cleanup_temp` on the next picture search once older than 1 h (no timer).
- All outbound fetches go through `fetch.safe_fetch` (http/https, ports 80/443, public addresses re-checked on every redirect, `FETCH_DENY_CIDRS`, size and time caps); never call `requests`/`urllib` directly for user-supplied URLs.
- SMS and email are optional; absent sender means no UI and 404 on SMS routes.

## i18n rule

Every user-visible string (templates, flashes, errors, emails, SMS) goes through `_()`/gettext. After adding strings run `make i18n-update`, fill the Polish `msgstr` in `wishlist/translations/pl/LC_MESSAGES/messages.po` (no fuzzy entries); `tests/test_translations.py` enforces completeness.

## Testing notes

- `tests/conftest.py`: `app` has injected `RecordingMailer`/`RecordingSms` (`tests/fakes.py`) and `WTF_CSRF_ENABLED=False`; CSRF is covered in `tests/test_csrf.py` with its own app.
- The `db` fixture is a separate session (`expire_on_commit=False`), like another process, so a domain function that forgets to commit fails the test.
- Tests that call catalog functions directly (outside a request) use the `catalog_mailer` fixture; `notify.DISABLED` builds an app with a channel off (`create_app(cfg, sms=DISABLED)`).
- Fetch tests use the `local_server` fixture (a loopback HTTP server; it also allows its port) plus `allow_loopback` (treats 127.0.0.1 as public); monkeypatch the fetch budgets (`fetch.TIMEOUT`/`TOTAL_TIMEOUT`, `pictures.FETCH_LIMIT`, `pictures.time`) instead of waiting. View tests monkeypatch `pictures.inspect_page` (returns `PageInfo(title, description, pictures)`).
- Helpers: `make_user`, `login`, fixtures `admin_client`, `item_in_list`.
