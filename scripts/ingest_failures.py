# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recording why the ingest pipeline rejected a file.

Rejected sources are moved to <processed_books>/failed as '<timestamp>_<name>';
write_failure() leaves a hidden '.<name>.failure.json' next to each one with the
reason. Kept free of Flask/cps imports so it can be tested on its own.
"""

import json
import os


def _sidecar_path(path: str) -> str:
    return os.path.join(os.path.dirname(path),
                        "." + os.path.basename(path) + ".failure.json")


def write_failure(path: str, reason: str, job_id: str | None = None) -> None:
    """Atomically record why `path` (a file already inside failed/) was rejected."""
    try:
        payload = {"reason": " ".join(str(reason).split())[:2000]
                   or "Import failed; check logs",
                   "job_id": job_id or ""}
        target = _sidecar_path(path)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, target)
    except OSError:
        pass
