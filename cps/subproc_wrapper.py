# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import signal
import subprocess
import re
import threading


class ProcessTimeout:
    """Kills a process that is still running after `seconds`.

    Use it around the loop that reads the process's output: a blocking readline() returns
    once the process is killed, so the loop ends instead of hanging the worker forever.
    With kill_group (for processes started with process_open(new_session=True)) the whole
    process group is killed, including helpers the tool spawned.
    """

    def __init__(self, process, seconds, kill_group=False):
        self.process = process
        self.seconds = seconds
        self.kill_group = kill_group
        self.timed_out = False
        self._timer = threading.Timer(seconds, self._kill)
        self._timer.daemon = True

    def _kill(self):
        if self.process.poll() is not None:
            return
        self.timed_out = True
        try:
            if self.kill_group and hasattr(os, "killpg"):
                os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
        except OSError:
            pass  # exited in the meantime

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.cancel()
        return False


def drain_in_background(stream):
    """Reads `stream` to the end in a daemon thread. Returns (thread, lines); join the thread
    before using lines. Reading stderr this way keeps a chatty process from blocking on a
    full pipe while the caller only reads stdout."""
    lines = []

    def _read():
        try:
            lines.extend(stream.readlines())
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    return thread, lines


def process_open(command, quotes=(), env=None, sout=subprocess.PIPE, serr=subprocess.PIPE, newlines=True,
                 new_session=False):
    # Linux py2.7 encode as list without quotes no empty element for parameters
    # linux py3.x no encode and as list without quotes no empty element for parameters
    # windows py2.7 encode as string with quotes empty element for parameters is okay
    # windows py 3.x no encode and as string with quotes empty element for parameters is okay
    # separate handling for windows and linux
    if os.name == 'nt':
        for key, element in enumerate(command):
            if key in quotes:
                command[key] = '"' + element + '"'
        exc_command = " ".join(command)
    else:
        exc_command = [x for x in command]

    return subprocess.Popen(exc_command, shell=False, stdout=sout, stderr=serr, universal_newlines=newlines, env=env,
                            start_new_session=new_session and os.name != 'nt')  # nosec


def process_wait(command, serr=subprocess.PIPE, pattern=""):
    # Run command, wait for process to terminate, and return an iterator over lines of its output.
    newlines = os.name != 'nt'
    ret_val = ""
    p = process_open(command, serr=serr, newlines=newlines)
    p.wait()
    for line in p.stdout.readlines():
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors="ignore")
        match = re.search(pattern, line, re.IGNORECASE)
        if match and ret_val == "":
            ret_val = match
            break
    p.stdout.close()
    p.stderr.close()
    return ret_val
