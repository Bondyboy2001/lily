# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Task that mirrors new and changed book files to the configured second folder."""

import sys

from flask_babel import lazy_gettext as N_

from cps import config, logger
from cps.services.worker import CalibreTask

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from library_mirror import mirror_library, MirrorError


def get_mirror_dir() -> str:
    """cwa_settings.library_mirror_dir; '' means the mirror is off."""
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            value = cwa_db.cwa_settings.get("library_mirror_dir") or ""
    except Exception:
        return ""
    if isinstance(value, list):
        value = ",".join(value)
    return str(value).strip()


class TaskMirrorLibrary(CalibreTask):
    """Copies new and changed book files and covers into the configured mirror folder."""

    def __init__(self, task_message=N_('Mirroring library files')):
        super(TaskMirrorLibrary, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        dest = get_mirror_dir()
        if not dest:
            self._handleSuccess()  # not configured: nothing to do
            return

        def _progress(done, total):
            self.progress = done / total if total else 1

        try:
            result = mirror_library(config.config_calibre_dir, dest, _progress)
        except MirrorError as e:
            self.log.error("Library mirror failed: %s", e)
            self._handleError(str(e))
            return
        self.log.info("Library mirror: copied %d file(s), %d MB, %d error(s)",
                      result["copied"], result["bytes"] // 2**20, len(result["errors"]))
        for rel, err in list(result["errors"].items())[:20]:
            self.log.error("Library mirror could not copy %s: %s", rel, err)
        if result["errors"]:
            self._handleError("Mirrored %d file(s); %d failed (see the log)" % (result["copied"], len(result["errors"])))
        else:
            self.message = N_('Mirrored %(n)d file(s)', n=result["copied"])
            self._handleSuccess()

    @property
    def name(self):
        return "Mirror Library Files"

    @property
    def is_cancellable(self):
        return False
