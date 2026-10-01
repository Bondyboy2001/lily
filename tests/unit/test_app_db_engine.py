# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Background-task sessions share the one app.db engine instead of creating (and leaking)
an engine, a connection pool and an atexit hook per task run."""

import atexit
import threading

import pytest

from tests.unit.lily_env import lily_env

pytestmark = pytest.mark.unit


def test_task_sessions_share_one_engine_and_return_their_connections(tmp_path, monkeypatch):
    from cps import ub
    from cps.tasks.clean import TaskClean

    with lily_env(tmp_path):
        engine = ub.session.get_bind()
        hooks = []
        monkeypatch.setattr(atexit, "register", lambda *a, **k: hooks.append(a))
        engines = set()
        for _ in range(25):
            registry = ub.get_new_session_instance()
            registry.query(ub.User).count()
            engines.add(id(registry().get_bind()))
            registry.remove()
        assert engines == {id(engine)}
        assert hooks == []
        assert engine.pool.checkedout() == 0

        # init_db_thread() hands out sessions on the same engine too
        thread_session = ub.init_db_thread()
        assert thread_session.get_bind() is engine
        thread_session.close()

        # a task run from the worker thread closes its session when it finishes
        task = TaskClean()
        worker = threading.Thread(target=task.run, args=(None,))
        worker.start()
        worker.join()
        assert engine.pool.checkedout() == 0
