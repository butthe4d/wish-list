# Household Wishlist App — Design

Date: 2026-10-24
Status: approved

## Intent

A small, private, self-hosted app for keeping track of household members' gift
wishlists. It runs in a homelab as one small Docker container and lives in a
public GitHub repo, so anyone can run the same setup.

The point is gift coordination. Members write down what they want. Family and
friends get a link and can reserve an item so nobody buys it twice. The owner
doesn't see who reserved what, so the surprise holds.

## Goals

- Admin creates household members (name + email). Each member gets an email with
  a personal link to their own page. No passwords.
- Members manage **items** (name, description, optional store link) and
  **wishlists**. An item can be in any number of wishlists.
- Members share a wishlist by **email or SMS**. Recipients open it without an
  account.
- Anyone with a shared link can **reserve** an item by typing their name.
  Everyone else who opens the list sees it as reserved.
- The admin can see who reserved what.
- All UI text is externalized into per-language files; English and Polish ship
  first.
- Runs as one small container with a single data volume. Easy for strangers to
  self-host from the README.

## Non-goals

- Passwords, OAuth, 2FA. The personal link is the credential, by design.
- Payments, price tracking, scraping store pages, item images.
- Native mobile apps. Pages must work well in a phone browser, nothing more.
- Running a mail server. The app sends through an existing SMTP provider.
- Multi-household or multi-tenant hosting.

## Anti-goals (would count as failure even if everything works)

- A list owner finds out who reserved their gifts by accident.
- Guests have to create an account or install anything to reserve.
- The container or setup becomes heavy enough that homelab users give up
  (e.g., needs Postgres, Redis, a Node build, or a separate frontend).
- SMS becomes required. The app must be fully usable with email alone.

## Constraints

- Hosted on the user's homelab and exposed later via Cloudflare Tunnel or
  Tailscale Funnel. The app must work behind a reverse proxy (correct external
  URLs, trust `X-Forwarded-*`) and must not assume it is on the public internet
  directly.
- Public repo: no secrets in code. All configuration comes from environment
  variables.
- License: Apache-2.0 (already in repo).
- SMS via gsmservice.pl / SzybkiSMS.pl using its official Python SDK,
  `github.com/gsmservice-pl/messaging-sdk-python`. It is a paid service, in
  practice for Polish numbers, so it is optional.

## Approaches considered

1. **Python + Flask, server-rendered HTML (Jinja2) + htmx, SQLite** — **chosen.**
   The user knows some Python and wants something easy to maintain. Python is
   readable for people who fork homelab repos. No JS build toolchain. One SQLite
   file is the whole database, and backup means copying it. Cost: image is about
   60–80 MB and RAM about 50–80 MB, which is fine for a homelab.
2. **Go, same architecture.** Smallest image (~20 MB) and lowest RAM. Rejected
   because the user can't read Go, so every change would depend on an AI.
3. **Java / Spring Boot.** The language the user knows best, but it works
   against the "small container" goal (~200 MB+ image, hundreds of MB of RAM)
   unless they take on native-image complexity.

A separate SPA frontend (React, etc.) was also rejected. It doubles the
tooling for a handful of pages.

## Decided by the user

- **Auth model:** each member has a secret personal link (key). Anyone holding
  it acts as that member. This is intentional.
- **Admin can change a member's key.** The old link stops working and a new one
  is issued.
- **Reservations:** anyone with a shared-list link can reserve an item by
  entering their name. Other viewers see "reserved".
