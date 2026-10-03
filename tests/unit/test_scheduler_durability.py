# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Scheduler durability: job setup failures are logged, jobs get sane misfire defaults,
and the worker compares task states by value."""

import inspect
import re
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit


def _scheduler_with_mock(monkeypatch):
    from cps.services import background_scheduler as bs
    monkeypatch.setattr(bs, "use_APScheduler", True)
    instance = object.__new__(bs.BackgroundScheduler)
    instance.scheduler = Mock()
    return bs, instance


def test_schedule_passes_durability_defaults_to_apscheduler(monkeypatch):
    bs, instance = _scheduler_with_mock(monkeypatch)
    bs.BackgroundScheduler.schedule(instance, func=Mock(), trigger=Mock(), name="probe")
    _, kwargs = instance.scheduler.add_job.call_args
    assert kwargs["misfire_grace_time"] == 3600
    assert kwargs["coalesce"] is True
    assert kwargs["max_instances"] == 1
    assert kwargs["name"] == "probe"


def test_schedule_caller_can_override_the_defaults(monkeypatch):
    bs, instance = _scheduler_with_mock(monkeypatch)
    bs.BackgroundScheduler.schedule(instance, func=Mock(), trigger=Mock(), max_instances=3)
    _, kwargs = instance.scheduler.add_job.call_args
    assert kwargs["max_instances"] == 3


def test_a_job_that_fails_to_schedule_is_logged(monkeypatch):
    from cps import schedule
    log = Mock()
    monkeypatch.setattr(schedule, "log", log)
    scheduler = Mock()
    scheduler.schedule_task.side_effect = RuntimeError("boom")
    schedule._schedule_db_backup(scheduler, 4, None)  # must not raise
    assert log.exception.called
    assert "job setup failed" in log.exception.call_args.args[0]


def test_no_scheduler_error_is_silently_swallowed():
    from cps import schedule
    src = inspect.getsource(schedule)
    for match in re.finditer(r"except Exception(?: as \w+)?:", src):
        handler = src[match.end():match.end() + 250]
        assert re.search(r"log\.(exception|warning|error)", handler), handler


def test_waiting_check_uses_equality():
    import cps.services.worker as worker
    src = inspect.getsource(worker.WorkerThread.run)
    assert "is STAT_WAITING" not in src
    assert "== STAT_WAITING" in src
