# UI Redesign, Item Pictures and Themes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the app the HTML5 UP-style "design A" (picture-tile grids, bold type), per-list color themes with admin defaults, and item pictures fetched safely from shop links.

**Architecture:** Same Flask/Jinja app. New domain modules: `themes.py` (theme registry + app settings), `fetch.py` (SSRF-safe HTTP fetch), `pictures.py` (candidate extraction + image processing), `images.py` (file storage under `DATA_DIR/images`). Pico CSS is replaced by one design stylesheet (`design-a.css`) plus `themes.css` (CSS variables per `data-theme`); templates share one markup so designs B/C can later be added as stylesheets only.

**Tech Stack:** existing stack + Pillow ≥ 11 (only new dependency); self-hosted Source Sans 3 woff2 (OFL); stdlib `html.parser`, `http.client`, `ssl`, `ipaddress`.

**Spec:** `docs/superpowers/specs/2026-11-06-ui-redesign-design.md` (builds on `docs/superpowers/specs/2026-10-24-wishlist-app-design.md`)

## Global Constraints

- All privacy invariants in `CLAUDE.md` stay: owners never see reservation state of their own items; admin sees reservations only on a `?reveal=1` member page; keys never in logs; `Referrer-Policy: no-referrer`; CSRF token and `novalidate` on every POST form; external links built from `BASE_URL`.
- Every user-visible string via gettext; `make i18n-update`; Polish `msgstr` filled (no fuzzy, no stale continuation lines); `tests/test_translations.py` passes.
- No JS build step. Small vanilla JS in `static/` allowed; every page must work without JS.
- Theme keys exactly: `classic`, `christmas`, `birthday`. Display names: en "Classic"/"Christmas"/"Birthday", pl "Klasyczny"/"Święta"/"Urodziny". Design keys: only `a` ("A — tiles" / "A — kafelki").
- Fonts self-hosted (no request to Google from guests' browsers). Pictures served by the app, never hotlinked.
- Pictures stored under `DATA_DIR/images` (new env `DATA_DIR`, default `/data`); resized to max 800 px long side, re-encoded JPEG (quality 85, metadata stripped), random file names `secrets.token_urlsafe(16) + ".jpg"`.
- Fetch limits: http/https only, ports 80/443 only, refuse non-public addresses (every redirect hop), max 3 redirects, page ≤ 2 MB, image ≤ 10 MB, timeout 8 s per request, `Image.MAX_IMAGE_PIXELS = 40_000_000`, 30 page fetches per member per hour.
- Touch targets ≥ 44 px; text contrast ≥ 4.5:1; layouts work at 360 px width.
- Copy on the guest page (exact, pl / en): hint "Prezenty, o których marzy %(name)s. Dotknij prezentu, żeby zobaczyć więcej — %(name)s nie zobaczy, kto co wybrał." / "Gifts %(name)s is wishing for. Tap a gift to see more — %(name)s won't see who picked what."; reserve label "Kto kupuje ten prezent?" / "Who's buying this?"; button "REZERWUJĘ" / "I'LL GET IT"; ribbon "ZAREZERWOWANE" / "RESERVED"; mine "Twoja rezerwacja" / "Your reservation"; taken "Ktoś już to kupuje" / "Someone is already getting this"; shop link "Zobacz w sklepie" / "See in shop".

## Review Focus

1. **Hostile or broken shop pages** (slow, huge, not HTML, corrupt images, decompression bombs, 0 candidates) → the form shows "Couldn't find pictures" and the manual field; no 500, no hang beyond the timeout. Tests in Tasks 2–3.
2. **Links into the homelab** (`http://192.168.1.1/`, `http://localhost:8000/`, a hostname resolving to `10.x`, `[::1]`, a public page redirecting to a private address, port 8080) → refused with a friendly error. Tests in Task 2.
3. **Picture lifecycle**: editing an item without touching the picture keeps it; replacing or removing deletes the old file; deleting an item, purging a soft-deleted item, or deleting a member removes files (no orphans piling up in `/data`). Tests in Task 4.
4. **Picture URL access**: a random/guessed name, a picture of a soft-deleted item, or a path like `../wishlist.db` → 404. Tests in Task 4.
5. **Existing database upgrade**: a DB at revision 0001 with members, lists, items and reservations upgrades to 0002 with data intact and every list on `classic`. Test in Task 1.

---

## File Structure

```
wishlist/themes.py            THEMES registry, DESIGNS, AppSettings access (get/update)
wishlist/fetch.py             safe_fetch(), FetchError, is_public_address()
wishlist/pictures.py          extract_candidates(), process_image(), find_pictures(), PictureError
wishlist/images.py            store/delete/serve paths, temp area, cleanup
wishlist/views/images.py      GET /images/<name>, GET /images/tmp/<token> (member)
wishlist/models.py            + Item.image, Wishlist.theme, AppSettings
wishlist/migrations/versions/0002_pictures_themes.py
wishlist/static/design-a.css  base + components (replaces pico)
wishlist/static/themes.css    [data-theme=…] variables
wishlist/static/fonts/        Source Sans 3 woff2 (300, 400, 600, 900; latin + latin-ext)
wishlist/static/dialog.js     enhances tile links into a <dialog>
wishlist/templates/_macros.html  tile(), icons, theme_picker()
templates: base, home, item_form, list_owner, shared_list, shared_item (new), admin/index (settings section)
```

---

### Task 1: Data model, migration 0002, theme registry and app settings

**Files:**
- Create: `wishlist/themes.py`, `wishlist/migrations/versions/0002_pictures_themes.py`, `tests/test_themes.py`, `tests/test_migration_0002.py`
- Modify: `wishlist/models.py`, `wishlist/config.py` (+ `data_dir`), `wishlist/catalog.py` (`create_wishlist`, `update_wishlist` take `theme`), `pyproject.toml` (+ `Pillow>=11`), `tests/conftest.py` (`data_dir=tmp_path`)

**Interfaces:**
- Produces:
  - `Item.image: Mapped[str | None]` (String(64)); `Wishlist.theme: Mapped[str]` (String(20), `server_default="classic"`); `class AppSettings(Base)` table `app_settings`: `id` (always 1), `design: str` (default `"a"`), `default_theme: str` (default `"classic"`).
  - `Config.data_dir: str = "/data"` (env `DATA_DIR`); property `images_dir -> str` = `f"{data_dir}/images"`.
  - `themes.THEMES: dict[str, LazyString]` (ordered `classic`, `christmas`, `birthday`; names via `lazy_gettext`); `themes.DESIGNS: dict[str, LazyString]` = `{"a": "A — tiles"}`.
  - `themes.get_settings(s) -> AppSettings` (creates the row on first call, commits); `themes.update_settings(s, *, design: str, default_theme: str) -> AppSettings` (unknown key → `ValidationError("design"|"default_theme", …)`; commits).
  - `themes.validate_theme(key: str) -> str` (raises `ValidationError("theme", …)`).
  - `catalog.create_wishlist(s, owner, *, name, description, theme: str | None = None)` — `None` → `get_settings(s).default_theme`; `catalog.update_wishlist(s, wl, *, name, description, theme: str)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_themes.py
def test_settings_defaults_and_update(db):
    st = get_settings(db)
    assert (st.design, st.default_theme) == ("a", "classic")
    update_settings(db, design="a", default_theme="christmas")
    assert get_settings(db).default_theme == "christmas"

@pytest.mark.parametrize("field,kw", [("design", {"design": "b", "default_theme": "classic"}),
                                      ("default_theme", {"design": "a", "default_theme": "neon"})])
def test_settings_reject_unknown(db, field, kw):
    with pytest.raises(ValidationError) as e: update_settings(db, **kw)
    assert e.value.field == field

def test_new_list_uses_admin_default_theme(db, user):
    update_settings(db, design="a", default_theme="birthday")
    assert create_wishlist(db, user, name="B-day", description=None).theme == "birthday"

def test_update_wishlist_theme_validated(db, user):
    wl = create_wishlist(db, user, name="X", description=None)
    with pytest.raises(ValidationError): update_wishlist(db, wl, name="X", description=None, theme="neon")
    update_wishlist(db, wl, name="X", description=None, theme="christmas")
    # + fresh-session persistence check, as in tests/test_catalog_lists.py
```

`tests/test_migration_0002.py::test_upgrade_keeps_data`: `alembic` upgrade a temp DB to `0001` only, insert a user, a list, an item in it and a reservation with raw SQL, upgrade to head, then assert the rows are intact, `wishlist.theme == "classic"`, `item.image IS NULL`, and `app_settings` is usable via `get_settings`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_themes.py tests/test_migration_0002.py -q` → FAIL (import errors).
- [ ] **Step 3: Implement** models, config, `themes.py`, catalog signature changes (update existing callers/tests; `list_edit` view passes the list's current theme for now), migration `0002` (autogenerate, then make `wishlist.theme` NOT NULL with `server_default='classic'` via batch op; verify `compare_metadata` shows no diff), add Pillow to deps and `pip install -e '.[dev]'`.
- [ ] **Step 4: Run** full suite → all pass.
- [ ] **Step 5: Commit** `feat: item picture column, list themes and app settings (migration 0002)`.

---

### Task 2: SSRF-safe fetcher

**Files:**
- Create: `wishlist/fetch.py`, `tests/test_fetch.py`

**Interfaces:**
- Produces:
  - `class FetchError(Exception)` (message safe to show is NOT required; views map it to a generic localized message).
  - `is_public_address(ip: ipaddress.IPv4Address | IPv6Address) -> bool`: false for private, loopback, link-local, multicast, reserved, unspecified, shared (100.64/10), IPv4-mapped/6to4/NAT64 forms of those.
  - `@dataclass FetchResult(content: bytes, content_type: str, final_url: str)`.
  - `safe_fetch(url: str, *, max_bytes: int, accept: str) -> FetchResult`: validates scheme/port; resolves with `socket.getaddrinfo`; refuses if ANY resolved address is non-public; connects to the checked IP (pinned) with `http.client.HTTPConnection`/`HTTPSConnection` (for HTTPS: SNI + certificate verification against the hostname via `ssl.create_default_context()` and a connection subclass whose `connect()` wraps a socket to the pinned IP with `server_hostname=host`); sends `Host`, `User-Agent: Mozilla/5.0 (compatible; WishlistBot/1.0)`, `Accept: <accept>`; follows ≤ 3 redirects (301/302/303/307/308), re-validating each hop; reads at most `max_bytes + 1` and raises if exceeded; timeout 8 s; non-2xx → FetchError.
  - Constants `PAGE_MAX_BYTES = 2_000_000`, `IMAGE_MAX_BYTES = 10_000_000`, `TIMEOUT = 8`, `MAX_REDIRECTS = 3`.

- [ ] **Step 1: Write failing tests** (a local `http.server` in a thread on 127.0.0.1; tests that need to reach it monkeypatch `wishlist.fetch.is_public_address` to allow loopback):

```python
@pytest.mark.parametrize("ip,ok", [("8.8.8.8", True), ("10.0.0.1", False), ("192.168.1.1", False),
    ("127.0.0.1", False), ("169.254.169.254", False), ("100.64.0.1", False), ("::1", False),
    ("fe80::1", False), ("::ffff:192.168.1.1", False), ("2001:4860:4860::8888", True)])
def test_is_public_address(ip, ok): assert is_public_address(ipaddress.ip_address(ip)) is ok

@pytest.mark.parametrize("url", ["ftp://example.com/x", "http://example.com:8080/", "file:///etc/passwd",
                                 "http://localhost:8000/", "http://[::1]/"])
def test_refuses_bad_urls(url):
    with pytest.raises(FetchError): safe_fetch(url, max_bytes=1000, accept="*/*")

def test_refuses_hostname_resolving_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("10.1.2.3", 80))])
    with pytest.raises(FetchError): safe_fetch("http://shop.example/", max_bytes=1000, accept="*/*")

