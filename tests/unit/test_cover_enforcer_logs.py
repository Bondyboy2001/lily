"""cover_enforcer.py takes the change logs left waiting behind its lock (a metadata rebuild
writes one a second, and each one's own run is cancelled while another holds the lock)."""
import importlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def enforcer_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # where the module takes its lock
    monkeypatch.delitem(sys.modules, "cover_enforcer", raising=False)
    module = importlib.import_module("cover_enforcer")
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(module, "change_logs_dir", str(logs))
    yield module, logs
    module.atexit.unregister(module.removeLock)
    module.removeLock()


@pytest.mark.unit
def test_logs_written_during_a_pass_get_the_next_pass(enforcer_module):
    module, logs = enforcer_module
    (logs / "20261002100000-1.json").write_text("{}")
    passes = []

    def process(log_files, processed):
        passes.append(sorted(Path(p).name for p in log_files))
        for p in log_files:
            Path(p).unlink()
        if len(passes) == 1:  # arrives while the first pass runs
            (logs / "20261002100001-2.json").write_text("{}")

    fake = SimpleNamespace(_process_logs=process)
    module.Enforcer.check_for_other_logs(fake, processed_book_ids={"9"})
    assert passes == [["20261002100000-1.json"], ["20261002100001-2.json"]]


@pytest.mark.unit
def test_a_log_that_cannot_be_deleted_is_read_once(enforcer_module):
    module, logs = enforcer_module
    (logs / "20261002100000-1.json").write_text("{}")
    passes = []
    fake = SimpleNamespace(_process_logs=lambda files, processed: passes.append(files))
    module.Enforcer.check_for_other_logs(fake)
    assert len(passes) == 1