- **Admin visibility** (revised 2026-10-26, user's option B): every member's
  reservations, the admin's own included, stay hidden in the admin view until
  the admin clicks "Show reservations" on that one member's page. Before that
  click the member page shows **no reservation state at all** (not even
  "reserved"). The reveal covers only that member and lasts for that one page
  load. The admin's normal member page never shows them.
- **Email:** no self-hosted SMTP server. Use an existing provider.
- **Stack:** option 1 above.

## Proposals the user accepted

- The **owner never sees reservations** on their own items in the member view.
- **Reservations belong to the item, not the list.** An item reserved through
  one list shows as reserved in every list that contains it.
- **Share links have their own key**, separate from the owner's personal key, so
  a share link can be revoked without locking the owner out.
- A guest can **cancel their own reservation** from the same browser (tracked by
  cookie). Anyone else asks the admin to cancel it.
- Email goes through **generic SMTP settings**, so Gmail with an app password,
  Brevo, SMTP2GO, etc. all work with no provider-specific code. The README
  includes a Gmail example.
- **SMS is optional.** When gsmservice is not configured, SMS options are hidden.

## Design

### Roles

- **Member** — a household person with a personal link. Manages their own items
  and lists and shares lists.
- **Admin** — a member with the admin flag. Also has their own lists. Can manage
  members and see reservations as described above.
- **Guest** — anyone holding a share link. Can view that one list and reserve or
  unreserve items. No account.

### Data model (conceptual)

- **User**: id, name, email, preferred language, is_admin,
  key hash, key version/created-at, created-at.
- **Item**: id, owner (User), name, description (optional), link (optional URL),
  created/updated timestamps.
- **Wishlist**: id, owner (User), name, optional description, share-key hash,
  timestamps.
- **WishlistItem**: many-to-many between Wishlist and Item. A list may only
  contain its owner's items.
- **Reservation**: item (unique, at most one active reservation per item),
  reserver name (free text), guest cookie token (to allow self-cancel), via
  which wishlist (for the admin's information), created-at.

### Keys and links

- Personal and share keys are long random URL-safe tokens (≥ 32 bytes of
  entropy). Only a hash is stored, so the database alone can't be used to log
  in.
- Personal link: `<BASE_URL>/u/<key>`. Opening it starts a session cookie
  (signed, tied to the key version) and redirects to the member's home page, so
  the key doesn't stay in the address bar or proxy logs on later requests.
  Rotating the key invalidates existing sessions.
- Share link: `<BASE_URL>/s/<key>`. Same idea, scoped to one list, read-only
  apart from reserve/unreserve.
- **Owner opening their own share link** (builder's call): if the browser has a
  session for the list's owner, the share URL shows the owner view with no
  reservation state. The app can't stop an owner from opening it in another
  browser; that's an accepted limitation.
- Member sessions are long-lived persistent cookies (about a year, builder's
  call), so members rarely need their link again.
- Since only hashes are stored, links can't be shown again later. "Resend link"
  and "Change key" both generate a new key; the new link is emailed and also
  shown once to the admin, for copying when email isn't set up.

### Main screens

- **Member home**: their wishlists and their items. Add/edit/delete items. Each
  item has checkboxes or a picker for which lists it belongs to.
- **Wishlist page (owner view)**: items in the list, add/remove items, rename,
  share (email / SMS), regenerate share link. No reservation info.
- **Shared list (guest view)**: list name, owner's first name, items with
  name/description/store link, and a reservation state per item: available
  (with "Reserve" + name field), reserved (no name shown), or reserved by you
  (with "Cancel").
- **Admin: members**: list, create (name, email, language,
  admin flag), edit, delete, change key, resend link.
- **Admin: member's lists**: view any member's lists and items. Reserver
  names, "Cancel reservation" and that member's warnings appear only after an
  explicit "Show reservations" on that page, as described above.
- **Language switcher** in the page footer.

### Reserved items that the owner deletes or removes (decided by the user)

The owner can't see reservations, so they may delete an item someone has
already bought, or remove it from the list it was reserved through.

- Deleting an item is a **soft delete**: it disappears for the owner and for
  guests, but if it has an active reservation it stays in the database.
- The owner's member page in admin shows a **warning** for each such item
  ("deleted by <owner>, reserved by <name> via <list>"), with actions to dismiss
  the warning or cancel the reservation, but only once revealed with "Show
  reservations". Unrevealed, the admin home page shows only a neutral "Needs
  attention" marker next to that member, and the member page a neutral notice.
  Deleted items with no reservation can be purged (builder's call).
- The app also **emails the admin** when this happens, if email is configured.
  The email is neutral (revised 2026-10-26): it names the member and links their
  admin page, never the item or the reserver.
- **Exception:** when the deleted item belongs to the admin, there is no email
  and no marker; the warning appears only behind "Show reservations" on their
  own member page (protects the surprise).
- Removing a reserved item from a list keeps the reservation, because it belongs
  to the item. If the item is now in no shared list, the admin gets the same
  warning. A deleted wishlist leaves the reservation's "via" field pointing at
  a name kept for display.
- Editing an item (even renaming it) keeps the reservation.

### Lost links (decided by the user)

No self-service recovery. A member who loses their link asks the admin, who
uses "Change key" to email a new one.

### Sharing

- **Email**: the sharer enters one or more addresses. Each gets a localized
  email with the list's share link.
- **SMS**: the sharer enters a phone number. The app sends a short localized
  SMS with the link via the gsmservice SDK. Only shown when configured.
- The app keeps a simple log of shares sent (list, channel, recipient, time)
  so the owner can see whom they've shared with.

### Localization

- All UI strings, email bodies and SMS texts go through gettext, using
  Flask-Babel with `.po` files under `translations/<lang>/`.
- Ships with `en` and `pl`. Adding a language means adding one `.po` file. The
  README explains how.
- Language choice: a logged-in member uses their saved preference. Guests get
  the browser's `Accept-Language`, falling back to `DEFAULT_LANGUAGE`, and can
  override it with the switcher (stored in a cookie).
- Emails and SMS to a member use the member's language. Share emails/SMS use the
  sharer's language.

### Configuration (environment variables)

- `BASE_URL` (required): the external URL used in links.
- `SECRET_KEY` (required): signs session cookies.
- `ADMIN_NAME`, `ADMIN_EMAIL`: on first start with an empty database, creates
  the first admin and prints their personal link to the container log (so
  setup works even before email is configured).
- `DEFAULT_LANGUAGE` (default `en`).
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
  `SMTP_SECURITY` (`starttls` / `ssl` / `none`). If unset, email is disabled
  and links are only shown on screen / in logs.
- `GSMSERVICE_TOKEN`, `GSMSERVICE_SENDER` (optional): enable SMS.
- Data directory `/data` (volume) holds the SQLite file.

### Packaging

- One Docker image: slim Python base, gunicorn, runs as non-root, exposes one
  port, `/data` volume. Database migrations run automatically on start.
- `docker-compose.yml` example in the repo.
- GitHub Actions: run tests and publish the image to GHCR, so users can
  `docker pull` without building.
- README: what it is, quick start with compose, Gmail SMTP example, gsmservice
  setup, Cloudflare Tunnel / Tailscale Funnel notes, backup (copy the SQLite
  file), adding a language.

## Left to the builder

- Exact library choices within the stack (SQLAlchemy vs. plain sqlite3,
  Alembic vs. simple migrations, Flask-WTF for CSRF, etc.).
- Page layout and styling. Keep it light: a small CSS file or a classless CSS
  framework, mobile-friendly, no build step.
- Exact URL structure beyond the two link formats above.
- Test layout (pytest), lint/format tooling.
- Email/SMS message wording, as long as it is fully localized.
