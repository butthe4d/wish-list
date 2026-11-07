# Fill Name and Description from the Shop Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Find pictures" also fills the item name and description from the shop page, asking in a popup before replacing text the member already entered.

**Architecture:** Extend `wishlist/pictures.py`'s existing HTML parsing to collect title/description sources and return them with the pictures from one page download (`inspect_page`). The item-form view fills empty fields and renders per-field suggestions; a small `suggest.js` turns the suggestions into a `<dialog>` with tick boxes; without JS each suggestion has an "apply" submit button.

**Tech Stack:** existing (Flask, Jinja, stdlib `html.parser`, vanilla JS).

**Spec:** `docs/superpowers/specs/2026-11-08-shop-title-description-design.md`

## Global Constraints

- Same single page download, same `allow_fetch` count and `FIND_BUDGET` as today; no new outbound request.
- Title sources in order: `og:title`, JSON-LD `Product.name`, `twitter:title`, `<title>`. Description: `og:description`, JSON-LD `Product.description`, `meta[name=description]`, `twitter:description`.
- Cleanup: `html.unescape`, strip tags, collapse whitespace; drop a trailing suffix after ` | `, ` – `, ` — `, ` - ` when it equals (case-insensitive) `og:site_name` or contains the shop's registrable host name label (e.g. `x-kom` for `www.x-kom.pl`); name ≤ 200 chars; description ≤ 300 chars cut at a word boundary + "…".
- Never overwrite a non-empty field without the member confirming; identical values (after whitespace normalisation) produce no suggestion.
- Strings (en / pl): "From the shop: «%(value)s»" / "Ze sklepu: «%(value)s»"; button "Replace" / "Zamień"; "Keep mine" / "Zostaw moje"; popup title "Use the shop's text?" / "Użyć tekstu ze sklepu?"; field labels reuse existing "Name"/"Description" msgids.
- All CLAUDE.md invariants (CSRF + `novalidate` on forms, gettext + pl, privacy unaffected).

## Review Focus

1. **Odd pages** (no title, only `<title>`, entities like `&amp;`, tags inside JSON-LD description, 50 KB description, title that is only the shop name) → sensible value or no suggestion; never a 500. Task 1.
2. **Member already typed text**: never replaced silently, on both new and edit forms. Task 2.
3. **No JS**: the apply buttons work and keep picture candidates and other typed fields. Task 2.
4. **Candidate tokens round-tripped through the form** are re-validated (`images.temp_path`) — a forged token is ignored. Task 2.
5. **Pages with details but no pictures**: name/description still filled, notice "Couldn't find pictures on that page." still shown. Task 2.

---

### Task 1: Page details extraction

**Files:** Modify `wishlist/pictures.py`; Test `tests/test_page_details.py`

**Interfaces:**
- Produces:
  - `@dataclass PageInfo(title: str | None, description: str | None, pictures: list[bytes])`.
  - `extract_details(html: str, page_url: str) -> tuple[str | None, str | None]` (title, description), using the source order and cleanup in Global Constraints (extend `_Collector` rather than parsing twice; `extract_candidates` behaviour unchanged).
  - `inspect_page(page_url: str, *, limit: int = 3) -> PageInfo`: one `safe_fetch` (as `find_pictures` does today), details extracted from that page, pictures downloaded as today. Page-level FetchError / not-HTML → `PictureError` (unchanged). If pictures fail but the page was fetched, return `PageInfo(title, description, [])` instead of raising (budget exhaustion included).
  - `find_pictures` becomes a thin wrapper returning `inspect_page(...).pictures` and raising `PictureError` when that list is empty (keeps existing callers/tests valid).

- [ ] **Step 1: Write failing tests**

