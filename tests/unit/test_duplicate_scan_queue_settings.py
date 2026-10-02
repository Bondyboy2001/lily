# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

from types import ModuleType, SimpleNamespace
import importlib.util
import pathlib
import sys
import pytest


@pytest.fixture(autouse=True)
def _isolate_sys_modules(isolated_sys_modules):
    """Every test here writes stubs into sys.modules; undo them afterwards."""
    yield


def _install_stub(name, attrs=None):
    module = ModuleType(name)
    if attrs:
        for key, value in attrs.items():
            setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Blueprint:
    def __init__(self, *args, **kwargs):
        return None

    def route(self, *args, **kwargs):
        return lambda fn: fn

    def app_template_global(self, *args, **kwargs):
        return lambda fn: fn


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _SettingsCwaDB:
    instances = []
    default_settings = {
        "duplicate_detection_title": 1,
        "duplicate_detection_author": 1,
        "duplicate_detection_language": 0,
        "duplicate_detection_series": 0,
        "duplicate_detection_publisher": 0,
        "duplicate_detection_format": 0,
        "duplicate_scan_enabled": 1,
        "duplicate_scan_frequency": "after_import",
        "duplicate_scan_debounce_seconds": 60,
    }

    def __init__(self):
        self.cwa_default_settings = dict(self.default_settings)
        self.cwa_settings = dict(self.default_settings)
        self.updated_settings = None
        self.__class__.instances.append(self)

    def update_cwa_settings(self, result):
        self.updated_settings = dict(result)
        self.cwa_settings.update(result)

    def get_cwa_settings(self):
        return dict(self.cwa_settings)

    def set_default_settings(self, force=False):
        self.cwa_settings = dict(self.cwa_default_settings)


def _clear_modules():
    for name in list(sys.modules):
        if name == "cps" or name.startswith("cps.") or name == "cwa_db":
            sys.modules.pop(name, None)


