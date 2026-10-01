"""The image keeps the app and the s6 scripts owned by root; abc (the services' user) may
write only a few listed directories under the app."""

import re

import pytest

from tests.unit.lily_env import REPO

pytestmark = pytest.mark.unit

WRITABLE = {"/app/calibre-web-automated/metadata_change_logs", "/app/calibre-web-automated/metadata_temp",
            "/app/calibre-web-automated/cps/cache"}


def test_app_is_copied_as_root():
    dockerfile = (REPO / "Dockerfile").read_text()
    copy = re.search(r"^COPY .*\. /app/calibre-web-automated/\s*$", dockerfile, re.M).group(0)
    assert "--chown" not in copy


def test_setup_script_keeps_code_and_s6_scripts_root_owned():
    setup = (REPO / "scripts" / "setup-cwa.sh").read_text()
    assert "chown -R abc:abc /etc/s6-overlay" not in setup
    assert "chown -R root:root /etc/s6-overlay" in setup
    assert "chmod -R go-w /app/calibre-web-automated" in setup
    assert "chmod 775" not in setup
    listed = set(re.search(r"APP_WRITABLE_DIRS=\(([^)]*)\)", setup).group(1).split())
    assert listed == WRITABLE


def test_init_service_reowns_only_the_writable_dirs():
    init = (REPO / "root" / "etc" / "s6-overlay" / "s6-rc.d" / "cwa-init" / "run").read_text()
    block = re.search(r"declare -a requiredDirs=\(([^)]*)\)", init).group(1)
    dirs = set(re.findall(r'"([^"]+)"', block))
    assert "/app/calibre-web-automated" not in dirs
    assert WRITABLE <= dirs