```python
def test_title_order_and_suffix():
    html = '''<meta property="og:site_name" content="Sklep XYZ">
      <meta property="og:title" content="Słuchawki X200 | Sklep XYZ">
      <title>ignored</title>'''
    assert extract_details(html, "https://sklep.example/p/1")[0] == "Słuchawki X200"

def test_title_falls_back_to_jsonld_then_title_tag(): ...   # no og → Product.name; neither → <title> "Kubek &amp; spodek - x-kom" on www.x-kom.pl → "Kubek & spodek"
def test_description_cleanup_and_limit(): ...              # tags + entities stripped; 2000-char text → ≤ 301 chars ending "…", cut at a space
def test_title_only_shop_name_is_none(): ...               # og:title == og:site_name → None
def test_garbage_html_returns_none(): assert extract_details("<<<&&", "https://x.example/") == (None, None)
def test_inspect_page_details_without_pictures(local_server, allow_loopback): ...  # page with og:title, no images → PageInfo(title, None, [])
def test_find_pictures_still_raises_without_pictures(local_server, allow_loopback): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_page_details.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: read product title and description from the shop page`.

---

### Task 2: Item form — fill, suggest, confirm

**Files:** Modify `wishlist/views/member.py`, `wishlist/templates/item_form.html`, `wishlist/static/design-a.css`, translations; Create `wishlist/static/suggest.js`, `tests/test_shop_suggestions.py`

**Interfaces:**
- Consumes: `pictures.inspect_page`, `PageInfo` (Task 1); existing `_find_pictures`, `_render_form`, `images.store_temp`/`temp_path`.
- Produces:
  - `_find_pictures` calls `inspect_page`; builds a form copy where empty `name`/`description` are filled from the page; for each field that is non-empty and differs from the shop value, a suggestion `{"name": value, "description": value}` passed to the template.
  - Template: per suggested field, below the field: `<div class="shop-suggestion" data-field="name">` containing the "From the shop: «…»" text, a hidden `suggest_name` (or `suggest_description`) input carrying the value, and `<button type="submit" name="action" value="apply:name">Replace</button>`. Candidate tokens are carried in hidden `candidate` inputs so any re-render keeps them.
  - `action=apply:<field>` (field in `name`/`description` only, else ignored): copy `suggest_<field>` into the field, drop that suggestion, re-render with the remaining suggestions and the candidates (tokens re-validated with `images.temp_path`; invalid ones dropped). Nothing is saved; `allow_fetch` is not consumed.
  - `suggest.js` (loaded only on the item form, `defer`): if `.shop-suggestion` elements exist, open a `<dialog>` titled "Use the shop's text?" listing each with a checked tick box ("Name: «X» → «Y»"); **Replace** writes the ticked values into the fields client-side and removes their inline suggestions; **Keep mine** removes the inline suggestions; Escape/backdrop = Keep mine. Without JS the inline blocks stay.

- [ ] **Step 1: Write failing tests**

```python
def test_find_fills_empty_fields(member_client, monkeypatch): ...      # inspect_page → PageInfo("Słuchawki X200","Czarne",[jpeg]); empty form → inputs carry those values; no .shop-suggestion
def test_find_suggests_for_differing_fields(member_client, monkeypatch): ...  # name="Słuchawki" typed → value kept, suggestion with "Słuchawki X200", apply button
def test_identical_value_no_suggestion(...): ...
def test_apply_button_replaces_and_keeps_candidates(...): ...          # POST action=apply:name, suggest_name, candidate=<token> → name replaced, candidate radio still present, item count unchanged
def test_apply_ignores_unknown_field_and_forged_token(...): ...        # action=apply:link → no change; candidate="../x" → dropped
def test_details_without_pictures_still_fill(...): ...                  # PageInfo(title, None, []) → name filled + "Couldn't find pictures on that page."
def test_edit_form_never_replaces_silently(...): ...                   # existing item name differs → suggestion, stored name unchanged until save
def test_suggest_js_loaded_only_on_item_form(...): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_shop_suggestions.py -q` → FAIL.
- [ ] **Step 3: Implement**; `make i18n-update`, translate pl.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` → pass.
- [ ] **Step 5: Visual check** with Playwright (system `python3`) at 390 px: item form after a search with both suggestions (popup open), after Replace, and with JS disabled; screenshots in the session scratchpad, not committed.
- [ ] **Step 6: Commit** `feat: fill item name and description from the shop, ask before replacing`.
