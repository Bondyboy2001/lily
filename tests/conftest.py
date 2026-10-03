# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared pytest fixtures and configuration for Lily tests.

The integration fixtures bind-mount temp folders into a test container. Where the
container can't see them (a remote Docker daemon), files go in with `docker cp`.
"""

import json
import os
import sys
import pytest
import shutil
import time
import subprocess
from pathlib import Path
from typing import Generator

# Add project root to Python path so 'cps' module can be imported
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Add scripts directory to Python path so 'cwa_db' module can be imported
scripts_dir = project_root / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


AUTO_DOCKER_VOLUMES = False  # Set when bind mounts are not visible to the container: use docker cp


def _get_test_uid_gid() -> tuple[str, str]:
    """Return UID/GID strings for test containers.

    Uses explicit overrides when provided, otherwise falls back to host uid/gid
    on POSIX systems, or 1000/1000 as a default.
    """
    env_uid = os.getenv("CWA_TEST_PUID", "").strip()
    env_gid = os.getenv("CWA_TEST_PGID", "").strip()
    if env_uid and env_gid:
        return env_uid, env_gid

    if os.name == "posix" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        return str(os.getuid()), str(os.getgid())

    return "1000", "1000"

class DockerPath:
    """Represents a path inside a running docker container for auto-fallback mode."""
    def __init__(self, container: str, container_path: str):
        self.container = container
        self.container_path = container_path

    def __truediv__(self, name: str):
        return DockerPath(self.container, f"{self.container_path.rstrip('/')}/{name}")

    def exists(self) -> bool:
        try:
            res = subprocess.run(
                ["docker", "exec", self.container, "test", "-e", self.container_path],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return res.returncode == 0
        except Exception:
            return False

    def is_dir(self) -> bool:
        try:
            res = subprocess.run(
                ["docker", "exec", self.container, "test", "-d", self.container_path],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return res.returncode == 0
        except Exception:
            return False

    @property
    def name(self) -> str:
        return os.path.basename(self.container_path.rstrip('/'))

    def __str__(self) -> str:
        return self.container_path

    @property
    def _parent(self) -> str:
        return os.path.dirname(self.container_path.rstrip('/')) or '/'

    def iterdir(self):
        """Iterate immediate children of this directory inside the container."""
        try:
            res = subprocess.run(
                [
                    "docker", "exec", self.container, "sh", "-lc",
                    f"ls -1A {self.container_path} 2>/dev/null"
                ],
                check=False, capture_output=True, text=True
            )
            if res.returncode != 0:
                return iter(())
            entries = [e for e in res.stdout.splitlines() if e.strip()]
            for name in entries:
                yield DockerPath(self.container, f"{self.container_path.rstrip('/')}/{name}")
        except Exception:
            return iter(())

    def glob(self, pattern: str):
        """Yield paths matching the glob pattern under this directory."""
        # Use busybox/ash globbing via sh -lc to expand matches
        try:
            res = subprocess.run(
                [
                    "docker", "exec", self.container, "sh", "-lc",
                    f"set -o noglob; for f in {self.container_path.rstrip('/')}/{pattern}; do echo \"$f\"; done"
                ],
                check=False, capture_output=True, text=True
            )
            if res.returncode != 0:
                return iter(())
            for line in res.stdout.splitlines():
                p = line.strip()
                if p:
                    yield DockerPath(self.container, p)
        except Exception:
            return iter(())


def volume_copy(src, dest):
    """Copy file into ingest folder.

    - In normal bind mode: shutil.copy2 to host path
    - In auto-fallback mode and dest is DockerPath: docker cp to container
    """
    if AUTO_DOCKER_VOLUMES and isinstance(dest, DockerPath):
        # Ensure parent exists inside container (best-effort)
        parent = os.path.dirname(dest.container_path)
        subprocess.run(["docker", "exec", dest.container, "mkdir", "-p", parent], check=False)
        # docker cp requires <src> <container>:<path>
        target = f"{dest.container}:{dest.container_path}"
        # docker cp copies into existing directory; ensure parent exists and target filename honored
        return subprocess.run(["docker", "cp", str(src), target], check=True)
    else:
        return shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# sys.modules isolation
# ---------------------------------------------------------------------------
# Several unit tests load a single production module (e.g. cps/duplicates.py)
# against hand-written stubs for ``cps``, ``flask``, ``sqlalchemy``, ``cwa_db``
# and friends by writing them straight into ``sys.modules``. Left in place,
# those stubs leak into every test that runs afterwards (``'cps' is not a
# package``, broken SQLAlchemy imports, ...), so the suite only passed when
# files were run one at a time. ``isolated_sys_modules`` snapshots
# ``sys.modules`` and puts it back exactly as it was once the test finishes.

def _is_stub_module(module) -> bool:
    """True for modules that were not loaded from a real file (test stubs/mocks)."""
    module_file = getattr(module, "__file__", None)
    return not isinstance(module_file, str)


def _module_root(name: str) -> str:
    return name.partition(".")[0]


def restore_sys_modules(snapshot: dict) -> None:
    """Restore ``sys.modules`` to ``snapshot``.

    * Entries the test replaced or removed are put back.
    * New entries are dropped when they belong to a package tree the test
      tampered with (stubbed/replaced/removed anything under the same root),
      because they may have been executed against stubs. Genuine third-party
      or stdlib modules imported as a side effect under untouched roots are
      kept, so C extensions are never imported twice.
    * Attributes on parent packages that point at removed/replaced
      submodules are repaired so ``import a.b`` and ``a.b`` agree again.
    """
    current = sys.modules
    tainted_roots = set()
    for name, module in list(current.items()):
        if name in snapshot:
            if snapshot[name] is not module:
                tainted_roots.add(_module_root(name))
        elif _is_stub_module(module):
            tainted_roots.add(_module_root(name))
    for name in snapshot:
        if name not in current:
            tainted_roots.add(_module_root(name))

    if not tainted_roots:
        return

    touched = []  # (name, module that the test left behind)
    for name in list(current):
        if name not in snapshot and _module_root(name) in tainted_roots:
            touched.append((name, current.pop(name)))
    for name, module in snapshot.items():
        if current.get(name) is not module:
            touched.append((name, current.get(name)))
            current[name] = module

    for name, leftover in touched:
        parent_name, _, child = name.rpartition(".")
        if not parent_name or leftover is None:
            continue
        parent = snapshot.get(parent_name)
        if parent is None or _is_stub_module(parent):
            continue
        # Only touch the parent attribute when it still points at the module
        # the test left behind; never clobber ordinary attributes (e.g. the
        # real ``cps.calibre_db`` object when a test stubbed a module by that name).
        if getattr(parent, "__dict__", {}).get(child) is not leftover:
            continue
        try:
            if name in snapshot:
                setattr(parent, child, snapshot[name])
            else:
                delattr(parent, child)
        except (AttributeError, TypeError):
            pass


@pytest.fixture
def isolated_sys_modules():
    """Snapshot ``sys.modules`` for the duration of one test and restore it after.

    Use it (typically via a module-level autouse fixture) in any test that
    writes stubs into ``sys.modules``.
    """
    snapshot = dict(sys.modules)
    try:
        yield
    finally:
        restore_sys_modules(snapshot)


def get_db_path(db_path, tmp_path=None):
    """A local path to read a test container's database from.

    A bind-mounted path is returned as is; a DockerPath (docker cp mode) is copied
    out into tmp_path first.
    """
    # Auto-fallback mode: copy out from container path
    if AUTO_DOCKER_VOLUMES and isinstance(db_path, DockerPath):
        if tmp_path is None:
            raise ValueError("tmp_path required to copy a database out of the container")
        local_path = tmp_path / os.path.basename(str(db_path))
        base_container_path = db_path.container_path
        container = db_path.container
        # Copy sqlite db and possible WAL/SHM sidecars to ensure consistent read
        for suffix in ("", "-wal", "-shm"):
            src = f"{container}:{base_container_path}{suffix}"
            dest = str(local_path) + suffix
            try:
                subprocess.run(["docker", "cp", src, dest], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                # Sidecar may not exist; ignore errors for -wal/-shm
                if suffix == "":
                    raise RuntimeError(f"Failed to copy DB from container: {src}")
        return local_path

    # Bind mode: use path directly
    return db_path


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture
def temp_cwa_db(tmp_path, monkeypatch):
    """
    Create a temporary CWA database for testing.

    Uses monkeypatch to temporarily override the database path
    so tests don't interfere with real data.
    """
    import sys
    from pathlib import Path

    # Add scripts directory to path (works in both dev container and CI)
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from cwa_db import CWA_DB

    # Override the database path
    db_path = tmp_path / "cwa.db"
    monkeypatch.setenv('CWA_DB_PATH', str(tmp_path))

    # Create the database
    db = CWA_DB(verbose=False)

    yield db

    # Cleanup
    if db.con:
        db.con.close()


# ============================================================================
# Markers by directory, and skips for missing tools
# ============================================================================

@pytest.hookimpl(tryfirst=True)  # add directory markers before -m deselection
def pytest_collection_modifyitems(config, items):
    """Mark tests by directory and skip Docker tests when Docker is missing."""
    skip_docker_integration = pytest.mark.skip(reason="Docker not available")
    has_docker = shutil.which('docker') is not None

    tests_root = Path(__file__).parent
    dir_markers = {tests_root / "unit": "unit", tests_root / "smoke": "smoke"}

    for item in items:
        # Tests under tests/unit and tests/smoke are unit/smoke tests by
        # location. Several files there carry no explicit marker, so CI's
        # `-m "smoke or unit"` silently skipped them; mark them here.
        item_path = Path(str(item.fspath))
        for directory, marker in dir_markers.items():
            if directory in item_path.parents and item.get_closest_marker(marker) is None:
                item.add_marker(getattr(pytest.mark, marker))
        if "docker_integration" in item.keywords and not has_docker:
            item.add_marker(skip_docker_integration)


SHARD_WEIGHTS = Path(__file__).parent / "shard_weights.json"


class _ShardPlugin:
    """LILY_TEST_SHARD=i/n keeps only shard i of n (CI runs the shards in parallel).

    Whole files go to one shard, so module fixtures aren't built twice. Files are
    dealt out heaviest first to the lightest shard, weighed by the seconds they took
    (tests/shard_weights.json): by test count alone, one shard got the files with
    slow app fixtures and ran twice as long as another. A file not in the weights
    counts its tests at the average per-test time. Every xdist worker computes the
    same split, since it depends only on the collected items and that file.
    Regenerate the weights with LILY_WRITE_SHARD_WEIGHTS=1 on a full unit run.
    """

    def __init__(self, index: int, total: int):
        self.index, self.total = index, total

    @pytest.hookimpl(trylast=True)  # after -m deselection, so shards split what actually runs
    def pytest_collection_modifyitems(self, config, items):
        counts: dict[str, int] = {}
        for item in items:
            path = item.nodeid.split("::")[0]
            counts[path] = counts.get(path, 0) + 1
        weights = json.loads(SHARD_WEIGHTS.read_text())
        known = [p for p in counts if p in weights]
        per_test = (sum(weights[p] for p in known) / sum(counts[p] for p in known)) if known else 1.0
        weight = {p: weights.get(p, n * per_test) for p, n in counts.items()}
        loads = [0.0] * self.total
        owner = {}
        for path in sorted(counts, key=lambda p: (-weight[p], p)):
            lightest = loads.index(min(loads))
            owner[path] = lightest
            loads[lightest] += weight[path]
        keep, drop = [], []
        for item in items:
            (keep if owner[item.nodeid.split("::")[0]] == self.index - 1 else drop).append(item)
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


class _ShardWeightsWriter:
    """LILY_WRITE_SHARD_WEIGHTS=1: writes each test file's total seconds (setup, call
    and teardown) to tests/shard_weights.json at the end of the run."""

    def __init__(self):
        self.seconds: dict[str, float] = {}

    def pytest_runtest_logreport(self, report):
        path = report.nodeid.split("::")[0]
        self.seconds[path] = self.seconds.get(path, 0.0) + report.duration

    def pytest_sessionfinish(self, session):
        if hasattr(session.config, "workerinput"):  # xdist workers; the controller writes
            return
        rounded = {p: round(s, 2) for p, s in sorted(self.seconds.items())}
        SHARD_WEIGHTS.write_text(json.dumps(rounded, indent=1) + "\n")


def pytest_configure(config):
    shard = os.environ.get("LILY_TEST_SHARD")
    if shard:
        index, total = (int(part) for part in shard.split("/"))
        config.pluginmanager.register(_ShardPlugin(index, total), "lily-shard")
    if os.environ.get("LILY_WRITE_SHARD_WEIGHTS"):
        config.pluginmanager.register(_ShardWeightsWriter(), "lily-shard-weights")


# ============================================================================
# Docker Container Fixtures (for integration tests)
# ============================================================================

@pytest.fixture(scope="session")
def docker_compose_file() -> str:
    """Return path to the docker-compose.yml file."""
    repo_root = Path(__file__).parent.parent
    return str(repo_root / "docker-compose.yml")


@pytest.fixture(scope="session")
def test_volumes(tmp_path_factory) -> dict:
    """
    Create temporary directories for Docker volume mounts.

    Returns a dict with paths for config, ingest, and library volumes.
    """
    base_dir = tmp_path_factory.mktemp("cwa_test_volumes")

    volumes = {
        "config": base_dir / "config",
        "ingest": base_dir / "cwa-book-ingest",
        "library": base_dir / "calibre-library",
    }

    # Create directory structure
    for vol_dir in volumes.values():
        vol_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal config structure
    config_dir = volumes["config"]
    (config_dir / "processed_books" / "imported").mkdir(parents=True, exist_ok=True)
    (config_dir / "processed_books" / "failed").mkdir(parents=True, exist_ok=True)
    (config_dir / ".cwa_conversion_tmp").mkdir(exist_ok=True)

    # Create empty Calibre library (CWA will initialize it)
    library_dir = volumes["library"]
    (library_dir / ".keep").touch()

    yield volumes

    # Cleanup after session
    try:
        shutil.rmtree(base_dir)
    except Exception as e:
        print(f"Warning: Could not clean up test volumes: {e}")


# First-run admin password for test containers (LILY_ADMIN_PASSWORD in the container)
TEST_ADMIN_PASSWORD = os.getenv("LILY_ADMIN_PASSWORD", "lily-test-admin-pw")


@pytest.fixture(scope="session")
def cwa_container(docker_compose_file: str, test_volumes: dict) -> Generator:
    """
    Start the CWA Docker container and wait until it's reachable, measuring real startup time.
    By default, no arbitrary timeout is imposed unless configured via env vars.

    Env controls:
      - CWA_TEST_NO_TIMEOUT=true  -> wait indefinitely
      - CWA_TEST_START_TIMEOUT=N  -> cap wait to N seconds (ignored if NO_TIMEOUT=true)
      - CWA_TEST_PORT=PORT        -> host port to bind (default 8085)
      - CWA_TEST_IMAGE=IMAGE      -> image ref to run (default latest)
    """
    import requests
    import subprocess
    from testcontainers.compose import DockerCompose

    # Get repo root
    repo_root = Path(docker_compose_file).parent

    # Runtime configuration
    test_port = os.getenv('CWA_TEST_PORT', '8085')
    test_image = os.getenv('CWA_TEST_IMAGE', 'lily:dev')
    test_uid, test_gid = _get_test_uid_gid()

    # Create a temporary docker-compose override for testing
    compose_override = repo_root / "docker-compose.test-override.yml"

    # Write test-specific overrides
    # Note: Container always runs on 8083 internally, we just map host port to it
    override_content = f"""---
