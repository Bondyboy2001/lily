# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared secret for calls from CWA's own processes to the web app's internal endpoints.

The web process and the s6 scripts (ingest processor etc.) run as the same user in the
same container, so they share a random token through a 0600 file in a private 0700
directory under the temp dir (lily-<uid>). A token file is only reused when it is a regular
file (not a symlink) owned by this user and readable by nobody else; otherwise it is
replaced. Callers send it in the INTERNAL_TOKEN_HEADER header; the web app compares it in
constant time.

Kept dependency-free so scripts can import it without pulling in the Flask app.
"""

import hmac
import os
import secrets
import stat
import tempfile

INTERNAL_TOKEN_HEADER = "X-CWA-Internal-Token"
_TOKEN_FILE_ENV = "CWA_INTERNAL_TOKEN_FILE"


def _private_dir() -> str:
    """<tmp>/lily-<uid>, created 0700. Refuses a directory someone else owns or can write to."""
    path = os.path.join(tempfile.gettempdir(), "lily-%d" % os.getuid())
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("unsafe directory for the internal token: %s" % path)
    return path


def _token_path() -> str:
    return os.environ.get(_TOKEN_FILE_ENV) or os.path.join(_private_dir(), "internal_token")


def _read_trusted(path: str):
    """The token in path if the file is ours alone; '' if it must be replaced; None if missing."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:
        return ""  # a symlink (O_NOFOLLOW) or unreadable
    with os.fdopen(fd, "r", encoding="ascii", errors="replace") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            return ""
        return f.read().strip()


def get_internal_token() -> str:
    """Return the shared token, creating it atomically on first use."""
    path = _token_path()
    token = _read_trusted(path)
    if token:
        return token
    if token == "":
        # Wrong owner, permissions or a symlink: never trust it, start over
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    # Write to a private temp file, then link it into place: os.link fails if another
    # process won the race, and readers never see a partially written token.
    token = secrets.token_hex(32)
    tmp_path = f"{path}.{os.getpid()}.new"
    try:
        os.unlink(tmp_path)  # left over from a crashed process with the same pid
    except FileNotFoundError:
        pass
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(token)
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            # Another process won the race; its file must pass the same checks
            token = _read_trusted(path) or ""
            if not token:
                raise PermissionError("untrusted internal token file: %s" % path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
    return token


def internal_headers() -> dict:
    """Headers a CWA process must send when calling an internal endpoint."""
    return {INTERNAL_TOKEN_HEADER: get_internal_token()}


def is_valid_internal_token(candidate) -> bool:
    if not candidate:
        return False
    try:
        return hmac.compare_digest(str(candidate), get_internal_token())
    except OSError:
        return False
