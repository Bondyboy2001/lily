# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Duplicate auto-resolution guardrails, run against a real temp library: Title must be a
criterion, a preview must come first, big runs stop before deleting, a copy with reading
data is kept, and removed copies go to the Trash."""

import os
import sqlite3
from datetime import datetime, timezone

import pytest

from cps import trash_store as store
from cps.duplicate_rules import auto_resolve_block_reason, auto_resolve_delete_cap, select_book_to_keep
from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------- pure rules

def test_block_reason_needs_title_and_a_preview():
    assert "Title" in auto_resolve_block_reason({"duplicate_detection_title": False,
                                                 "duplicate_auto_resolve_previewed_at": "2026-01-01T00:00:00"})
    assert "Preview" in auto_resolve_block_reason({"duplicate_detection_title": True,
                                                   "duplicate_auto_resolve_previewed_at": ""})
    assert auto_resolve_block_reason({"duplicate_detection_title": True,
                                      "duplicate_auto_resolve_previewed_at": ""}, require_preview=False) is None
    assert auto_resolve_block_reason({"duplicate_detection_title": True,
                                      "duplicate_auto_resolve_previewed_at": "2026-01-01T00:00:00"}) is None


def test_delete_cap_is_20_or_one_percent():
    assert auto_resolve_delete_cap(0) == 20
    assert auto_resolve_delete_cap(1999) == 20
    assert auto_resolve_delete_cap(5000) == 50


def test_engaged_copy_wins_over_the_strategy():
    from types import SimpleNamespace
    old = SimpleNamespace(id=1, timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc), data=[])
    new = SimpleNamespace(id=2, timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc), data=[])
    assert select_book_to_keep([old, new], "newest").id == 2
    assert select_book_to_keep([old, new], "newest", preferred_ids={1}).id == 1
    assert select_book_to_keep([old, new], "oldest", preferred_ids={1, 2}).id == 1
    assert select_book_to_keep([old, new], "newest", preferred_ids={99}).id == 2


# ---------------------------------------------------------------------------- live library

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        from cps import duplicates, editbooks
        monkeypatch.setattr(duplicates, "DUPLICATE_BACKUP_ROOT", str(tmp_path / "dup-backups"))
        monkeypatch.setattr(editbooks, "_queue_duplicate_scan_after_change", lambda ids=None: None)
        _settings(duplicate_detection_title=1, duplicate_auto_resolve_previewed_at="2026-01-01T00:00:00")
        yield e


def _settings(**values):
    from cwa_db import CWA_DB
    with CWA_DB() as db:
        db.update_cwa_settings(values)
        return db.cwa_settings


def _pair(env, title, author="Pair Author"):
    """Two copies of one book; the second is newer."""
    first = env.add_library_book(title, author=author, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    second = env.add_library_book(title + " Copy", author=author, files=("metamorphosis.txt",),
                                  timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc))
    return first, second


def _groups(*pairs):
    from cps import calibre_db
    calibre_db.ensure_session()
    out = []
    for i, (a, b) in enumerate(pairs):
        books = [calibre_db.get_book(a), calibre_db.get_book(b)]
        out.append({"group_hash": "%032x" % (i + 1), "title": books[0].title, "author": "Pair Author", "books": books})
    return out


def _exists(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute("SELECT COUNT(*) FROM books WHERE id=?", (book_id,)).fetchone()[0] == 1
    finally:
        con.close()


def _trashed_ids(env):
    return sorted(e["book_id"] for e in store.list_entries(str(env.library_dir / store.TRASH_DIRNAME)))


def _resolve(groups, strategy="newest", trigger="automatic", dry_run=False):
    from cps.duplicates import auto_resolve_duplicates
    return auto_resolve_duplicates(strategy=strategy, dry_run=dry_run, trigger_type=trigger,
                                   duplicate_groups=groups)


def test_auto_resolve_moves_the_losing_copy_to_the_trash(env, tmp_path):
    a, b = _pair(env, "Dune")
    with env.app.app_context():
        result = _resolve(_groups((a, b)))
    assert result["success"] and result["deleted_count"] == 1
    assert _exists(env, b) and not _exists(env, a)  # newest kept
    assert _trashed_ids(env) == [a]
    assert len(os.listdir(tmp_path / "dup-backups")) == 1


def test_auto_resolve_refuses_without_title(env):
    a, b = _pair(env, "Emma")
    _settings(duplicate_detection_title=0)
    with env.app.app_context():
        for trigger in ("automatic", "manual"):
            result = _resolve(_groups((a, b)), trigger=trigger)
            assert result["aborted"] and "Title" in result["message"]
    assert _exists(env, a) and _exists(env, b) and _trashed_ids(env) == []
    assert "Title" in str(_settings()["duplicate_auto_resolve_last_abort"])


def test_automatic_run_needs_a_preview_first(env):
    a, b = _pair(env, "Ivanhoe")
    _settings(duplicate_auto_resolve_previewed_at="")
    with env.app.app_context():
        result = _resolve(_groups((a, b)))
    assert result["aborted"] and "Preview" in result["message"]
    assert _exists(env, a) and _exists(env, b)


def test_auto_resolve_aborts_above_the_deletion_cap(env):
    pairs = [_pair(env, "Book %02d" % i) for i in range(21)]
    with env.app.app_context():
        result = _resolve(_groups(*pairs))
    assert result["aborted"] and "would delete 21 books" in result["message"]
    assert all(_exists(env, x) for p in pairs for x in p)
    assert _trashed_ids(env) == []
    last = _settings()["duplicate_auto_resolve_last_abort"]
    assert "limit is 20" in (last if isinstance(last, str) else ",".join(last))
    # A manual run the admin chose (after previewing) is not capped
    with env.app.app_context():
        result = _resolve(_groups(*pairs[:2]), trigger="manual")
    assert result["deleted_count"] == 2


def test_copy_with_shelf_or_progress_is_kept(env):
    from cps import ub
    a, b = _pair(env, "Middlemarch")
    ub.session.add(ub.WebReaderProgress(user_id=env.admin().id, book_id=a, cfi="epubcfi(/6/2)", percent=0.3))
    ub.session.commit()
    with env.app.app_context():
        result = _resolve(_groups((a, b)))  # "newest" would keep b
    assert result["deleted_count"] == 1
    assert _exists(env, a) and not _exists(env, b)


def test_merge_strategy_merges_formats_and_trashes_the_other_copy(env):
    a, b = _pair(env, "Kidnapped")
    with env.app.app_context():
        result = _resolve(_groups((a, b)), strategy="merge")
    assert result["deleted_count"] == 1
    assert _trashed_ids(env) == [a]
    kept = env.library_dir / "Pair Author" / f"Kidnapped Copy ({b})"
    assert sorted(os.path.splitext(f)[1] for f in os.listdir(kept) if f.endswith((".epub", ".txt"))) == [".epub", ".txt"]
    entry = store.list_entries(str(env.library_dir / store.TRASH_DIRNAME))[0]
    assert entry["reason"].startswith("duplicate of %d" % b)


def test_split_library_backs_up_and_deletes_from_the_book_folder(env, tmp_path):
    from cps import config
    a, b = _pair(env, "Rob Roy")
    elsewhere = tmp_path / "metadata-only"
    elsewhere.mkdir()
    config.config_calibre_split = True
    config.config_calibre_split_dir = str(env.library_dir)
    config.config_calibre_dir = str(elsewhere)
    with env.app.app_context():
        result = _resolve(_groups((a, b)))
    assert result["deleted_count"] == 1, result
    [backup] = os.listdir(tmp_path / "dup-backups")
    assert os.listdir(tmp_path / "dup-backups" / backup) == ["book_%d" % a]
    assert _trashed_ids(env) == [a]


def test_missing_book_folder_is_not_deleted(env):
    import shutil
    a, b = _pair(env, "Waverley")
    shutil.rmtree(env.library_dir / "Pair Author" / f"Waverley ({a})")
    with env.app.app_context():
        result = _resolve(_groups((a, b)))
    assert result["deleted_count"] == 0 and "missing" in result["errors"][0]
    assert _exists(env, a)


def test_settings_refuse_enabling_auto_resolve_without_title_or_preview(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    _settings(duplicate_auto_resolve_previewed_at="")
    c.post("/cwa-settings", data={"duplicate_detection_title": "1", "duplicate_auto_resolve_enabled": "1"})
    assert not _settings()["duplicate_auto_resolve_enabled"]

    a, b = _pair(env, "Rebecca")
    resp = c.post("/duplicates/preview-resolution", json={"strategy": "newest"})
    assert resp.status_code == 200 and resp.get_json()["success"]
    assert _settings()["duplicate_auto_resolve_previewed_at"]
    assert _exists(env, a) and _exists(env, b)  # a preview deletes nothing

    c.post("/cwa-settings", data={"duplicate_auto_resolve_enabled": "1"})  # Title unchecked
    assert not _settings()["duplicate_auto_resolve_enabled"]
    c.post("/cwa-settings", data={"duplicate_detection_title": "1", "duplicate_auto_resolve_enabled": "1"})
    assert _settings()["duplicate_auto_resolve_enabled"]


def test_settings_page_shows_the_guardrails(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    _settings(duplicate_auto_resolve_previewed_at="", duplicate_auto_resolve_last_abort="2026-01-01 03:00 Stopped")
    html = c.get("/cwa-settings").get_data(as_text=True)
    assert 'data-needs-title="1" disabled' in html and "(or 1% of the library)" in html
    assert "Preview first" in html and "2026-01-01 03:00 Stopped" in html
