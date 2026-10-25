# CLAUDE.md

Self-hosted Flask household wishlist app (Apache-2.0). Python 3.12+, SQLAlchemy 2, Alembic, Flask-Babel, Flask-WTF, htmx + Pico CSS (vendored, no build step), gunicorn.

## Commands

- `make test` - run pytest (`.venv/bin/pytest -q`)
- `make i18n-update` - extract strings and update the `.po` files
- `make i18n-compile` - compile `.mo` files (gitignored; tests and the Docker build compile them)
- New migration: `.venv/bin/alembic revision -m "msg"` (scripts in `wishlist/migrations`, applied by `upgrade_db`)
- Bootstrap / first admin: `python -m wishlist.bootstrap` (runs migrations, prints `Admin link: ...`)
- Image: `docker build -t wishlist:dev .`; entrypoint runs bootstrap, then gunicorn with `wishlist/gunicorn_conf.py`

## Architecture

- `wishlist/__init__.py` `create_app(config=None, *, mailer=None, sms=None)`: config from env (`config.py`), CSRFProtect, ProxyFix, blueprints.
- Domain modules hold the logic and rules: `catalog`, `reservations`, `sharing`, `members`, `notify`, `keys`. Views in `wishlist/views/` (`member`, `admin`, `shared`) and `auth.py`/`i18n.py` stay thin.
- Senders (`mailer`, `sms`) are injected into `create_app` or built from config; UI and routes key off sender presence (`get_mailer()`/`get_sms()` not None).
- Mutating domain functions commit their own transaction; views do not commit. `ValidationError(field, message)` is in `errors.py`; `reservations` must not import `catalog`.
- Personal keys: `secrets.token_urlsafe(32)`, only SHA-256 hex stored; link `<BASE_URL>/u/<key>`. Share keys are derived (`f"{wishlist_id}.{hmac}"`, HMAC-SHA256 of `share:{id}:{share_version}` with `SECRET_KEY`), link `<BASE_URL>/s/<key>`; regenerate by incrementing `share_version`.

## Invariants (do not break)

- A list owner never sees reservation state of their own items, in any member or share view. Admins see it only on a page loaded with `?reveal=1` from the "Show reservations" button.
- External links are built from `BASE_URL`, never the request host.
- `Referrer-Policy: no-referrer` on all responses; store links use `rel="noopener noreferrer" target="_blank"`.
- Keys never reach logs (gunicorn logger redacts `/u/<key>`, `/s/<key>`).
- Every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- SMS and email are optional; absent sender means no UI and 404 on SMS routes.

## i18n rule

Every user-visible string (templates, flashes, errors, emails, SMS) goes through `_()`/gettext. After adding strings run `make i18n-update`, fill the Polish `msgstr` in `wishlist/translations/pl/LC_MESSAGES/messages.po` (no fuzzy entries); `tests/test_translations.py` enforces completeness.

## Testing notes

- `tests/conftest.py`: `app` has injected `RecordingMailer`/`RecordingSms` (`tests/fakes.py`) and `WTF_CSRF_ENABLED=False`; CSRF is covered in `tests/test_csrf.py` with its own app.
- The `db` fixture is a separate session (`expire_on_commit=False`), like another process, so a domain function that forgets to commit fails the test.
- Tests that call catalog functions directly (outside a request) use the `catalog_mailer` fixture; `notify.DISABLED` builds an app with a channel off (`create_app(cfg, sms=DISABLED)`).
- Helpers: `make_user`, `login`, fixtures `admin_client`, `item_in_list`.
