import subprocess
import sys
from pathlib import Path

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent


def test_every_message_translated_to_polish(tmp_path):
    pot = tmp_path / "m.pot"
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract", "-F", "babel.cfg", "-o", str(pot), "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
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
