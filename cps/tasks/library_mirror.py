# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Task that mirrors new and changed book files to the configured second folder."""

import sys

from flask_babel import lazy_gettext as N_

from cps import config, logger
from cps.services.worker import CalibreTask

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from library_mirror import mirror_library, normalize_version_days, MirrorError, DEFAULT_VERSION_DAYS


def _setting(name: str) -> str:
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            value = cwa_db.cwa_settings.get(name) or ""
    except Exception:
        return ""
    if isinstance(value, list):
        value = ",".join(value)
    return str(value).strip()


def get_mirror_dir() -> str:
    """cwa_settings.library_mirror_dir; '' means the mirror is off."""
    return _setting("library_mirror_dir")


def get_version_days() -> int:
    """Days replaced mirror copies are kept in <mirror>/.versions (0 = forever)."""
    return normalize_version_days(_setting("library_mirror_version_days"), DEFAULT_VERSION_DAYS)


class TaskMirrorLibrary(CalibreTask):
    """Copies new and changed book files and covers into the configured mirror folder."""

    job_name = "library_mirror"
    # A first mirror of a large library onto a slow disk can legitimately take many hours
    max_runtime_hours = 24

    def __init__(self, task_message=N_('Mirroring library files')):
        super(TaskMirrorLibrary, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        dest = get_mirror_dir()
        if not dest:
            self.job_name = None  # not configured: nothing to do, and nothing to record
            self._handleSuccess()
            return

        def _progress(done, total):
            self.progress = done / total if total else 1

        try:
            result = mirror_library(config.config_calibre_dir, dest, _progress, version_days=get_version_days())
        except MirrorError as e:
            self.log.error("Library mirror failed: %s", e)
            self._handleError(str(e))
            return
        self.log.info("Library mirror: copied %d file(s), %d MB, kept %d previous version(s), "
                      "%d suspicious, %d error(s), pruned %d version folder(s)",
                      result["copied"], result["bytes"] // 2**20, result["versioned"], len(result["suspicious"]),
                      len(result["errors"]), len(result["pruned_versions"]))
        for rel, err in list(result["errors"].items())[:20]:
            self.log.error("Library mirror could not copy %s: %s", rel, err)
        for rel, reason in list(result["suspicious"].items())[:20]:
            self.log.warning("Library mirror kept the old copy of %s: the new file looks damaged (%s). "
                             "If the change is intended, move the mirror copy away and run the mirror again.",
                             rel, reason)
        problems = []
        if result["errors"]:
            problems.append("%d failed" % len(result["errors"]))
        if result["suspicious"]:
            problems.append("%d suspicious file(s) not replaced" % len(result["suspicious"]))
        if problems:
            self._handleError("Mirrored %d file(s); %s (see the log)" % (result["copied"], ", ".join(problems)))
        else:
            self.message = N_('Mirrored %(n)d file(s)', n=result["copied"])
            self._handleSuccess()

    @property
    def name(self):
        return "Mirror Library Files"

    @property
    def is_cancellable(self):
        return False
