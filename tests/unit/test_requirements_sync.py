# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""pyproject.toml, requirements.txt and requirements.lock agree (the Docker image installs
requirements.txt constrained by requirements.lock)."""

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _requirement_lines():
    lines = (REPO / "requirements.txt").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


@pytest.mark.unit
def test_pyproject_dependencies_match_requirements_txt():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    declared = list(pyproject["dependencies"])
    for group in pyproject["optional-dependencies"].values():
        declared += group
    requirements = set(_requirement_lines())
    missing = sorted(dep for dep in declared if dep not in requirements)
    assert not missing, f"in pyproject.toml but not requirements.txt: {missing}"


@pytest.mark.unit
def test_requirements_txt_has_no_duplicate_packages():
    names = [line.split(";")[0].split("=")[0].split(">")[0].split("<")[0].strip().lower()
             for line in _requirement_lines()]
    # A package may repeat only with different environment markers (e.g. win32-only wheels)
    unmarked = [n for n, line in zip(names, _requirement_lines()) if ";" not in line]
    dupes = sorted({n for n in unmarked if unmarked.count(n) > 1})
    assert not dupes, dupes


# --- requirements.lock -------------------------------------------------------------------------
# The Docker image resolves requirements.txt with requirements.lock as constraints, so every
# requirement that applies to the image (Linux, CPython 3.13) must be pinned there and the pin
# must sit inside the range requirements.txt allows.

_IMAGE_ENV = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
              "python_version": "3.13", "python_full_version": "3.13.0",
              "implementation_name": "cpython", "platform_python_implementation": "CPython"}


def _lock_pins():
    from packaging.utils import canonicalize_name
    pins = {}
    for raw in (REPO / "requirements.lock").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        assert sep and version.strip(), f"requirements.lock entry is not an exact pin: {raw!r}"
        pins[canonicalize_name(name.strip())] = version.strip()
    return pins


def _image_requirements():
    from packaging.requirements import Requirement
    reqs = [Requirement(line) for line in _requirement_lines()]
    return [r for r in reqs if r.marker is None or r.marker.evaluate(_IMAGE_ENV)]


@pytest.mark.unit
def test_lock_pins_every_requirement_within_its_range():
    from packaging.utils import canonicalize_name
    pins = _lock_pins()
    problems = []
    for req in _image_requirements():
        version = pins.get(canonicalize_name(req.name))
        if version is None:
            problems.append(f"{req.name}: missing from requirements.lock")
        elif not req.specifier.contains(version, prereleases=True):
            problems.append(f"{req.name}=={version} is outside requirements.txt range {req.specifier}")
    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_dockerfile_installs_with_the_lock():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "requirements.lock" in dockerfile.split("FROM build-base AS python-deps", 1)[-1], (
        "the python-deps stage should install with -c/-r requirements.lock")