def _load_cwa_functions(monkeypatch, request):
    _clear_modules()
    cps = _install_stub("cps")
    for name in ("config", "constants", "csrf", "helper", "ub", "calibre_db"):
        module = _install_stub(f"cps.{name}")
        setattr(cps, name, module)
    cps.config.save = lambda: None
    cps.helper.get_internal_api_url = lambda path: f"http://localhost{path}"
    cps.logger = _install_stub("cps.logger", {"create": lambda: _Logger()})
    cps.csrf.exempt = lambda fn: fn

    _install_stub(
        "cps.usermanagement",
        {"login_required_if_no_ano": lambda fn: fn, "user_login_required": lambda fn: fn},
    )
    _install_stub("cps.admin", {"admin_required": lambda fn: fn})
    _install_stub("cps.internal_api", {"internal_only": lambda fn: fn})
    _install_stub("cps.render_template", {"render_title_template": lambda *args, **kwargs: {"template": args[0]}})
    _install_stub("cps.cw_login", {"current_user": SimpleNamespace(id=7), "login_user": None, "logout_user": None})
    _install_stub("cps.web", {"cwa_get_num_books_in_library": lambda: 0})
    _install_stub("cps.services")
    _install_stub("cps.services.background_scheduler", {"BackgroundScheduler": lambda: None, "DateTrigger": object})
    worker_module = _install_stub(
        "cps.services.worker",
        {
            "WorkerThread": SimpleNamespace(add=lambda *args, **kwargs: None),
            "STAT_FINISH_SUCCESS": 3,
            "STAT_FAIL": 1,
            "STAT_ENDED": 4,
            "STAT_CANCELLED": 5,
        },
    )
    _install_stub("cps.tasks")
    _install_stub("cps.tasks.database", {"TaskReconnectDatabase": object})
    _install_stub("cps.tasks.auto_send", {"TaskAutoSend": object})
    _install_stub("cwa_db", {"CWA_DB": _SettingsCwaDB})
    _install_stub(
        "flask",
        {
            "Blueprint": _Blueprint,
            "redirect": lambda *args, **kwargs: None,
            "flash": lambda *args, **kwargs: None,
            "url_for": lambda endpoint, **kwargs: endpoint,
            "request": request,
            "send_from_directory": lambda *args, **kwargs: None,
            "abort": lambda *args, **kwargs: None,
            "make_response": lambda *args, **kwargs: None,
            "jsonify": lambda payload=None, **kwargs: payload if payload is not None else kwargs,
            "current_app": SimpleNamespace(config={}),
        },
    )
    _install_stub(
        "flask_babel",
        {
            "gettext": lambda text, **kwargs: text % kwargs if kwargs else text,
            "lazy_gettext": lambda text, **kwargs: text,
        },
    )
    _install_stub(
        "cps.duplicate_index",
        {
            "get_criteria_fingerprint": lambda settings: tuple(
                int(settings.get(key, 0))
                for key in (
                    "duplicate_detection_title",
                    "duplicate_detection_author",
                    "duplicate_detection_language",
                    "duplicate_detection_series",
                    "duplicate_detection_publisher",
                    "duplicate_detection_format",
                )
            ),
            "mark_duplicate_index_pending": lambda reason=None: pending_reasons.append(reason) or True,
        },
    )

    # cps.cwa_functions is a package; its submodules are imported through its __path__.
    package_dir = pathlib.Path(__file__).resolve().parents[2] / "cps" / "cwa_functions"
    spec = importlib.util.spec_from_file_location(
        "cps.cwa_functions", package_dir / "__init__.py", submodule_search_locations=[str(package_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.cwa_functions"] = module
    spec.loader.exec_module(module)
    module.ingest.WorkerThread = worker_module.WorkerThread
    return module


pending_reasons = []


def test_internal_duplicate_queue_passes_coalesced_book_ids(monkeypatch):
    added_tasks = []
    timers = []

    class _TaskDuplicateScan:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _ImmediateTimer:
        def __init__(self, delay, func):
            self.delay = delay
            self.func = func
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    request = SimpleNamespace(
        headers={},
        remote_addr="127.0.0.1",
        get_json=lambda force=True, silent=True: {"delay_seconds": 5, "book_ids": [4, "5", 4]},
    )
    module = _load_cwa_functions(monkeypatch, request)
    monkeypatch.setattr(module.ingest, "Timer", _ImmediateTimer)
    module.ingest.WorkerThread.add = lambda username, task, hidden=False: added_tasks.append((username, task, hidden))
    _install_stub("cps.tasks.duplicate_scan", {"TaskDuplicateScan": _TaskDuplicateScan})

    response, status = module.cwa_internal_queue_duplicate_scan()
    timers[0].func()

    assert status == 200
    assert response["queued"] is True
    assert added_tasks[0][1].kwargs["book_ids"] == [4, 5]
    assert added_tasks[0][1].kwargs["include_new_books"] is False


def test_an_import_and_an_edit_in_one_debounce_window_scan_both(monkeypatch):
    # The import asks for new books by sending no ids; the edit sends its own.
    # Before, the edit's id replaced the import's request and new books went unscanned.
    added_tasks = []
    timers = []

    class _TaskDuplicateScan:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _ManualTimer:
        def __init__(self, delay, func):
            self.func = func
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    payloads = iter([{"delay_seconds": 5, "book_ids": []}, {"delay_seconds": 5, "book_ids": [9]}])
    request = SimpleNamespace(headers={}, remote_addr="127.0.0.1",
                              get_json=lambda force=True, silent=True: next(payloads))
    module = _load_cwa_functions(monkeypatch, request)
    monkeypatch.setattr(module.ingest, "Timer", _ManualTimer)
    module.ingest.WorkerThread.add = lambda username, task, hidden=False: added_tasks.append(task)
    _install_stub("cps.tasks.duplicate_scan", {"TaskDuplicateScan": _TaskDuplicateScan})

    module.cwa_internal_queue_duplicate_scan()
    module.cwa_internal_queue_duplicate_scan()
    timers[-1].func()

    assert added_tasks[0].kwargs["book_ids"] == [9]
    assert added_tasks[0].kwargs["include_new_books"] is True


def test_internal_duplicate_queue_defaults_to_sixty_second_debounce(monkeypatch):
    timers = []

    class _ImmediateTimer:
        def __init__(self, delay, func):
            self.delay = delay
            self.func = func
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    request = SimpleNamespace(
        headers={},
        remote_addr="127.0.0.1",
        get_json=lambda force=True, silent=True: {},
    )
    module = _load_cwa_functions(monkeypatch, request)
    monkeypatch.setattr(module.ingest, "Timer", _ImmediateTimer)

    response, status = module.cwa_internal_queue_duplicate_scan()

    assert status == 200
    assert response["delay_seconds"] == 60
    assert timers[0].delay == 60


def test_direct_duplicate_queue_helper_defaults_to_settings(monkeypatch):
    timers = []

    class _ImmediateTimer:
        def __init__(self, delay, func):
            self.delay = delay
            self.func = func
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    request = SimpleNamespace(headers={}, remote_addr="127.0.0.1", get_json=lambda force=True, silent=True: {})
    module = _load_cwa_functions(monkeypatch, request)
    monkeypatch.setattr(module.ingest, "Timer", _ImmediateTimer)

    response = module.queue_debounced_duplicate_scan(book_ids=[9])

    assert response == {"success": True, "queued": True, "delay_seconds": 60}
    assert timers[0].delay == 60


def test_cwa_settings_saves_only_the_fields_the_page_shows(monkeypatch):
    request = SimpleNamespace(
        method="POST",
        form={"auto_metadata_fetch_enabled": "on", "auto_ingest_automerge": "overwrite",
              "config_google_books_api_key": " abc ", "config_uploading": "on"},
    )
    module = _load_cwa_functions(monkeypatch, request)
    _SettingsCwaDB.instances = []

    module.set_cwa_settings()

    saved = _SettingsCwaDB.instances[0].updated_settings
    assert saved == {
        "auto_metadata_fetch_enabled": 1,
        "auto_metadata_enforcement": 0,
        "auto_ingest_automerge": "overwrite",
    }
    # Settings the page no longer shows are never written.
    assert "duplicate_detection_enabled" not in saved and "hardcover_auto_fetch_enabled" not in saved
    config = sys.modules["cps.config"]
    assert config.config_google_books_api_key == "abc" and config.config_uploading == 1


def test_cwa_settings_ignores_an_unknown_merge_option(monkeypatch):
    request = SimpleNamespace(method="POST", form={"auto_ingest_automerge": "drop_table"})
    module = _load_cwa_functions(monkeypatch, request)
    _SettingsCwaDB.instances = []

    module.set_cwa_settings()

    assert "auto_ingest_automerge" not in _SettingsCwaDB.instances[0].updated_settings