def test_follows_redirect_and_refuses_redirect_to_private(local_server, allow_loopback_once): ...
    # server: /a → 302 /b (ok, returns body, final_url endswith "/b");
    # /evil → 302 http://10.0.0.5/ → FetchError (only the first hop is allowed by the fixture)
def test_too_many_redirects(local_server, allow_loopback): ...   # 4-hop chain → FetchError
def test_size_cap(local_server, allow_loopback): ...              # 3_000 bytes with max_bytes=1000 → FetchError
def test_timeout(local_server, allow_loopback, monkeypatch): ...  # handler sleeps; TIMEOUT patched to 0.5 → FetchError
def test_non_2xx(local_server, allow_loopback): ...                # 404 → FetchError
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_fetch.py -q` → FAIL.
- [ ] **Step 3: Implement** `wishlist/fetch.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: SSRF-safe fetcher for shop pages and images`.

---

### Task 3: Picture candidates, processing and image storage

**Files:**
- Create: `wishlist/pictures.py`, `wishlist/images.py`, `tests/test_pictures.py`, `tests/test_images_store.py`

**Interfaces:**
- Consumes: `safe_fetch`, `FetchError`, `PAGE_MAX_BYTES`, `IMAGE_MAX_BYTES` (Task 2); `Config.images_dir` (Task 1).
- Produces:
  - `class PictureError(Exception)`.
  - `pictures.extract_candidates(html: str, base_url: str) -> list[str]`: absolute http(s) URLs in this order: `meta[property=og:image]` (and `og:image:secure_url`), `meta[name=twitter:image]`, JSON-LD `Product.image` (string, list, or `{url}`; also inside `@graph`), `link[rel=image_src]`, then `<img>` `src`/`data-src`/first `srcset` URL, skipping `.svg`, `data:` URIs, and URLs containing `sprite`, `logo`, `icon`, `pixel`, `spacer`, and `<img>` with `width`/`height` attributes < 100. De-duplicated, order kept, at most 12. Uses `html.parser.HTMLParser` (no new dependency).
  - `pictures.process_image(data: bytes) -> bytes`: Pillow open + verify + reopen, refuse if pixel count > `Image.MAX_IMAGE_PIXELS` (set to 40_000_000 at module import) or not an image → `PictureError`; `ImageOps.exif_transpose`; convert to RGB (flatten alpha on white); `thumbnail((800, 800))`; save JPEG quality 85, no EXIF.
  - `pictures.download_image(url: str) -> bytes` = `process_image(safe_fetch(url, max_bytes=IMAGE_MAX_BYTES, accept="image/*").content)`; FetchError → PictureError.
  - `pictures.find_pictures(page_url: str, *, limit: int = 3) -> list[bytes]`: fetch page (`accept="text/html"`, content type must contain `html`), extract, download candidates in order until `limit` succeed (skip failures, try at most 8); FetchError on the page → PictureError.
  - `images.store(data: bytes) -> str` (writes `images_dir/<name>`, returns name); `images.delete(name: str | None) -> None` (no-op on None/missing); `images.path_for(name: str) -> str | None` (None unless `name` matches `^[A-Za-z0-9_-]{22}\.jpg$` and the file exists); temp area `images_dir/tmp`: `images.store_temp(data) -> str` (token, same name format), `images.temp_path(token) -> str | None`, `images.promote_temp(token) -> str` (moves into images dir; missing → `PictureError`), `images.cleanup_temp(max_age_seconds: int = 3600) -> int` (called on every `store_temp`).
  - Tests build pages and images with Pillow in-memory and serve them from the Task 2 local server fixture (move that fixture to `tests/conftest.py` as `local_server` + `allow_loopback`).

- [ ] **Step 1: Write failing tests**

```python
def test_extract_order_and_resolution():
    html = '''<meta property="og:image" content="/img/a.jpg">
      <meta name="twitter:image" content="https://cdn.shop/b.jpg">
      <script type="application/ld+json">{"@type":"Product","image":["https://cdn.shop/c.jpg"]}</script>
      <img src="/sprite.png"><img src="logo.svg"><img src="/d.jpg" width="40" height="40"><img src="/e.jpg">'''
    assert extract_candidates(html, "https://shop.example/p/1") == [
        "https://shop.example/img/a.jpg", "https://cdn.shop/b.jpg", "https://cdn.shop/c.jpg",
        "https://shop.example/e.jpg"]