services:
  calibre-web-automated:
    image: {test_image}
    container_name: cwa-test-container
    environment:
      - PUID={test_uid}
      - PGID={test_gid}
      - TZ=UTC
      - NETWORK_SHARE_MODE=false
      - LILY_ADMIN_PASSWORD={TEST_ADMIN_PASSWORD}
    volumes:
      - {test_volumes['config']}:/config
      - {test_volumes['ingest']}:/cwa-book-ingest
      - {test_volumes['library']}:/calibre-library
    ports:
      - "{test_port}:8083"
    restart: "no"
"""

    compose_override.write_text(override_content)

    try:
        print("\n🐳 Starting CWA Docker container for testing...")
        print(f"   Port: {test_port}")

        # Use testcontainers with docker-compose
        compose = DockerCompose(
            context=str(repo_root),
            compose_file_name=["docker-compose.test-override.yml"],
            pull=False,
        )

        # Start the container
        compose.start()

        # Wait for CWA to be ready (health check)
        print("⏳ Waiting for CWA to be ready...")
        no_timeout = os.getenv('CWA_TEST_NO_TIMEOUT', 'false').lower() == 'true'
        max_wait_env = os.getenv('CWA_TEST_START_TIMEOUT', '').strip()
        max_wait = None if no_timeout or max_wait_env in ('0', '-1') else int(max_wait_env or '600')
        start_time = time.time()
        last_progress = 0

        while True:
            # Enforce optional timeout cap
            if max_wait is not None and (time.time() - start_time) >= max_wait:
                break

            # Try to connect to the web interface (root and login)
            ready = False
            for path in ("/", "/login"):
                try:
                    resp = requests.get(
                        f"http://localhost:{test_port}{path}", timeout=5, allow_redirects=False
                    )
                    if 200 <= resp.status_code < 400:
                        ready = True
                        break
                except requests.exceptions.RequestException:
                    pass

            # Fallback readiness via logs: consider ready when ingest services are watching
            if not ready:
                try:
                    logs = subprocess.run(
                        ['docker', 'compose', '-f', str(compose_override), 'logs', '--no-color', '--tail', '200'],
                        cwd=str(repo_root),
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    log_text = logs.stdout or ""
                    if (
                        "Watching folder: /cwa-book-ingest" in log_text
                        and "metadata-change-detector" in log_text
                    ) or "Connection to localhost" in log_text:
                        ready = True
                except Exception:
                    pass

            if ready:
                duration = int(time.time() - start_time)
                print(f"✅ CWA container is ready! (startup: {duration}s)")
                break

            # Progress output every 5s
            now = time.time()
            if now - last_progress >= 5:
                print(f"… still starting (waited {int(now - start_time)}s)")
                last_progress = now
            time.sleep(2)

        if max_wait is not None and (time.time() - start_time) >= max_wait:
            print("❌ CWA container failed to become ready in time")
            # Attempt to fetch container status/logs for debugging before stopping
            try:
                print("\n--- docker compose ps ---")
                subprocess.run(
                    ['docker', 'compose', '-f', str(compose_override), 'ps'],
                    cwd=str(repo_root),
                    check=False,
                )
                print("\n--- docker compose logs (tail 200) ---")
                subprocess.run(
                    ['docker', 'compose', '-f', str(compose_override), 'logs', '--tail', '200'],
                    cwd=str(repo_root),
                    check=False,
                )
                print("--- end logs ---\n")
            except Exception as e:
                print(f"Warning: Unable to retrieve docker logs: {e}")
            compose.stop()
            raise TimeoutError("CWA container did not start within the allotted time")

        # Container is ready; detect bind-mount visibility and auto-fallback if needed
        try:
            sentinel = test_volumes["ingest"] / f".cwa_bind_check_{int(time.time())}"
            sentinel.write_text("bind-check")
            # Check visibility inside container
            res = subprocess.run(
                ["docker", "exec", "cwa-test-container", "test", "-f", f"/cwa-book-ingest/{sentinel.name}"],
                check=False,
            )
            if res.returncode != 0:
                print("⚠️  Bind mounts not visible in container. Enabling auto volume fallback (docker cp mode).")
                global AUTO_DOCKER_VOLUMES
                AUTO_DOCKER_VOLUMES = True
            else:
                print("✅ Bind mounts are visible to container.")
        except Exception as e:
            print(f"Warning: bind mount visibility check failed: {e}")
        finally:
            try:
                if 'sentinel' in locals() and sentinel.exists():
                    sentinel.unlink()
            except Exception:
                pass

        # Container is ready, yield it to tests
        yield compose

    finally:
        # Cleanup
        print("\n🧹 Stopping CWA Docker container...")
        try:
            compose.stop()
        except Exception as e:
            print(f"Warning: Error stopping container: {e}")

        # Remove override file
        if compose_override.exists():
            try:
                compose_override.unlink()
            except Exception as e:
                print(f"Warning: Could not remove override file: {e}")


@pytest.fixture(scope="session")
def container_name(cwa_container) -> str:
    """The test container's name for docker exec/cp (set in the compose override)."""
    return "cwa-test-container"


