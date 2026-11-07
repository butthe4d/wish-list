"""Finding item pictures on shop pages and turning them into stored JPEGs."""

import html as htmllib
import io
import json
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from flask import current_app
from PIL import Image, ImageOps

from .fetch import IMAGE_MAX_BYTES, PAGE_MAX_BYTES, FetchError, safe_fetch

Image.MAX_IMAGE_PIXELS = 40_000_000

MAX_CANDIDATES = 12
MAX_ATTEMPTS = 8
FIND_BUDGET = 20
ALLOWED_FORMATS = ("JPEG", "PNG", "GIF", "WEBP", "BMP", "AVIF")
MAX_SIDE = 800
JPEG_QUALITY = 85
_SKIP_WORDS = frozenset({"sprite", "logo", "icon", "pixel", "spacer"})
_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_CT_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.I)


class PictureError(Exception):
    """No usable picture. Messages are for logs, not users."""


def _first_srcset_url(srcset: str) -> str:
    first = re.split(r",\s", srcset.strip(), maxsplit=1)[0].strip()
    return first.split()[0] if first else ""


def _size_below_100(value: str | None) -> bool:
    m = re.fullmatch(r"\s*(\d+)(?:px)?\s*", value or "", re.I)
    return bool(m) and int(m.group(1)) < 100


def _json_ld_images(node, out: list[str], names: list[str] | None = None, descriptions: list[str] | None = None) -> None:
    if isinstance(node, list):
        for n in node:
            _json_ld_images(n, out, names, descriptions)
    elif isinstance(node, dict):
        if "@graph" in node:
            _json_ld_images(node["@graph"], out, names, descriptions)
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            _collect_image_values(node.get("image"), out)
            if names is not None and isinstance(node.get("name"), str):
                names.append(node["name"])
            if descriptions is not None and isinstance(node.get("description"), str):
                descriptions.append(node["description"])


def _collect_image_values(value, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for v in value:
            _collect_image_values(v, out)
    elif isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str):
            out.append(url)


_TEXT_KEYS = frozenset({
    "og:title", "og:site_name", "og:description", "twitter:title", "twitter:description", "description",
})
NAME_MAX = 200
DESCRIPTION_MAX = 300
_TAG_RE = re.compile(r"<[^>]*>")
_SUFFIX_SPLIT_RE = re.compile(r"\s+[|\u2013\u2014-]\s+")


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: list[str] = []
        self.twitter: list[str] = []
        self.json_ld: list[str] = []
        self.link: list[str] = []
        self.imgs: list[str] = []
        self._ld_buf: list[str] | None = None
        self.texts: dict[str, str] = {}  # og:title, og:site_name, ... first value wins
        self.ld_names: list[str] = []
        self.ld_descriptions: list[str] = []
        self.title_tag: str | None = None
        self._title_buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            prop = (a.get("property") or "").lower()
            name = (a.get("name") or "").lower()
            if prop in ("og:image", "og:image:secure_url"):
                self.meta.append(a.get("content", ""))
            elif name == "twitter:image":
                self.twitter.append(a.get("content", ""))
            key = prop if prop.startswith("og:") else name
            if key in _TEXT_KEYS and a.get("content", "").strip():
                self.texts.setdefault(key, a["content"])
        elif tag == "link":
            if "image_src" in (a.get("rel") or "").lower().split():
                self.link.append(a.get("href", ""))
        elif tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self._ld_buf = []
        elif tag == "title":
            if self.title_tag is None:
                self._title_buf = []
        elif tag == "img":
            if _size_below_100(a.get("width")) or _size_below_100(a.get("height")):
                return
            url = a.get("src") or a.get("data-src") or _first_srcset_url(a.get("srcset", ""))
            if url:
                self.imgs.append(url)

    def handle_data(self, data):
        if self._ld_buf is not None:
            self._ld_buf.append(data)
        elif self._title_buf is not None:
            self._title_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._ld_buf is not None:
            try:
                _json_ld_images(
                    json.loads("".join(self._ld_buf)), self.json_ld, self.ld_names, self.ld_descriptions
                )
            except (ValueError, RecursionError):
                pass
            self._ld_buf = None
        elif tag == "title" and self._title_buf is not None:
            self.title_tag = "".join(self._title_buf)
            self._title_buf = None


def _parse(html: str) -> _Collector:
    parser = _Collector()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # html.parser can raise on pathological input; keep what we have
        pass
    return parser


def _clean_text(value: str | None) -> str:
    text = htmllib.unescape(value or "")
    return " ".join(_TAG_RE.sub(" ", text).split())


