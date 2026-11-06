# UI Redesign, Item Pictures and Themes — Design

Date: 2026-11-06
Status: approved
Builds on: `2026-10-24-wishlist-app-design.md` (all its privacy rules still hold)

## Intent

The app works, but every page uses stock Pico CSS and reads like a form ("Google
Forms, really boring" — the user). Christmas 2026 is the first real test with
the family, so the pages people actually see should feel like browsing a little
gift shop, not filling in a questionnaire. The user likes the HTML5 UP template
style (bold headings, generous space, picture-tile grids).

Mockups agreed on: https://claude.ai/artifact/RYohYT36Q8y6t1rCRv3CDM, third row
("HTML5 UP-style directions"). Direction **A (Phantom-style tiles)** is the one
to build now.

## Goals

- **Design A** for every non-admin page: a light page, a heavy display title,
  uppercase letter-spaced labels, and gifts as a grid of picture tiles.
- **Item pictures fetched from the shop link**: when an item has a shop link,
  the app finds up to 3 candidate pictures on that page and the user picks one;
  the user can instead paste a direct image URL, or have no picture.
- **Tap a gift to see more**: tapping a tile opens a detail panel (big picture,
  description, shop link). Reserving happens inside that panel, never directly
  on the tile.
- **Color themes per list**: the list owner picks the list's theme
  (Klasyczny/Classic, Święta/Christmas, Urodziny/Birthday) when creating or
  editing a list. The guest page and the owner's list page use it.
- **Admin defaults**: the admin picks the app-wide design (only A for now) and
  the default color theme. The default applies to new lists and to pages that
  belong to no list (member home, item form, welcome, errors).
- **Built for more designs later**: designs B (Forty) and C (Dimension) from the
  mockups must be addable later as pure styling (one stylesheet each) on the
  same page markup. Not built now.

## Non-goals

- Designs B and C (a follow-up project; this one only keeps the door open).
- Redesigning admin pages. They pick up the new base styles and stay
  functional, nothing more.
- Animations (snow, confetti motion). Decorations are static.
- Image uploads from the user's device. Pictures come from a shop page or a
  pasted image URL only.
- A JavaScript build step or a CSS framework that needs one.

## Anti-goals

- Anything that weakens the privacy rules: the owner still never sees
  reservation state; reservations are still only made by guests via the share
  link.
- Guests' browsers contacting shop servers just by viewing a list (privacy, and
  shop images break or change over time) — pictures are served by the app.
- The server fetching arbitrary URLs inside the homelab network on a user's
  behalf (see Picture fetching: safety).
- Pages that only work on a big screen. Phones first; most guests open the link
  on a phone.

## Constraints

- Still Flask + Jinja, server-rendered, htmx (`hx-boost`), no JS build. Small
  vanilla JS files in `static/` are fine (like `copy.js`).
