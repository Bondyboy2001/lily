"""Static checks on user-facing copy (docs/design.md §10 Voice)."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CPS = REPO_ROOT / "cps"
TEMPLATES = CPS / "templates"

# Phrases that must never reach the reader: raw database errors, the fork's old
# names and multi-user leftovers in a single-user app.
BANNED = ["Oops! Database Error", "Calibre-Web", "calibre-web", "calibre database",
          "cwa-book-ingest", "(Public)", "logged in as"]

# A translatable string: _("...") or _('...'), including implicitly joined pieces.
TRANSLATABLE = re.compile(r"""_\(\s*u?((?:"[^"\n]*"\s*|'[^'\n]*'\s*)+)""")


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(template):
    template = re.sub(r"\{#.*?#\}", "", template, flags=re.S)
    return re.sub(r"<!--.*?-->", "", template, flags=re.S)


def ui_strings():
    """(file, string) for every translatable string in the app and templates."""
    sources = list(CPS.glob("*.py")) + list(CPS.glob("services/*.py"))
    found = []
    for path in sources:
        for match in TRANSLATABLE.finditer(read(path)):
            found.append((path.name, match.group(1)))
    for path in TEMPLATES.glob("*.*"):
        for match in TRANSLATABLE.finditer(strip_comments(read(path))):
            found.append((path.name, match.group(1)))
    return found


def test_ui_strings_avoid_banned_phrases():
    offenders = [(name, text) for name, text in ui_strings()
                 for phrase in BANNED if phrase in text]
    assert offenders == []


def test_flashes_do_not_show_raw_exceptions():
    pattern = re.compile(r"flash\(\s*_\([^\n]*(error=ex?\b|\.format\(ex?\))")
    for path in CPS.glob("*.py"):
        assert not pattern.search(read(path)), path.name


def test_new_password_field_hints_password_managers():
    user_edit = read(TEMPLATES / "user_edit.html")
    field = re.search(r'<input type="password"[^>]*name="password"[^>]*>', user_edit).group(0)
    assert 'autocomplete="new-password"' in field
