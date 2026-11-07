# Fill Name and Description from the Shop Page — Short Design

Date: 2026-11-08
Status: approved
Builds on: `2026-11-06-ui-redesign-design.md` (picture search)

## Intent

Adding a gift should be "paste link, tap search, pick picture, save". The picture
search already downloads the shop page; it should also take the product's name
and description from it so the member doesn't have to type them.

## Goals

- "Szukaj zdjęć / Find pictures" also reads the product title and description
  from the same page download (no extra request, same rate limit and budget).
- Empty name/description fields are filled directly.
- A field the member already filled that differs from the shop's value: ask in a
  popup (the existing `<dialog>`), e.g. "Sklep nazywa to «Słuchawki X200».
  Zamienić «Słuchawki»?" — one tick box per differing field (name, description),
  buttons **Zamień / Replace** (applies the ticked fields) and **Zostaw moje /
  Keep mine**. Identical values: no question.
- Without JavaScript: under each differing field a line "Ze sklepu: «…»" with a
  **Zamień** button that applies that value (form re-render, no save).

## Non-goals

- Prices, availability, brand or other product data.
- Auto-searching without the button.
- Changing how pictures are found.

## Anti-goals

- Overwriting text the member typed without asking.
- Long marketing blurbs pasted into the description.

## Approaches considered

1. **In-page popup with per-field tick boxes, inline fallback without JS —
   chosen by the user.** Matches the design, localized, handles both fields at once.
2. Suggestion line under each field only — simpler, easy to miss.
3. Browser `confirm()` — not localized, one yes/no at a time. Rejected.

## How (decided)

- Title sources, first non-empty wins: `og:title`, JSON-LD `Product.name`,
  `twitter:title`, `<title>`. Description: `og:description`, JSON-LD
  `Product.description`, `meta[name=description]`, `twitter:description`.
- Cleanup: unescape HTML entities, strip tags, collapse whitespace; drop a
  trailing shop suffix after ` | `, ` – `, ` — `, ` - ` when it matches
  `og:site_name` or the shop's domain name (builder's call on matching rules).
- Limits: name cut to 200 characters (the field limit); description cut to about
  300 characters at a word boundary with "…".
- Hostile or odd pages never cause errors: missing data just means nothing is
  suggested.

## Left to the builder

- Function shape (e.g. one page-inspection call returning pictures, title and
  description), suffix-matching details, popup wording beyond the strings above.
- Tests with sample pages (og, JSON-LD, plain `<title>`, suffix stripping,
  long description, entities, existing-field conflicts with and without JS).