- Fonts: Source Sans 3 (as in the mockups), self-hosted in `static/` (no Google
  Fonts request from guests' browsers; same privacy reasoning as pictures). OFL
  license, noted in README.
- Everything localized (en + pl), CSRF on every POST form, `novalidate` on every
  form, as today.
- Works over plain HTTP (homelab before the tunnel exists).
- Pictures live in the `/data` volume so backups keep working (copy the folder).

## Approaches considered

1. **One page structure, one stylesheet per design, color themes as CSS
   variables — chosen, building only design A now ("3, then 1").** Each design
   is a stylesheet over identical markup; each color theme is a set of CSS
   custom properties. 3 designs × 3 themes from one set of templates. Cost:
   designs can't diverge structurally, and every new screen must later be
   checked in each design.
2. **Separate templates per design.** Unlimited freedom, but triple the work for
   every screen and change; designs drift. Rejected.
3. Same as 1 but build all three designs now. Rejected for now: Christmas is the
   first real test; one well-tested design beats three half-tested ones.

## Design

### Visual system (design A)

- Fonts: Source Sans 3 — 900 for titles and uppercase labels
  (letter-spacing ~0.15–0.3em), 300/400 for body text.
- Layout: white page, 24 px side gutter on phones, a max-width container
  (~960 px) on larger screens; the gift grid is 2 columns on phones, 3–4 on
  wider screens.
- Gift tile: square picture (object-fit cover) or, without a picture, a solid
  theme-colored block with a generic gift icon; below it the name in uppercase
  and a one-line status.
- Tile states on the guest page: available (plain), reserved by someone else
  (dimmed with a diagonal "ZAREZERWOWANE / RESERVED" ribbon), reserved by me
  (check badge, "Twoja rezerwacja / Your reservation" in the accent color).
- Buttons: flat, uppercase, letter-spaced; primary = theme accent fill.
  Inputs: 2 px dark border, square corners. Touch targets ≥ 44 px.
- Header of each page: small logo mark + "LISTA ŻYCZEŃ" label, then the big
  title.

### Color themes

Each theme defines the same CSS variables (accent, accent text, tile palette of
4 colors cycled for picture-less tiles, ribbon color, page background,
decoration). Starting set, from the mockups:

- **Klasyczny (classic)** — teal accent (#0F766E family), neutral tiles.
- **Święta (christmas)** — red accent (#B4232A family), tiles in red, pine
  green, gold, frost blue; a subtle static snowflake/tree decoration in the
  header.
- **Urodziny (birthday)** — magenta accent (#BE185D family), bright tiles,
  static confetti decoration.

Colors must keep 4.5:1 text contrast. A theme is chosen by a `data-theme`
attribute on `<html>`; the design by a stylesheet link. Adding a theme = one
CSS block + one name in a registry list.

### Where themes come from

- `Wishlist.theme` (new column): one of the theme keys; set on create (default:
  admin default theme) and editable by the owner on the list page.
- App settings (new, admin-editable): `design` (only `"a"` selectable now) and
  `default_theme`.
- Guest shared page and owner list page: the list's theme. Every other page:
  the admin default theme. The item detail panel uses the theme of the page it
  opens on.

### Guest page (shared list)

- Header: theme decoration, "LISTA ŻYCZEŃ", list name as the big title, one
  line "Prezenty, o których marzy Ania. Dotknij prezentu, żeby zobaczyć więcej —
  Ania nie zobaczy, kto co wybrał." (en equivalent).
- Grid of gift tiles (states above). Tile = a link to the item detail.
- **Item detail panel**: opens as a bottom sheet on phones / centered dialog on
  wider screens (a native `<dialog>` element), and also works as its own page
  without JavaScript (the tile link points to a real URL; htmx/JS enhances it
  into the panel). Contents: big picture, name, description, "Zobacz w
  sklepie / See in shop" link (opens in a new tab, `noopener noreferrer`), and
  the state-dependent action:
  - available → "Kto kupuje ten prezent? / Who's buying this?" name field +
    "REZERWUJĘ / I'LL GET IT" button + "Ania nie zobaczy, że to Ty."
  - reserved by me → "Twoja rezerwacja" + "Anuluj rezerwację / Cancel".
  - reserved by someone else → "Ktoś już to kupuje / Someone is already
    getting this" — no action.
- After reserve/cancel, the guest returns to the grid with the tile updated.

### Member pages

- **Home**: the member's lists as wide theme-colored cards (name, item count,
  theme) and their items as the same tile grid (no reservation info, as
  always); a clear "Nowa lista / New list" and "Dodaj prezent / Add gift"
  action.
- **List page (owner)**: header in the list's theme, theme picker (three
  swatch cards, radio inputs), the share section (link + copy, email, SMS), the
  list's items as tiles, add/remove items. No reservation info.
- **Item form (add/edit)**: shop link field first; when a link is entered (on
  blur / "Szukaj zdjęć / Find pictures" button), the app fetches candidates and
  shows up to 3 as selectable thumbnails; below them "Albo wklej bezpośredni
  link do zdjęcia / Or paste a direct image link"; an option for no picture.
  Then name, description, and the list checkboxes. Works without JavaScript
  (a plain "Find pictures" submit that re-renders the form with candidates).
- Editing an item shows its current picture with "Zmień / Change".

### Picture fetching

- Triggered only by a member on the item form (never by guests).
- Fetch the shop page (HTML only, size cap ~2 MB, timeout ~8 s, max 3
  redirects, a normal browser-like User-Agent), then collect candidate image
  URLs in this order: `og:image`, `twitter:image`, JSON-LD `Product.image`,
  `link[rel=image_src]`, then large-looking `<img>` sources (skip sprites,
  icons, tracking pixels, SVG). Resolve relative URLs; de-duplicate; keep the
  first 3 that actually download as real images.
- Shops that block the fetch or have no usable pictures: show "Nie udało się
  znaleźć zdjęć / Couldn't find pictures" and leave the manual image-URL field
  and "no picture" option.
- On save, the chosen image (candidate or pasted URL) is downloaded, verified
  to be an image (Pillow), resized to max ~800 px on the long side, re-encoded
  (strips metadata; JPEG or WebP), and stored under `/data/images/` with a
  random file name. The item keeps only that file name. Replacing or removing
  the picture deletes the old file; deleting an item deletes its file (a
  soft-deleted item keeps it until purged).
- Candidates shown on the form before saving are fetched into a short-lived
  temp area (or proxied through the app) so the browser never loads them from
  the shop directly; temp files older than ~1 hour are cleaned up.
- Images are served by the app at a URL with the random name; guests can see
  pictures of items on lists shared with them, and nothing else (a random name
  is unguessable; the route serves only files that belong to a live item).

### Picture fetching: safety

The server fetches URLs that a member types, from inside the homelab. To avoid
it being used to reach internal services:

- Only `http`/`https`, ports 80/443 (others rejected).
- Resolve the host and refuse private, loopback, link-local, multicast and
  reserved addresses (IPv4 and IPv6), and re-check on every redirect hop; pin
  the connection to the checked address.
- Size caps (page ~2 MB, image ~10 MB), timeouts, and a cap on decoded image
  pixels (Pillow decompression-bomb limit).
- Fetching is rate-limited per member (e.g. 30 page fetches per hour).

### Admin

- A small "Ustawienia / Settings" section on the admin home: design (select
  with one option, "A — kafelki", for now) and default theme (select). Admin
  pages themselves keep their current layout with the new base styles.

### Data changes

- `Item.image` (nullable, file name), `Wishlist.theme` (string, not null,
  default `"classic"` for existing rows), and a settings store (single-row
  table or key/value) for `design` and `default_theme`. One Alembic migration
  `0002`.
- The user already has a real database, so the migration must upgrade it in
  place.

## Decided by the user

- Theming: a new look for the whole app; per-list color theme chosen by the
  list owner; admin sets the defaults.
- Not Material Design; first-row mockups felt like Google Forms; aim for the
  HTML5 UP style.
- Pictures fetched from the shop: show up to 3 candidates to pick from, with a
  manual direct-image-URL override.
- Tapping a gift shows more; reserving happens from the detail view.
- Build design A now, structured so B and C are added later as styling only.

## Proposals (builder's calls, flagged for review)

- Pictures are downloaded and served by the app, not hotlinked (privacy for
  guests, links don't rot).
- Fonts self-hosted for the same reason.
- SSRF protections and fetch limits as listed.
- Picture-less items get a theme-colored tile with a generic gift icon.
- Admin pages are not redesigned.

## Left to the builder

- Exact CSS values within the visual system, breakpoints, icon set (inline
  stroke SVGs as in the mockups).
- HTML parsing library for candidates (stdlib `html.parser` preferred over a
  new dependency; Pillow is the one new dependency).
- How the detail panel is enhanced (small vanilla JS + `<dialog>`, or htmx
  `hx-get` into a dialog), as long as it degrades to a normal page.
- Exact tile palettes per theme, as long as contrast holds.
