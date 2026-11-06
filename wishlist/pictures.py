"""Finding item pictures on shop pages and turning them into stored JPEGs."""

import io
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from flask import current_app
from PIL import Image, ImageOps

from .fetch import IMAGE_MAX_BYTES, PAGE_MAX_BYTES, FetchError, safe_fetch

Image.MAX_IMAGE_PIXELS = 40_000_000

MAX_CANDIDATES = 12
MAX_ATTEMPTS = 8
FIND_BUDGET = 20
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


def _json_ld_images(node, out: list[str]) -> None:
    if isinstance(node, list):
        for n in node:
            _json_ld_images(n, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            _json_ld_images(node["@graph"], out)
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            _collect_image_values(node.get("image"), out)


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


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: list[str] = []
        self.twitter: list[str] = []
        self.json_ld: list[str] = []
        self.link: list[str] = []
        self.imgs: list[str] = []
        self._ld_buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            prop = (a.get("property") or "").lower()
            name = (a.get("name") or "").lower()
            if prop in ("og:image", "og:image:secure_url"):
                self.meta.append(a.get("content", ""))
            elif name == "twitter:image":
                self.twitter.append(a.get("content", ""))
        elif tag == "link":
            if "image_src" in (a.get("rel") or "").lower().split():
                self.link.append(a.get("href", ""))
        elif tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self._ld_buf = []
        elif tag == "img":
            if _size_below_100(a.get("width")) or _size_below_100(a.get("height")):
                return
            url = a.get("src") or a.get("data-src") or _first_srcset_url(a.get("srcset", ""))
            if url:
                self.imgs.append(url)

    def handle_data(self, data):
        if self._ld_buf is not None:
            self._ld_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._ld_buf is not None:
            try:
                _json_ld_images(json.loads("".join(self._ld_buf)), self.json_ld)
            except (ValueError, RecursionError):
                pass
            self._ld_buf = None


def extract_candidates(html: str, base_url: str) -> list[str]:
    parser = _Collector()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # html.parser can raise on pathological input; keep what we have
        pass
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
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width * height > Image.MAX_IMAGE_PIXELS:
                raise PictureError("image has too many pixels")
            probe.verify()
        img = Image.open(io.BytesIO(data))
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
        except LookupError:
            pass
    return content.decode("utf-8", errors="replace")


def find_pictures(page_url: str, *, limit: int = 3) -> list[bytes]:
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
    candidates = extract_candidates(_decode_html(page.content, page.content_type or ""), page.final_url)
    pictures: list[bytes] = []
    for url in candidates[:MAX_ATTEMPTS]:
        left = expires - time.monotonic()
        if left <= 0:
            if not pictures:
                raise PictureError("time budget exhausted")
            break
        try:
            pictures.append(download_image(url, timeout=left))
        except PictureError:
            continue
        if len(pictures) >= limit:
            break
    if not pictures and time.monotonic() >= expires:
        raise PictureError("time budget exhausted")
    return pictures
