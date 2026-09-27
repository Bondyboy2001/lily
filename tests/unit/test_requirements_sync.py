# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""pyproject.toml and requirements.txt list the same pins (the Docker image installs requirements.txt)."""

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