def test_extract_handles_garbage(): assert extract_candidates("<html><<>>&&", "https://x.example/") == []
def test_process_image_resizes_and_strips(): ...   # 3000x1500 PNG with alpha+EXIF → JPEG, max(size)==800, no exif
def test_process_image_rejects_non_image_and_bomb(monkeypatch): ...  # b"hello" and a pixel-limit breach → PictureError
def test_find_pictures_returns_three_valid(local_server, allow_loopback): ...  # page with 5 candidates, one 404, one corrupt → 3 results
def test_find_pictures_page_not_html(local_server, allow_loopback): ...        # application/json → PictureError
# test_images_store.py
def test_store_and_delete(app_config): ...      # file exists then gone; delete(None) no-op
def test_path_for_rejects_traversal(app_config):
    for bad in ("../wishlist.db", "x.jpg", "a"*22 + ".png", ""): assert path_for(bad) is None
def test_temp_promote_and_cleanup(app_config): ...  # promote moves file; old temp (mtime -2h) removed by cleanup_temp
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_pictures.py tests/test_images_store.py -q` → FAIL.
- [ ] **Step 3: Implement** `pictures.py`, `images.py` (images module reads `current_app.config["WISHLIST"].images_dir`; create dirs on demand).
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: shop picture candidates, image processing and storage`.

