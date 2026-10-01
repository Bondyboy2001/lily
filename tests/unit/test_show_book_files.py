"""/show/ serves uploaded book files from Lily's own origin: reader formats stay readable,
everything else is downloaded inside a CSP sandbox."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

PAYLOAD = b"<html><script>alert(document.cookie)</script></html>"


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _book_with_file(env, title, fmt, content=PAYLOAD):
    book = env.add_book(title, fmt=fmt.upper())
    folder = env.library_dir / "Test Author" / title
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{title}.{fmt.lower()}").write_bytes(content)
    return book


def _admin(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return c


def test_txt_is_plain_text_and_a_download(env):
    # No TXT reader any more: never rendered as a page on this origin, offered as a file
    book = _book_with_file(env, "Plain", "txt")
    resp = _admin(env).get(f"/show/{book}/txt")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert resp.data == PAYLOAD


@pytest.mark.parametrize("fmt", ["html", "mobi", "docx"])
def test_formats_no_reader_opens_are_sandboxed_downloads(env, fmt):
    book = _book_with_file(env, "Odd " + fmt, fmt)
    resp = _admin(env).get(f"/show/{book}/{fmt}")
    assert resp.status_code == 200
    assert resp.headers["Content-Security-Policy"] == "sandbox"
    assert resp.headers["Content-Disposition"].startswith("attachment")


@pytest.mark.parametrize("fmt", ["pdf", "epub", "djvu", "mp3"])
def test_reader_and_audio_formats_are_not_forced_to_download(env, fmt):
    book = _book_with_file(env, "Readable " + fmt, fmt, content=b"not really a book")
    resp = _admin(env).get(f"/show/{book}/{fmt}")
    assert resp.status_code == 200
    assert "attachment" not in resp.headers.get("Content-Disposition", "")
    assert resp.headers.get("Content-Security-Policy") != "sandbox"