def _host_label(page_url: str) -> str:
    try:
        host = (urlsplit(page_url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(".", 1)[0]


def _clean_title(value: str | None, site_name: str, label: str) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parts = _SUFFIX_SPLIT_RE.split(text)
    if len(parts) > 1:
        suffix = parts[-1].casefold()
        if suffix == site_name.casefold() or (label and label in suffix):
            text = text[: text.rfind(parts[-1])].rstrip()
            text = re.sub(r"\s*[|\u2013\u2014-]$", "", text).rstrip()
    if not text or text.casefold() == site_name.casefold():
        return None
    return text[:NAME_MAX].rstrip()


def _clean_description(value: str | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX:
        cut = text[:DESCRIPTION_MAX]
        if " " in cut and text[DESCRIPTION_MAX] != " ":
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip() + "…"
    return text


def _details_from(parser: _Collector, page_url: str) -> tuple[str | None, str | None]:
    t = parser.texts
    site_name = _clean_text(t.get("og:site_name"))
    label = _host_label(page_url)
    title = None
    for raw in (
        t.get("og:title"),
        parser.ld_names[0] if parser.ld_names else None,
        t.get("twitter:title"),
        parser.title_tag,
    ):
        title = _clean_title(raw, site_name, label)
        if title:
            break
    description = None
    for raw in (
        t.get("og:description"),
        parser.ld_descriptions[0] if parser.ld_descriptions else None,
        t.get("description"),
        t.get("twitter:description"),
    ):
        description = _clean_description(raw)
        if description:
            break
    return title, description


def extract_details(html: str, page_url: str) -> tuple[str | None, str | None]:
    """Product title and description from a shop page, or None for each that is missing."""
    return _details_from(_parse(html), page_url)


def extract_candidates(html: str, base_url: str) -> list[str]:
    return _candidates_from(_parse(html), base_url)


def _candidates_from(parser: _Collector, base_url: str) -> list[str]:
    raw = [
        (u, False) for u in parser.meta + parser.twitter + parser.json_ld + parser.link
    ] + [(u, True) for u in parser.imgs]
    seen: set[str] = set()
    result: list[str] = []
    for url, from_img in raw:
        url = url.strip()
        if not url or url.lower().startswith("data:"):
            continue
        try:
            absolute = urljoin(base_url, url)
            parts = urlsplit(absolute)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if from_img:
            filename = parts.path.lower().rsplit("/", 1)[-1]
            tokens = {t.rstrip("s") for t in re.split(r"[^a-z]+", filename)}
            if filename.endswith(".svg") or tokens & _SKIP_WORDS:
                continue
        if absolute in seen:
            continue
        seen.add(absolute)
        result.append(absolute)
        if len(result) >= MAX_CANDIDATES:
            break
    return result


def process_image(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS) as probe:
            width, height = probe.size
            if width * height > Image.MAX_IMAGE_PIXELS:
                raise PictureError("image has too many pixels")
            probe.verify()
        img = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
        if img.format == "JPEG":
            img.draft("RGB", (2 * MAX_SIDE, 2 * MAX_SIDE))
        img.load()
    except PictureError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PictureError("not a usable image") from exc
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            img = background
        else:
            img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=JPEG_QUALITY)
    except (OSError, ValueError) as exc:
        raise PictureError("image could not be processed") from exc
    return out.getvalue()


def _deny_networks():
    return current_app.config["WISHLIST"].fetch_deny_networks


def download_image(url: str, *, timeout: float | None = None) -> bytes:
    try:
        result = safe_fetch(
            url,
            max_bytes=IMAGE_MAX_BYTES,
            accept="image/*",
            deny_networks=_deny_networks(),
            timeout=timeout,
        )
    except FetchError as exc:
        raise PictureError(str(exc)) from exc
    return process_image(result.content)


def _decode_html(content: bytes, content_type: str) -> str:
    charset = None
    m = _CT_CHARSET_RE.search(content_type)
    if m:
        charset = m.group(1)
    else:
        m = _CHARSET_RE.search(content[:2048])
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    if charset:
        try:
            return content.decode(charset, errors="replace")
        except (LookupError, UnicodeError):  # unknown, or not a text codec (e.g. "undefined", "idna")
            pass
    return content.decode("utf-8", errors="replace")


@dataclass
class PageInfo:
    title: str | None
    description: str | None
    pictures: list[bytes]


def inspect_page(page_url: str, *, limit: int = 3) -> PageInfo:
    expires = time.monotonic() + FIND_BUDGET
    try:
        page = safe_fetch(
            page_url,
            max_bytes=PAGE_MAX_BYTES,
            accept="text/html",
            deny_networks=_deny_networks(),
            timeout=FIND_BUDGET,
        )
    except FetchError as exc:
        raise PictureError(str(exc)) from exc
    if "html" not in (page.content_type or "").lower():
        raise PictureError("page is not HTML")
    parser = _parse(_decode_html(page.content, page.content_type or ""))
    title, description = _details_from(parser, page.final_url)
    pictures: list[bytes] = []
    for url in _candidates_from(parser, page.final_url)[:MAX_ATTEMPTS]:
        left = expires - time.monotonic()
        if left <= 0:
            break
        try:
            pictures.append(download_image(url, timeout=left))
        except PictureError:
            continue
        if len(pictures) >= limit:
            break
    return PageInfo(title, description, pictures)


def find_pictures(page_url: str, *, limit: int = 3) -> list[bytes]:
    pictures = inspect_page(page_url, limit=limit).pictures
    if not pictures:
        raise PictureError("no usable picture")
    return pictures


FETCH_LIMIT = 30
FETCH_WINDOW = 3600
_fetch_log: dict[int, list[float]] = {}
_fetch_lock = threading.Lock()


def allow_fetch(user_id: int) -> bool:
    """Sliding-window limit of 30 page fetches per user per hour.

    In-process only: each gunicorn worker counts separately.
    """
    now = time.monotonic()
    with _fetch_lock:
        recent = [t for t in _fetch_log.get(user_id, []) if now - t < FETCH_WINDOW]
        allowed = len(recent) < FETCH_LIMIT
        if allowed:
            recent.append(now)
        if recent:
            _fetch_log[user_id] = recent
        else:
            _fetch_log.pop(user_id, None)
        return allowed
