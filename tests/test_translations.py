import re
import subprocess
import sys
from pathlib import Path

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent


def _extract(out):
    """Same keywords as `make i18n-update` (read from the Makefile, the single place)."""
    keywords = re.findall(r"-k (\S+)", (ROOT / "Makefile").read_text())
    args = [a for k in keywords for a in ("-k", k)]
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract", "-F", "babel.cfg", *args,
         "-o", str(out), "."],
        cwd=ROOT, check=True, capture_output=True,
    )


def test_extraction_sees_lazy_gettext(tmp_path):
    pot = tmp_path / "m.pot"
    _extract(pot)
    with open(pot, "rb") as f:
        assert "Christmas" in {m.id for m in read_po(f)}


def test_every_message_translated_to_polish(tmp_path):
    pot = tmp_path / "m.pot"
    _extract(pot)
    with open(pot, "rb") as f:
        extracted = read_po(f)
    with open(ROOT / "wishlist/translations/pl/LC_MESSAGES/messages.po", "rb") as f:
        pl = read_po(f, locale="pl")
    assert len(extracted) > 0
    problems = []
    for msg in extracted:
        if not msg.id:
            continue
        have = pl.get(msg.id)
        if have is None or not have.string or "fuzzy" in have.flags:
            problems.append(msg.id)
        elif isinstance(have.string, tuple) and not all(have.string):
            problems.append(msg.id)
    assert not problems, f"untranslated or fuzzy in pl: {problems}"


def test_polish_messages_have_no_glued_sentences():
    with open(ROOT / "wishlist/translations/pl/LC_MESSAGES/messages.po", "rb") as f:
        pl = read_po(f, locale="pl")
    glued = re.compile(r"[.!?][A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]")
    bad = []
    for msg in pl:
        if not msg.id:  # header
            continue
        strings = msg.string if isinstance(msg.string, tuple) else (msg.string,)
        if any(glued.search(s or "") for s in strings):
            bad.append(msg.id)
    assert not bad, f"pl msgstr with glued sentences: {bad}"
