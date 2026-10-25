# Wish List

A small, self-hosted wishlist app for a household or a circle of friends. Each member keeps a catalog of items and groups them into lists. Lists can be shared with other members or sent to outsiders by link. Visitors reserve items so nobody buys the same gift twice, and the list owner never sees which of their items are reserved, so the surprise stays intact.

Features: personal login links (no passwords), email and optional SMS sharing, English and Polish UI, a single Docker image, SQLite storage.

## Quick start

1. Copy [`docker-compose.yml`](docker-compose.yml) to a new directory and edit `BASE_URL` and `ADMIN_EMAIL`. Create a `.env` file next to it containing `SECRET_KEY=<value>` (generate one as described under [SECRET_KEY](#secret_key); compose refuses to start without it).
2. Start it:

   ```sh
   docker compose up -d
   docker compose logs wishlist | grep "Admin link"
   ```

3. Open the printed `Admin link: ...` in your browser. That link is your login, so keep it private. The first admin is created only when the database is empty.
4. In the admin page, add the other members. Each gets their own personal link, shown on screen and emailed if SMTP is configured.

The database lives in `./data/wishlist.db`. Migrations run automatically at every start. The container starts as root only to make `/data` writable, then runs the app as uid 1000. With a `user:` override it runs as that user, and `./data` must already be writable by it.

## Configuration

All configuration comes from environment variables.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BASE_URL` | yes | | Public URL of the app, for example `https://wish.example.com`. Every link the app creates is built from it. |
| `SECRET_KEY` | yes | | At least 32 characters, not a placeholder. Signs sessions and derives share links. See below. |
| `DATABASE_URL` | no | `sqlite:////data/wishlist.db` | SQLAlchemy URL. |
| `ADMIN_EMAIL` | no | | Email of the first admin, created on first start when no members exist. |
| `ADMIN_NAME` | no | part of `ADMIN_EMAIL` before `@` | Name of the first admin. |
| `DEFAULT_LANGUAGE` | no | `en` | `en` or `pl`. Used when neither the member, a cookie nor the browser picks a language. |
| `SMTP_HOST` | no | | Enables email. Without it, links are shown on screen instead of emailed. |
| `SMTP_PORT` | no | `587` | |
| `SMTP_SECURITY` | no | `starttls` | `starttls`, `ssl` or `none`. |
| `SMTP_USER` | no | | |
| `SMTP_PASSWORD` | no | | |
| `SMTP_FROM` | no | `SMTP_USER` | Sender address. |
| `GSMSERVICE_TOKEN` | no | | Enables SMS sharing. Without it, no SMS UI is shown. |
| `GSMSERVICE_SENDER` | no | | Sender name shown on the SMS. |

### SECRET_KEY

Generate a long random value:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Changing `SECRET_KEY` logs everyone out and breaks every share link already sent (share links are derived from the key and are not stored). Personal links keep working, because only their hashes are stored.

### Email with Gmail

Create an [app password](https://myaccount.google.com/apppasswords) (this needs 2-step verification), then set:

```yaml
SMTP_HOST: smtp.gmail.com
SMTP_PORT: "587"
SMTP_SECURITY: starttls
SMTP_USER: you@gmail.com
SMTP_PASSWORD: your-16-char-app-password
SMTP_FROM: you@gmail.com
```

### SMS with gsmservice.pl

1. Create an account at [gsmservice.pl](https://gsmservice.pl) and generate an API token.
2. Register a sender name with the service (or use one it provides).
3. Set `GSMSERVICE_TOKEN` and `GSMSERVICE_SENDER`.

Phone numbers are entered in international format, for example `+48600100200`; spaces and dashes are ignored.

## Exposing the app

The app speaks plain HTTP on port 8000 and expects TLS to be handled in front of it. Set `BASE_URL` to the public HTTPS URL, because links in emails and SMS are built from it, never from the request.

- **Cloudflare Tunnel:** point a tunnel's public hostname at `http://localhost:8000` (or `http://wishlist:8000` from another container) and set `BASE_URL` to that hostname.
- **Tailscale Funnel:** run `tailscale funnel 8000` and set `BASE_URL` to the `https://<machine>.<tailnet>.ts.net` URL.

The app trusts one level of `X-Forwarded-For/Proto/Host` headers from the proxy.

## Privacy notes

- **Personal links are passwords.** Anyone holding a member's link is that member. There is no self-service recovery: if someone loses their link, an admin uses "Change key and send new link" on the member.
- **List owners never see reservations on their own items**, in member views and in share views. Note that someone who opens their own share link while logged out, or in another browser, is a visitor like any other and sees the reservation state of that list.
- **Admins** can reveal reservations of their own items only with the explicit "Show reservations" button. The reveal lasts for that one page load.
- Keys are stored only as SHA-256 hashes, and the gunicorn access log replaces keys in `/u/<key>` and `/s/<key>` with `***`.
- **Sharing caps:** each member can send at most 20 SMS and 50 emails per 24 hours.

## Backup

Everything is in one SQLite file. Either stop the container and copy `data/wishlist.db`, or take a consistent copy while it runs:

```sh
sqlite3 data/wishlist.db ".backup 'wishlist-backup.db'"
```

Keep your `SECRET_KEY` with the backup, or share links will have to be sent again.

## Adding a language

1. Add the language code to `SUPPORTED_LANGUAGES` in `wishlist/config.py` and its display name to `LANGUAGE_NAMES` in `wishlist/i18n.py`.
2. Run `.venv/bin/pybabel init -i wishlist/translations/messages.pot -d wishlist/translations -l <code>` (or `make i18n-update` first to refresh the template).
3. Translate every message in `wishlist/translations/<code>/LC_MESSAGES/messages.po`. The translation test fails on missing or fuzzy entries.
4. `make test`.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
make i18n-compile
make test
```

Run locally with `BASE_URL=http://localhost:8000 SECRET_KEY=dev ADMIN_EMAIL=you@example.com DATABASE_URL=sqlite:///dev.db .venv/bin/python -m wishlist.bootstrap`, then `.venv/bin/flask --app "wishlist:create_app()" run`.

## Licenses

The app is licensed under the [Apache License 2.0](LICENSE). It vendors two front-end assets:

- [htmx](https://htmx.org) 2.0.4, 0BSD license
- [Pico CSS](https://picocss.com) 2.1.1 (classless), MIT license