@pytest.fixture(scope="function")
def sample_ebook_path(tmp_path) -> Path:
    """
    Provide path to a minimal test EPUB file.

    Creates a fresh minimal EPUB for each test function.
    """
    # Import relative to tests directory
    import sys
    from pathlib import Path as PathLib

    # Add tests directory to path if not already there
    # NOTE: append (not prepend) so tests/ never shadows site-packages
    tests_dir = PathLib(__file__).parent
    if str(tests_dir) not in sys.path:
        sys.path.append(str(tests_dir))

    from fixtures.generate_synthetic import create_minimal_epub

    epub_path = tmp_path / "test_sample.epub"
    create_minimal_epub(epub_path)

    return epub_path


@pytest.fixture(scope="function")
def ingest_folder(test_volumes: dict, container_name: str) -> Path:
    """
    Provide the path to the ingest folder mounted in the container.

    Tests can drop files here to trigger ingest processing.
    """
    if AUTO_DOCKER_VOLUMES:
        return DockerPath(container_name, "/cwa-book-ingest")
    return test_volumes["ingest"]


@pytest.fixture(scope="function")
def library_folder(test_volumes: dict, container_name: str) -> Path:
    """
    Provide the path to the Calibre library folder mounted in the container.

    Tests can check this folder for imported books.
    """
    if AUTO_DOCKER_VOLUMES:
        return DockerPath(container_name, "/calibre-library")
    return test_volumes["library"]