---

### Task 4: Item pictures in the domain, picture routes and lifecycle

**Files:**
- Create: `wishlist/views/images.py`, `tests/test_item_pictures.py`
- Modify: `wishlist/catalog.py`, `wishlist/reservations.py` (`cancel_by_admin` purge), `wishlist/members.py` (`delete_member`), `wishlist/__init__.py` (register blueprint)

**Interfaces:**
- Consumes: Task 3 `images.*`, `pictures.download_image`, `PictureError`.
- Produces:
  - `catalog.set_item_image(s, item: Item, choice: ImageChoice) -> None`, where `ImageChoice` is one of `("keep",)`, `("none",)`, `("temp", token)`, `("url", image_url)`; `temp` → `images.promote_temp`; `url` → `download_image` + `images.store`; replaces and deletes the previous file; commits; `PictureError` → `ValidationError("image", _("Couldn't use that picture."))`.
  - Hard deletes remove files: `catalog.delete_item` (hard path), `reservations.cancel_by_admin` (purging a soft-deleted item), `members.delete_member` (collect the member's item images before the cascade). A soft-deleted item keeps its file.
  - `GET /images/<name>`: serves `images.path_for(name)` only if an `Item` with `image == name` and `deleted_at IS NULL` exists; else 404. `Cache-Control: private, max-age=86400`. No login required (names are unguessable; guests need them).
  - `GET /images/tmp/<token>`: member-only (`member_required`), `Cache-Control: no-store`.
  - Rate limit: `pictures.allow_fetch(user_id: int) -> bool` — in-process sliding window, 30 per hour per user (document in a comment that each gunicorn worker counts separately).

- [ ] **Step 1: Write failing tests** (Review Focus 3 and 4):

```python
def test_keep_replace_none(db, user): ...  # inside an app context; conftest sets data_dir=tmp_path
    # temp → item.image set + file exists; ("keep",) unchanged; new temp → old file deleted;
    # ("none",) → image None and file deleted
def test_url_choice_downloads(local_server, allow_loopback, db, user): ...
def test_bad_url_choice_is_validation_error(db, user): ...      # "http://10.0.0.1/x.jpg" → ValidationError(field="image")
def test_hard_delete_removes_file(db, user): ...
def test_soft_delete_keeps_file_until_admin_purge(db, user): ...  # reserved item deleted → file kept; cancel_by_admin → gone
def test_delete_member_removes_files(db, admin, user): ...
def test_image_route_access(client, db, user): ...
    # live item's image → 200 image/jpeg; soft-deleted item's image → 404; random valid-looking name → 404;
    # "/images/..%2Fwishlist.db" → 404
def test_temp_route_requires_member(client): ...                 # anonymous → 401
def test_rate_limit(monkeypatch): ...                            # 31st allow_fetch within an hour → False
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_item_pictures.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: item pictures — storage lifecycle and serving`.

---

### Task 5: Design A foundation — base layout, fonts, theme CSS, admin settings

**Files:**
- Create: `wishlist/static/design-a.css`, `wishlist/static/themes.css`, `wishlist/static/fonts/*` (Source Sans 3 300/400/600/900, latin + latin-ext woff2, from `https://cdn.jsdelivr.net/npm/@fontsource/source-sans-3@5/files/`), `wishlist/templates/_macros.html`, `tests/test_layout.py`
- Modify: `wishlist/templates/base.html`, `wishlist/__init__.py` (context processor), `wishlist/views/admin.py` + `admin/index.html` (settings section, `POST /admin/settings`), delete `wishlist/static/pico.classless.min.css`

**Interfaces:**
- Consumes: `themes.get_settings`, `THEMES`, `DESIGNS`, `update_settings` (Task 1).
- Produces:
  - Context processor injecting `page_theme` (default `get_settings(s).default_theme`) and `design` (`get_settings(s).design`); a view overrides the theme by passing `theme=<key>` to `render_template` (base uses `theme or page_theme`).
  - `base.html`: `<html lang=… data-theme="{{ theme or page_theme }}" data-design="{{ design }}">`, links `design-{{ design }}.css` and `themes.css`; header block with logo mark + "LISTA ŻYCZEŃ"/"WISHLIST" label and an `{% block hero %}`; flash area; footer with the language switcher (keep the existing `next` logic). No HTML5 UP code is copied, so no credit line is needed.
  - `_macros.html`: `icon(name)` (inline stroke SVGs: gift, tree, cake, check, lock, external, close, back, copy, plus), `tile(item, href, state=None, image_url=None, index=0)` (picture or theme-colored block cycling `--tile-1..4` by `index`, uppercase name, status line, ribbon for `state == "reserved"`, check badge for `"mine"`), `theme_picker(selected, name="theme")` (radio cards with swatches).
  - CSS: design A visual system from the spec; `themes.css` defines for each theme `--accent`, `--accent-ink` (text on accent), `--accent-text` (accent used as text, ≥ 4.5:1 on white), `--tile-1..4`, `--ribbon`, `--page-bg`, `--hero-decoration` (inline SVG data URI); base element styles so admin pages stay usable without Pico (forms, tables, buttons, `mark`, `article`, `section`, `nav`).
  - Admin: settings form (design select, default theme select) on `/admin/`, `POST /admin/settings` → `update_settings`, flash "Saved.", redirect.

- [ ] **Step 1: Write failing tests**

```python
def test_default_theme_on_member_pages(client, db):
    _, key = make_user(db, "Ania", "a@x.pl")
    update_settings(db, design="a", default_theme="christmas"); login(client, key)
    html = client.get("/").text
    assert 'data-theme="christmas"' in html and "design-a.css" in html and "pico" not in html
def test_fonts_self_hosted(client, db): ...       # no "fonts.googleapis.com" in any page; design-a.css references /static/fonts/
def test_admin_settings_update(admin_client, db): ...     # POST /admin/settings default_theme=birthday → saved; invalid → error shown, unchanged
def test_admin_settings_non_admin_404(client, db): ...
def test_every_page_renders(admin_client, ...): ...       # smoke: /, /items/new, /lists/<id>, /admin/, /admin/members/<id>, welcome, 404 → 200/expected
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; translate new strings.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass (existing view tests may need text-assertion updates only where markup changed; never weaken privacy assertions).
- [ ] **Step 5: Commit** `feat: design A foundation, self-hosted fonts, theme variables, admin settings`.

---

### Task 6: Guest page redesign and item detail

**Files:**
- Create: `wishlist/templates/shared_item.html`, `wishlist/static/dialog.js`, `tests/test_shared_design.py`
- Modify: `wishlist/templates/shared_list.html`, `wishlist/views/shared.py`, `base.html` (include `dialog.js` + an empty `<dialog id="detail">`)

**Interfaces:**
- Consumes: `tile`, `icon` macros (Task 5); `Wishlist.theme`; `/images/<name>` (Task 4); existing `reservations` functions.
- Produces:
  - `GET /shared/<int:wl_id>/items/<int:item_id>` (endpoint `shared.item`): same access rules as reserve (share session version, item in list, not deleted; owner redirected to the owner view) → `shared_item.html` in the list's theme, `Cache-Control: no-store`.
  - Grid page renders tiles linking to `shared.item`; the hint copy (Global Constraints) with the owner's first name.
  - Reserve/cancel POSTs: success → redirect to `shared.view`; validation error / already reserved → re-render `shared_item.html` with the error (400 / 409) instead of the grid.
  - `dialog.js`: clicking a `[data-detail]` link fetches its URL, extracts the `.item-detail` element, puts it in `<dialog id="detail">`, `showModal()`; close button and backdrop click close it; without JS the link navigates normally. Forms inside keep working (full submit).

- [ ] **Step 1: Write failing tests**

```python
def test_grid_shows_tiles_with_states(guest_client, ...): ...
    # available tile links to /shared/<wl>/items/<id>; reserved tile has the ribbon text "RESERVED" and no reserver name;
    # mine tile has "Your reservation"; data-theme equals the list's theme; hint text present with owner first name
def test_detail_available_shows_reserve_form(...): ...   # "Who's buying this?", "I'LL GET IT", shop link with rel="noopener noreferrer"
def test_detail_mine_and_taken(...): ...                 # mine → cancel form; taken → "Someone is already getting this", no form, no name
def test_reserve_from_detail_redirects_to_grid(...): ...
def test_blank_name_rerenders_detail_400(...): ...
def test_already_reserved_rerenders_detail_409(...): ...
def test_detail_access_rules(...): ...                   # other list's item 404; soft-deleted 404; revoked share 404; owner → redirect /lists/<id>
def test_picture_on_tile_and_detail(...): ...            # item with image → <img src="/images/<name>" alt="<item name>">
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; translate.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: guest gift grid and item detail in design A`.

---

### Task 7: Member pages — home, list page with theme picker, item form with picture picker

**Files:**
- Modify: `wishlist/templates/home.html`, `list_owner.html`, `item_form.html`, `wishlist/views/member.py`
- Create: `tests/test_member_design.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5 interfaces.
- Produces:
  - Home: lists as theme-colored cards (`data-theme` on each card), the member's items as tiles linking to edit; "New list" / "Add gift" actions. No reservation info.
  - List page in the list's theme: theme picker (`theme_picker`) inside the list edit form (`POST /lists/<id>/edit` now also takes `theme`); share section unchanged in behavior; items as tiles; add/remove items.
  - Item form: shop link first; submit button `name="action" value="find"` "Find pictures" re-renders the form with up to 3 candidates (each stored with `images.store_temp`, shown via `/images/tmp/<token>`, radio `picture=temp:<token>`), plus radio `picture=url` with field `image_url`, radio `picture=none`, and on edit `picture=keep` (default) showing the current image. Fetch failures / rate limit → message "Couldn't find pictures on that page." (or "Too many picture searches, try again later.") with the manual options still there. Saving maps `picture` to `ImageChoice` and calls `catalog.set_item_image` after create/update; an image `ValidationError` re-renders the form keeping the other fields.

- [ ] **Step 1: Write failing tests**

```python
def test_find_pictures_shows_candidates(member_client, local_server, allow_loopback): ...  # 3 radios temp:<token>, imgs under /images/tmp/
def test_find_pictures_failure_message(member_client, monkeypatch): ...  # find_pictures raises PictureError → message + url/none options
def test_find_rate_limited(member_client, monkeypatch): ...              # allow_fetch False → "Too many picture searches"
def test_save_with_candidate(member_client, ...): ...                    # item.image set, temp gone
def test_save_with_manual_url(...): ...
def test_edit_keeps_picture_by_default(...): ...
def test_list_theme_change(member_client, db, ...): ...                  # POST edit theme=christmas → persisted, page data-theme="christmas"; invalid → error, unchanged
def test_home_and_list_hide_reservations(...): ...                       # regression: reserved items show no ribbon/"RESERVED"/reserver name on owner pages
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; translate.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: member pages in design A with theme picker and picture picker`.

---

### Task 8: Docs, Docker and visual check

**Files:**
- Modify: `README.md` (pictures from shop links + manual URL, where pictures live and backup, `DATA_DIR`, fetch protections, themes and admin defaults, font license OFL for Source Sans 3), `CLAUDE.md` (new modules, theme/design rule "designs differ only by stylesheet", picture invariants), `Dockerfile` if Pillow needs nothing extra (verify), `docs/superpowers/specs/2026-11-06-ui-redesign-design.md` status line untouched.

- [ ] **Step 1:** Build and smoke-test the image (as in the original plan's Task 11 smoke test, with `--security-opt apparmor=unconfined` if needed): bootstrap link printed, `/healthz` ok, `docker exec <container> python -c "import PIL"` succeeds, and `/data/images` is created owned by uid 1000 after saving an item with a picture (use a manual image URL to a public image).
- [ ] **Step 2:** Run the app locally and take phone-width (390 px) screenshots of: guest grid (each theme), item detail, member home, list page, item form with candidates (use a monkeypatched `find_pictures` in a throwaway script, not committed). Report them to the controller; fix layout bugs found.
- [ ] **Step 3: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 4: Commit** `docs: pictures, themes and design A`.
