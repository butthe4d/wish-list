from pathlib import Path
import re

TEMPLATES = Path(__file__).resolve().parent.parent / "wishlist" / "templates"


def test_every_form_disables_browser_validation():
    # Browser validation bubbles use the browser's language, not the page's;
    # the server validates every field with translated messages instead.
    missing = [
        f"{path.relative_to(TEMPLATES)}: {tag}"
        for path in TEMPLATES.rglob("*.html")
        for tag in re.findall(r"<form\b[^>]*>", path.read_text(), flags=re.S)
        if "novalidate" not in tag
    ]
    assert missing == []
