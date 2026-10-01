# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""A hung task or converter must not block the background worker forever."""

import sys
import threading
import time
from datetime import datetime

import pytest

from cps.services import worker as worker_mod
from cps.services.worker import (CalibreTask, QueuedTask, WorkerThread, STAT_FAIL, STAT_FINISH_SUCCESS,
                                 STAT_STARTED)
from cps.subproc_wrapper import ProcessTimeout, drain_in_background, process_open

pytestmark = pytest.mark.unit


class Blocking(CalibreTask):
    name = "Blocking"
    is_cancellable = False

    def __init__(self):
        super().__init__("blocking")
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, worker_thread):
        self.started.set()
        self.release.wait(10)
        self.progress = 0.5
        self._handleSuccess()


class Quick(CalibreTask):
    name = "Quick"
    is_cancellable = False

    def run(self, worker_thread):
        self._handleSuccess()


def _queue(worker, task, num):
    worker.queue.put(QueuedTask(num=num, user="test", added=datetime.now(), task=task, hidden=False))


@pytest.fixture
def worker():
    w = WorkerThread(watchdog_interval=3600)  # checks are triggered by hand
    yield w
    w.stop()


def test_watchdog_fails_hung_task_and_queue_moves_on(worker, monkeypatch):
    recorded = []
    monkeypatch.setattr(worker_mod, "_record_job", lambda *a: recorded.append(a))
    blocker, quick = Blocking(), Quick("quick")
    blocker.job_name = "db_backup"
    _queue(worker, blocker, 1)
    assert blocker.started.wait(5)
    _queue(worker, quick, 2)

    assert worker.check_watchdog() is None  # within the limit: nothing happens
    abandoned = worker.check_watchdog(now=time.monotonic() + 6 * 3600 + 1)
    assert abandoned.task is blocker
    assert blocker.stat == STAT_FAIL and "did not finish" in blocker.error and blocker.end_time
    assert ("db_backup", "error", blocker.error) in recorded

    assert quick.done_event.wait(5), "the next task did not run"
    assert quick.stat == STAT_FINISH_SUCCESS

    # The stuck thread finally returns: it can't flip the result back to success
    blocker.release.set()
    time.sleep(0.2)
    assert blocker.stat == STAT_FAIL and blocker.progress == 1
    assert ("db_backup", "success") not in recorded
    assert worker.check_watchdog(now=time.monotonic() + 10**6) is None  # nothing running now


def test_watchdog_respects_task_limit_and_can_be_disabled(worker, monkeypatch):
    blocker = Blocking()
    blocker.max_runtime_hours = 24
    _queue(worker, blocker, 1)
    assert blocker.started.wait(5)
    try:
        assert worker.check_watchdog(now=time.monotonic() + 7 * 3600) is None
        monkeypatch.setenv(worker_mod.TASK_TIMEOUT_ENV, "0")
        assert worker.check_watchdog(now=time.monotonic() + 10**7) is None
        assert blocker.stat == STAT_STARTED
    finally:
        blocker.release.set()


@pytest.mark.parametrize("raw,expected", [("", 6.0), ("2.5", 2.5), ("0", 0.0), ("-1", 0.0), ("soon", 6.0)])
def test_task_timeout_hours(monkeypatch, raw, expected):
    monkeypatch.setenv(worker_mod.TASK_TIMEOUT_ENV, raw)
    assert worker_mod.task_timeout_hours() == expected


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_process_timeout_kills_a_silent_process():
    p = process_open([sys.executable, "-c", "import time; time.sleep(30)"], newlines=False, new_session=True)
    started = time.monotonic()
    with ProcessTimeout(p, 0.3, kill_group=True) as watchdog:
        while p.poll() is None:
            p.stdout.readline()  # blocks until the process is killed
    assert watchdog.timed_out and time.monotonic() - started < 10
    assert p.returncode != 0


def test_process_timeout_leaves_a_finished_process_alone():
    p = process_open([sys.executable, "-c", "print('ok')"])
    with ProcessTimeout(p, 30) as watchdog:
        out = p.stdout.read()
        p.wait()
    assert out.strip() == "ok" and not watchdog.timed_out and p.returncode == 0
    watchdog._kill()  # firing after exit is a no-op
    assert not watchdog.timed_out


def test_stderr_is_drained_so_a_chatty_process_does_not_block():
    script = "import sys; sys.stderr.write('x' * 400000); print('done')"
    p = process_open([sys.executable, "-c", script], newlines=False)
    thread, err = drain_in_background(p.stderr)
    with ProcessTimeout(p, 20) as watchdog:
        out = p.stdout.read()
        p.wait()
    thread.join(5)
    assert not watchdog.timed_out and out.strip() == b"done"
    assert sum(len(line) for line in err) == 400000


@pytest.mark.skipif(sys.platform == "win32", reason="shell script converter")
def test_hanging_ebook_convert_is_killed(tmp_path, monkeypatch):
    from cps import config, helper  # noqa: F401
    from cps.tasks import convert
    fake = tmp_path / "ebook-convert"
    # Writes a progress line, lots of stderr, then hangs (a child process keeps the pipe open too)
    fake.write_text("#!/bin/sh\necho '10% working'\nhead -c 300000 /dev/zero >&2\nsleep 60 &\nsleep 60\n")
    fake.chmod(0o755)
    (tmp_path / "book.epub").write_bytes(b"x")
    for name, value in (("config_converterpath", str(fake)), ("config_embed_metadata", False),
                        ("config_calibre", ""), ("config_use_google_drive", False)):
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(convert, "convert_timeout", lambda path: 1)
    task = convert.TaskConvert(str(tmp_path / "book"), 1, "convert", {"old_book_format": "EPUB",
                                                                     "new_book_format": "MOBI"})
    started = time.monotonic()
    check, message = task._convert_calibre(str(tmp_path / "book"), ".epub", ".mobi", False)
    assert check == 1 and "was stopped" in str(message)
    assert time.monotonic() - started < 15
    assert task.progress == 0.1


def test_convert_timeout_scales_with_size(tmp_path):
    from cps import helper  # noqa: F401  (imports cps.tasks.convert in the order the app does)
    from cps.tasks import convert
    assert convert.convert_timeout(str(tmp_path / "missing.epub")) == convert.CONVERT_TIMEOUT_BASE
    book = tmp_path / "b.pdf"
    book.write_bytes(b"\0" * (5 * 2**20))
    assert convert.convert_timeout(str(book)) == convert.CONVERT_TIMEOUT_BASE + 5 * convert.CONVERT_TIMEOUT_PER_MB
    big = tmp_path / "huge.pdf"
    with open(big, "wb") as f:
        f.truncate(10 * 2**30)  # sparse
    assert convert.convert_timeout(str(big)) == convert.CONVERT_TIMEOUT_MAX
