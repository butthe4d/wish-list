# Wish List

A small, self-hosted wishlist app for a household or a circle of friends. Each member keeps a catalog of items and groups them into lists. Each list can be shared by email or SMS link with anyone, member or not. Visitors reserve items so nobody buys the same gift twice, and the list owner never sees which of their items are reserved, so the surprise stays intact.

Features: personal login links (no passwords), email and optional SMS sharing, item pictures fetched from shop links, a colour theme per list (Classic, Christmas, Birthday), English and Polish UI, a single Docker image, SQLite storage.

## Quick start

1. Copy [`docker-compose.yml`](docker-compose.yml) to a new directory and edit `BASE_URL` and `ADMIN_EMAIL`. Create a `.env` file next to it containing `SECRET_KEY=<value>` (generate one as described under [SECRET_KEY](#secret_key); compose refuses to start without it).
2. Start it:

   ```sh
   docker compose up -d
   docker compose logs wishlist | grep "Admin link"
   ```

3. Open the printed `Admin link: ...` in your browser. That link is your login, so keep it private. The first admin is created only when the database is empty.
4. In the admin page, add the other members. Each gets their own personal link, shown on screen and emailed if SMTP is configured.

The database lives in `./data/wishlist.db` and item pictures in `./data/images/`. Migrations run automatically at every start. The container starts as root only to make `/data` writable, then runs the app as uid 1000. With a `user:` override it runs as that user, and `./data` must already be writable by it.

**Upgrading:** back up `data/` first (see [Backup](#backup)). The migration that adds pictures and themes runs automatically on the first start of the new version, and existing lists start with the Classic theme. Going back to an older image after upgrading is not supported.

## Configuration

All configuration comes from environment variables.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BASE_URL` | yes | | Public URL of the app, for example `https://wish.example.com`. Every link the app creates is built from it. |
| `SECRET_KEY` | yes | | At least 32 characters. Signs sessions and derives share links. See below. |
| `DATABASE_URL` | no | `sqlite:////data/wishlist.db` | SQLAlchemy URL. |
| `ADMIN_EMAIL` | no | | Email of the first admin, created on first start when no members exist. |
| `ADMIN_NAME` | no | part of `ADMIN_EMAIL` before `@` | Name of the first admin. |
| `DATA_DIR` | no | `/data` | Folder for item pictures (`DATA_DIR/images`). Keep it on the same volume as the database. The Docker image expects `/data`. |
| `FETCH_DENY_CIDRS` | no | | Extra networks the server must never fetch from. See [Pictures](#pictures-and-fetch-protections). |
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

## Pictures and fetch protections

When a member adds an item with a shop link, the server can look for product pictures on that page and offer up to three to choose from. The member can also paste a direct image URL, or use no picture. The chosen picture is downloaded once, resized (long side at most 800 px), re-encoded as a JPEG with metadata stripped, and stored under `DATA_DIR/images` with a random name. Guests' browsers never contact the shop or any third party: pictures (and fonts) are served by the app itself. A picture is served only while its item is live. Deleting an item removes its picture file, except that an item with a reservation is only soft-deleted: its picture file stays on disk (and is not served) until an admin cancels that reservation or the member is deleted. Candidate pictures that were not chosen are kept in `DATA_DIR/images/tmp`; they are not cleaned on a timer, but the next time anyone searches for pictures, candidates older than an hour are removed.

Because the server makes requests to addresses members type in, it is restricted:

- only `http` and `https`, only ports 80 and 443;
- loopback, private, link-local and other non-public addresses are refused, checked again on every redirect (at most 3);
- pages are limited to 2 MB and images to 10 MB; 8 s per request and 20 s in total, and a picture search stops after 20 s;
- each member can run 30 picture searches per hour (counted per gunicorn worker, so with several workers the effective limit is a multiple).

### FETCH_DENY_CIDRS

Private ranges are blocked automatically, but some of your own addresses look public to the app: the global IPv6 prefix of a LAN with native IPv6 (every device, including your router and other services, has an address in it), and the public IPv4 address or DDNS name of your home connection (with hairpin NAT it reaches your router and the services behind it from inside). A link pointing at them would let a member make the server talk to your internal services. List them in `FETCH_DENY_CIDRS`, comma or space separated (a bare address such as `203.0.113.7` is treated as `/32`):

```yaml
FETCH_DENY_CIDRS: "2001:db8:1234::/48, 203.0.113.7/32"
```

Here `2001:db8:1234::/48` is the LAN's IPv6 prefix and `203.0.113.7` is the household's public IPv4 address. If you use a DDNS name, list the address it currently resolves to. A malformed value stops the app from starting.

## Themes

The owner of a list picks its theme on the list page: Classic, Christmas or Birthday. The theme colours the list for its owner and for guests opening the share link. An admin sets the default theme for new lists and the design on the admin page (currently only "A — tiles"). Fonts (Source Sans 3) are bundled with the app.

## Exposing the app

The app speaks plain HTTP on port 8000 and expects TLS to be handled in front of it. Set `BASE_URL` to the public HTTPS URL, because links in emails and SMS are built from it, never from the request.

- **Cloudflare Tunnel:** point a tunnel's public hostname at `http://localhost:8000` (or `http://wishlist:8000` from another container) and set `BASE_URL` to that hostname.
- **Tailscale Funnel:** run `tailscale funnel 8000` and set `BASE_URL` to the `https://<machine>.<tailnet>.ts.net` URL.

The app trusts one level of `X-Forwarded-For/Proto/Host` headers from the proxy.

## Privacy notes

- **Personal links are passwords.** Anyone holding a member's link is that member. There is no self-service recovery: if someone loses their link, an admin uses "Change key and send new link" on the member.
- **List owners never see reservations on their own items**, in member views and in share views. Note that someone who opens their own share link while logged out, or in another browser, is a visitor like any other and sees the reservation state of that list.
- **Admins** see no reservations in the admin pages until they click "Show reservations" on one member's page; the reveal covers only that member and lasts for that one page load. When a reserved item is deleted, the admin home page shows a neutral "Needs attention" marker next to the member and admins get a neutral email (member name and link only). An admin's own items never trigger the marker or the email.
- Keys are stored only as SHA-256 hashes, and the gunicorn access log replaces keys in `/u/<key>` and `/s/<key>` with `***`.
- **Sharing caps:** each member can send at most 20 SMS and 50 emails per 24 hours.

## Backup

The data is the whole `data` folder: the SQLite database `wishlist.db` and the pictures in `images/`. Stop the container and copy the folder. For a copy while it runs, take a consistent database copy and copy `images/` alongside it:

```sh
sqlite3 data/wishlist.db ".backup 'wishlist-backup.db'"
cp -r data/images wishlist-backup-images
```

Keep the database backup and the images copy together. A database restored without its pictures works, but those items show no picture.

Keep your `SECRET_KEY` with the backup, or share links will have to be sent again.

## Adding a language

1. Add the language code and its display name to `LANGUAGE_NAMES` in `wishlist/config.py` (`SUPPORTED_LANGUAGES` and the language switcher and member form follow from it).
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

Run a local dev server (Flask's built-in server, reachable from other machines on your network):

```sh
make dev-env   # once: writes dev.env (git-ignored) with a generated SECRET_KEY and this machine's IP
make run       # compiles translations, runs migrations/bootstrap, serves on port 8000 (PORT=… to change)
```

The first `make run` prints `Admin link: …`. Data lives in `dev.db` and `devdata/` in the repo folder (both git-ignored).


## Licenses

The app is licensed under the [Apache License 2.0](LICENSE). It vendors these front-end assets:

- [htmx](https://htmx.org) 2.0.4, 0BSD license
- [Source Sans 3](https://github.com/adobe-fonts/source-sans) (weights 300/400/600/900, latin and latin-ext subsets, from [Fontsource](https://fontsource.org) 5), SIL Open Font License 1.1 — see `wishlist/static/fonts/OFL.txt`
