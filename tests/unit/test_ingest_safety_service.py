# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""cwa-ingest-service run script: timeouts, failed backups and the retry queue."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run"


@pytest.fixture
def svc(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    dirs = {name: tmp_path / name for name in ("watch", "processing", "recent", "failed", "bin")}
    for d in dirs.values():
        d.mkdir()
    if shutil.which("timeout") is None:
        # macOS dev machines: minimal stand-in that just runs the command
        shim = dirs["bin"] / "timeout"
        shim.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        shim.chmod(0o755)
    stub = dirs["bin"] / "processor"
    stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        printf '%s\\n' "$1" >> "$PROCESSOR_LOG"
        [ -z "${PROCESSOR_DELETE:-}" ] || rm -f "$1"
        exit "${PROCESSOR_EXIT_CODE:-0}"
    """))
    stub.chmod(0o755)
    post = dirs["bin"] / "post"
    post.write_text("#!/usr/bin/env bash\nexit 0\n")
    post.chmod(0o755)

    env = dict(os.environ)
    env.update({
        "PATH": f"{dirs['bin']}:{env.get('PATH', '')}",
        "WATCH_FOLDER": str(dirs["watch"]),
        "CWA_INGEST_SERVICE_TEST_MODE": "1",
        "CWA_INGEST_PROCESSING_DIR": str(dirs["processing"]),
        "CWA_INGEST_RECENT_DIR": str(dirs["recent"]),
        "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry_queue"),
        "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
        "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch_dirty"),
        "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch_last_success"),
        "CWA_INGEST_POST_BATCH_CMD": str(post),
        "CWA_INGEST_PROCESSOR_CMD": str(stub),
        "CWA_INGEST_FAILED_DIR": str(dirs["failed"]),
        "PROCESSOR_LOG": str(tmp_path / "processor.log"),
        "PROCESSOR_EXIT_CODE": "0",
    })

    def run(body, **extra_env):
        e = dict(env)
        e.update(extra_env)
        script = f'source "{RUN_SCRIPT}" >/dev/null\n{body}\n'
        return subprocess.run(["bash", "-c", script], env=e, text=True, capture_output=True, timeout=60)

    def invocations():
        log = tmp_path / "processor.log"
        return log.read_text().splitlines() if log.exists() else []

    dirs["queue"] = tmp_path / "retry_queue"
    dirs["run"] = run
    dirs["invocations"] = invocations
    dirs["tmp"] = tmp_path
    return dirs


def test_safety_timeout_moves_file_to_failed_without_overwriting(svc):
    book = svc["watch"] / "book.epub"
    for payload in ("first", "second"):
        book.write_text(payload)
        # Different content each time so the recent-event dedupe doesn't skip it
        res = svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="124")
        assert res.returncode == 0, res.stderr
        assert not book.exists()
    failed = sorted(svc["failed"].iterdir())
    assert len(failed) == 2
    assert sorted(p.read_text() for p in failed) == ["first", "second"]
    assert all("_safety_timeout_book" in p.name for p in failed)


def test_moved_to_failed_gets_fresh_mtime(svc):
    import time
    book = svc["watch"] / "old.epub"
    book.write_text("copied with cp -p")
    os.utime(book, (1_000_000_000, 1_000_000_000))
    svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="124")
    (moved,) = svc["failed"].iterdir()
    assert abs(moved.stat().st_mtime - time.time()) < 60


def test_safety_timeout_leaves_file_when_failed_dir_unusable(svc):
    blocker = svc["tmp"] / "blocker"
    blocker.write_text("x")
    book = svc["watch"] / "book.epub"
    book.write_text("precious")
    res = svc["run"](
        f'handle_event "{book}"',
        PROCESSOR_EXIT_CODE="124",
        CWA_INGEST_FAILED_DIR=str(blocker / "failed"),
    )
    assert book.exists() and book.read_text() == "precious"
    assert "LEAVING" in res.stdout


def test_busy_file_is_retried_without_waiting_for_unrelated_success(svc):
    book = svc["watch"] / "busy.epub"
    book.write_text("busy")
    res = svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="2")
    assert svc["queue"].read_text().splitlines() == [str(book)]
    # Same busy file again doesn't duplicate the queue entry
    book.write_text("busy2")
    svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="2")
    assert svc["queue"].read_text().splitlines() == [str(book)]

    # Service (re)start: the event loop drains the queue before/between events,
    # even when no new file arrives (stdin at EOF here)
    res = svc["run"]("event_loop < /dev/null", PROCESSOR_EXIT_CODE="0")
    assert res.returncode == 0, res.stderr
    assert "Successfully processed retry" in res.stdout
    assert svc["queue"].read_text() == ""


def test_retry_queue_stops_after_busy_and_keeps_remaining(svc):
    paths = []
    for i in range(3):
        p = svc["watch"] / f"b{i}.epub"
        p.write_text(str(i))
        paths.append(str(p))
    svc["queue"].write_text("\n".join(paths) + "\n")
    before = len(svc["invocations"]())
    svc["run"]("process_retry_queue", PROCESSOR_EXIT_CODE="2")
    assert len(svc["invocations"]()) - before == 1
    assert svc["queue"].read_text().splitlines() == paths


def test_queue_trim_logs_dropped_entries(svc):
    existing = [str(svc["watch"] / f"old{i}.epub") for i in range(3)]
    svc["queue"].write_text("\n".join(existing) + "\n")
    book = svc["watch"] / "new.epub"
    book.write_text("new")
    res = svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="2", CWA_INGEST_MAX_QUEUE_SIZE="2")
    assert f"Dropped from retry queue (file left untouched in ingest folder, will not be retried automatically): {existing[0]}" in res.stdout
    assert f"{existing[1]}" in res.stdout
    assert svc["queue"].read_text().splitlines() == [existing[2], str(book)]


def test_busy_file_moves_to_failed_after_attempt_cap(svc):
    book = svc["watch"] / "stuck.epub"
    book.write_text("stuck")
    # First busy result on the initial event queues it (attempt 1 of 3)
    svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="2", CWA_INGEST_MAX_BUSY_ATTEMPTS="3")
    assert svc["queue"].read_text().splitlines() == [str(book)]
    # Attempt 2: still queued
    svc["run"]("process_retry_queue", PROCESSOR_EXIT_CODE="2", CWA_INGEST_MAX_BUSY_ATTEMPTS="3")
    assert svc["queue"].read_text().splitlines() == [str(book)]
    assert book.exists()
    # Attempt 3: cap reached, moved to failed/ with an explanation, not re-queued
    res = svc["run"]("process_retry_queue", PROCESSOR_EXIT_CODE="2", CWA_INGEST_MAX_BUSY_ATTEMPTS="3")
    assert "GIVING UP" in res.stdout and "busy (exit 2) 3 times" in res.stdout
    assert svc["queue"].read_text() == ""
    assert not book.exists()
    failed = list(svc["failed"].iterdir())
    assert len(failed) == 1 and "_busy_retries_exhausted_stuck" in failed[0].name
    assert failed[0].read_text() == "stuck"
    attempts = Path(str(svc["queue"]) + ".attempts")
    assert not attempts.exists() or str(book) not in attempts.read_text()


def test_busy_count_resets_after_success(svc):
    book = svc["watch"] / "flaky.epub"
    book.write_text("x")
    svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="2", CWA_INGEST_MAX_BUSY_ATTEMPTS="2")
    svc["run"]("process_retry_queue", PROCESSOR_EXIT_CODE="0", CWA_INGEST_MAX_BUSY_ATTEMPTS="2")
    attempts = Path(str(svc["queue"]) + ".attempts")
    assert not attempts.exists() or str(book) not in attempts.read_text()
    assert book.exists()  # the stub processor doesn't consume the file


# ── Not ready (exit 3) ──────────────────────────────────────────────────────


def test_not_ready_is_kept_for_retry_not_logged_as_success(svc):
    book = svc["watch"] / "partial.epub"
    book.write_text("half")
    res = svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="3")
    assert "kept for retry" in res.stdout
    assert "Successfully processed" not in res.stdout
    assert book.read_text() == "half"
    assert svc["queue"].read_text().splitlines() == [str(book)]
    assert list(svc["failed"].iterdir()) == []


def test_not_ready_entries_do_not_block_the_rest_of_the_queue(svc):
    paths = []
    for i in range(3):
        p = svc["watch"] / f"n{i}.epub"
        p.write_text(str(i))
        paths.append(str(p))
    svc["queue"].write_text("\n".join(paths) + "\n")
    res = svc["run"]("process_retry_queue", PROCESSOR_EXIT_CODE="3")
    assert svc["invocations"]() == paths
    assert svc["queue"].read_text().splitlines() == paths
    assert res.stdout.count("kept for retry") == 3


def test_not_ready_file_unchanged_past_timeout_moves_to_failed(svc):
    book = svc["watch"] / "stalled.epub"
    book.write_text("stalled copy")
    res = svc["run"](
        f'handle_event "{book}"; sleep 1.2; process_retry_queue',
        PROCESSOR_EXIT_CODE="3", CWA_INGEST_NOT_READY_TIMEOUT="1",
    )
    assert "GIVING UP" in res.stdout and "incomplete and unchanged" in res.stdout
    assert not book.exists()
    (moved,) = svc["failed"].iterdir()
    assert "_incomplete_timeout_stalled" in moved.name and moved.read_text() == "stalled copy"
    assert svc["queue"].read_text() == ""


def test_not_ready_file_that_keeps_changing_is_never_moved(svc):
    book = svc["watch"] / "slow.epub"
    book.write_text("a")
    res = svc["run"](
        f'handle_event "{book}"; sleep 1.2; printf more >> "{book}"; process_retry_queue',
        PROCESSOR_EXIT_CODE="3", CWA_INGEST_NOT_READY_TIMEOUT="1",
    )
    assert "GIVING UP" not in res.stdout
    assert book.exists() and list(svc["failed"].iterdir()) == []
    assert svc["queue"].read_text().splitlines() == [str(book)]


def test_not_ready_then_vanished_is_not_queued(svc):
    book = svc["watch"] / "gone.epub"
    book.write_text("x")
    res = svc["run"](f'handle_event "{book}"', PROCESSOR_EXIT_CODE="3", PROCESSOR_DELETE="1")
    assert "vanished before it could be imported" in res.stdout
    assert "Successfully processed" not in res.stdout
    assert svc["queue"].read_text() == ""
