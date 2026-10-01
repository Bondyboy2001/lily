# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The background task queue: CalibreTask base class and the WorkerThread that runs tasks.

Tasks run one at a time. Python can't kill a thread, so a task that hangs (a stuck
subprocess, a dead network mount) used to block every later task forever. A watchdog
thread now checks the running task every minute: once it has run longer than its limit
(LILY_TASK_TIMEOUT_HOURS, default 6; a task class may set max_runtime_hours; 0 turns the
watchdog off) the task is marked failed in the task list and in job_status, logged, and a
fresh worker loop takes over the queue. The stuck thread is left behind: if it ever
returns it can no longer change the task's status, and it exits instead of taking more
work. While it lives it may still hold files or locks, so the log says which task it was.
"""

import os
import threading
import abc
import uuid
import time

try:
    import queue
except ImportError:
    import Queue as queue
from datetime import datetime
from collections import namedtuple

from cps import logger

log = logger.create()

# task 'status' consts
STAT_WAITING = 0
STAT_FAIL = 1
STAT_STARTED = 2
STAT_FINISH_SUCCESS = 3
STAT_ENDED = 4
STAT_CANCELLED = 5

# Only retain this many tasks in dequeued list
TASK_CLEANUP_TRIGGER = 20

TASK_TIMEOUT_ENV = "LILY_TASK_TIMEOUT_HOURS"
DEFAULT_TASK_TIMEOUT_HOURS = 6.0
WATCHDOG_INTERVAL_SECONDS = 60

QueuedTask = namedtuple('QueuedTask', 'num, user, added, task, hidden')


def task_timeout_hours():
    """The watchdog's default limit from LILY_TASK_TIMEOUT_HOURS (0 or less = off)."""
    raw = os.environ.get(TASK_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TASK_TIMEOUT_HOURS
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("Ignoring invalid %s=%r", TASK_TIMEOUT_ENV, raw)
        return DEFAULT_TASK_TIMEOUT_HOURS


def _get_main_thread():
    for t in threading.enumerate():
        if t.__class__.__name__ == '_MainThread':
            return t
    raise Exception("main thread not found?!")


class ImprovedQueue(queue.Queue):
    def to_list(self):
        """
        Returns a copy of all items in the queue without removing them.
        """

        with self.mutex:
            return list(self.queue)


# Class for all worker tasks in the background
class WorkerThread(threading.Thread):
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WorkerThread()
        return cls._instance

    def __init__(self, watchdog_interval=WATCHDOG_INTERVAL_SECONDS):
        threading.Thread.__init__(self)

        self.dequeued = list()

        self.doLock = threading.Lock()
        self.queue = ImprovedQueue()
        self.num = 0
        # Each queue loop runs while its generation is current; the watchdog bumps it to
        # retire a loop stuck in a task and starts a new one
        self._generation = 0
        self._current = None  # (generation, QueuedTask, time.monotonic() at start)
        self.watchdog_interval = watchdog_interval
        self.start()
        threading.Thread(target=self._watchdog_loop, name="lily-task-watchdog", daemon=True).start()

    @classmethod
    def add(cls, user, task, hidden=False):
        ins = cls.get_instance()
        ins.num += 1
        username = user if user is not None else 'System'
        log.debug("Add Task for user: {} - {}".format(username, task))
        ins.queue.put(QueuedTask(
            num=ins.num,
            user=username,
            added=datetime.now(),
            task=task,
            hidden=hidden
        ))

    @property
    def tasks(self):
        with self.doLock:
            tasks = self.queue.to_list() + self.dequeued
            return sorted(tasks, key=lambda x: x.num)

    def cleanup_tasks(self):
        with self.doLock:
            dead = []
            alive = []
            for x in self.dequeued:
                (dead if x.task.dead else alive).append(x)

            # if the ones that we need to keep are within the trigger, do nothing else
            delta = len(self.dequeued) - len(dead)
            if delta > TASK_CLEANUP_TRIGGER:
                ret = alive
            else:
                # otherwise, loop off the oldest dead tasks until we hit the target trigger
                ret = sorted(dead, key=lambda y: y.task.end_time)[-TASK_CLEANUP_TRIGGER:] + alive

            self.dequeued = sorted(ret, key=lambda y: y.num)

    # Main thread loop starting the different tasks
    def run(self):
        self._process_queue(0)

    def _process_queue(self, generation):
        main_thread = _get_main_thread()
        while main_thread.is_alive() and self._generation == generation:
            try:
                # this blocks until something is available. This can cause issues when the main thread dies - this
                # thread will remain alive. We implement a timeout to unblock every second which allows us to check if
                # the main thread is still alive.
                # We don't use a daemon here because we don't want the tasks to just be abruptly halted, leading to
                # possible file / database corruption
                item = self.queue.get(timeout=1)
            except queue.Empty:
                time.sleep(1)
                continue

            with self.doLock:
                # add to list so that in-progress tasks show up
                self.dequeued.append(item)
                self._current = (generation, item, time.monotonic())

            # once we hit our trigger, start cleaning up dead tasks
            if len(self.dequeued) > TASK_CLEANUP_TRIGGER:
                self.cleanup_tasks()

            # sometimes tasks (like Upload) don't actually have work to do and are created as already finished
            if item.task.stat is STAT_WAITING:
                # CalibreTask.start() should wrap all exceptions in its own error handling
                item.task.start(self)

            with self.doLock:
                if self._current is not None and self._current[0] == generation:
                    self._current = None

            # remove self_cleanup tasks and hidden "System Tasks" from list
            if item.task.self_cleanup or item.hidden:
                try:
                    self.dequeued.remove(item)
                except ValueError:
                    pass  # already cleaned up after the watchdog failed it

            self.queue.task_done()
        # A loop retired by the watchdog ends here once its stuck task finally returns

    def _watchdog_loop(self):
        main_thread = _get_main_thread()
        while main_thread.is_alive():
            time.sleep(self.watchdog_interval)
            try:
                self.check_watchdog()
            except Exception as e:
                log.error("Task watchdog check failed: %s", e)

    def check_watchdog(self, now=None):
        """Fails the running task if it exceeded its time limit, and hands the queue to a new
        loop thread. Returns the abandoned QueuedTask, or None."""
        default_hours = task_timeout_hours()
        if default_hours <= 0:
            return None
        now = time.monotonic() if now is None else now
        with self.doLock:
            if self._current is None:
                return None
            generation, item, started = self._current
            limit_hours = getattr(item.task, "max_runtime_hours", None) or default_hours
            if now - started < limit_hours * 3600:
                return None
            self._current = None
            self._generation = generation + 1
            new_generation = self._generation
        message = ("Stopped waiting after %g hours: the task did not finish and was marked as failed. "
                   "Its thread could not be killed; restart Lily if it holds files or locks." % limit_hours)
        log.error("Task watchdog: %s (%s, queued by %s) has run for more than %g hours. Marking it failed "
                  "and moving on to the next task; the stuck thread is left behind.",
                  item.task.name, item.task.id, item.user, limit_hours)
        item.task.abandon(message)
        threading.Thread(target=self._process_queue, args=(new_generation,),
                         name="lily-worker-%d" % new_generation).start()
        return item

    def stop(self):
        """Ends every queue loop after its current task (tests)."""
        self._generation = -1

    def end_task(self, task_id):
        ins = self.get_instance()
        for __, __, __, task, __ in ins.tasks:
            if str(task.id) == str(task_id) and task.is_cancellable:
                task.stat = STAT_CANCELLED if task.stat == STAT_WAITING else STAT_ENDED

    def cancel_tasks_for_book(self, book_id):
        """Cancel all pending tasks associated with a specific book ID

        Args:
            book_id: The book ID whose tasks should be cancelled

        Returns:
            int: Number of tasks cancelled
        """
        cancelled_count = 0
        ins = self.get_instance()

        try:
            with ins.doLock:
                # Access queue and dequeued directly to avoid recursive lock from .tasks property
                tasks_snapshot = list(ins.queue.to_list() + ins.dequeued)
        except Exception as e:
            log.warning("[worker] Could not get tasks snapshot: %s", str(e))
            return 0

        # Process outside the lock to avoid deadlock
        tasks_to_cancel = []
        for queued_task in tasks_snapshot:
            task = queued_task.task
            # Check if task has a book_id attribute and it matches
            if hasattr(task, 'book_id') and task.book_id == book_id:
                # Only cancel if task is waiting or scheduled
                if task.stat in (STAT_WAITING,) and task.is_cancellable:
                    tasks_to_cancel.append((task, 'book_id'))
            # Also check for scheduled tasks with bookId attribute (some tasks use different naming)
            elif hasattr(task, 'bookId') and task.bookId == book_id:
                if task.stat in (STAT_WAITING,) and task.is_cancellable:
                    tasks_to_cancel.append((task, 'bookId'))

        # Cancel tasks without holding the main lock
        for task, attr_name in tasks_to_cancel:
            try:
                task.stat = STAT_CANCELLED
                task.error = f"Cancelled: Book {book_id} was removed from library"
                log.info("[worker] Cancelled task %s for book %s", task.name, book_id)
                cancelled_count += 1
            except Exception as e:
                log.warning("[worker] Failed to cancel task %s: %s", task.name, str(e))

        return cancelled_count

    def has_active_task_of_type(self, task_class_name, extra_check=None):
        """Check if there is an active (non-terminal) task of the given class name.

        Args:
            task_class_name: The __name__ of the task class to match.
            extra_check: Optional callable(task) -> bool for additional filtering.

        Returns:
            bool: True if an active matching task exists.
        """
        terminal_stats = {STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED, STAT_CANCELLED}
        try:
            for __, __, __, task, __ in self.tasks:
                if getattr(task, "stat", None) in terminal_stats:
                    continue
                if task.__class__.__name__ != task_class_name:
                    continue
                if extra_check is None or extra_check(task):
                    return True
        except Exception as e:
            log.warning("Active-task check for %s failed: %s", task_class_name, e)
        return False


def _record_job(job, event, error=None):
    try:
        from cps.services.job_status import record_job_event
        record_job_event(job, event, error)
    except Exception as e:
        log.warning("Job status for %s not recorded: %s", job, e)


class CalibreTask:
    __metaclass__ = abc.ABCMeta

    # Recurring jobs set this to have their runs recorded in cwa.db (cps/services/job_status.py)
    # for the admin banner and /health. A task may clear it in run() when it had nothing to do.
    job_name = None
    # Watchdog limit for this task class; None uses LILY_TASK_TIMEOUT_HOURS (see module docstring)
    max_runtime_hours = None

    def __init__(self, message):
        self._abandoned = False
        self._progress = 0
        self.stat = STAT_WAITING
        self.error = None
        self.start_time = None
        self.end_time = None
        self.message = message
        self.id = uuid.uuid4()
        self.self_cleanup = False
        self._scheduled = False
        self.done_event = threading.Event()

    @abc.abstractmethod
    def run(self, worker_thread):
        """The main entry-point for this task"""
        raise NotImplementedError

    @abc.abstractmethod
    def name(self):
        """Provides the caller some human-readable name for this class"""
        raise NotImplementedError

    @abc.abstractmethod
    def is_cancellable(self):
        """Does this task gracefully handle being cancelled (STAT_ENDED, STAT_CANCELLED)?"""
        raise NotImplementedError

    def start(self, *args):
        self.start_time = datetime.now()
        self.stat = STAT_STARTED
        if self.job_name:
            _record_job(self.job_name, "start")

        # catch any unhandled exceptions in a task and automatically fail it
        try:
            self.run(*args)
        except Exception as ex:
            self._handleError(str(ex))
            log.error_or_exception(ex)

        if self._abandoned:
            # The watchdog already failed and recorded this run
            log.warning("Task %s returned after the watchdog had given up on it", self.name)
            return
        self.end_time = datetime.now()
        # Cancelled/ended runs are neither a success nor a failure
        if self.job_name and self.stat == STAT_FINISH_SUCCESS:
            _record_job(self.job_name, "success")
        elif self.job_name and self.stat == STAT_FAIL:
            _record_job(self.job_name, "error", self.error)

    def abandon(self, message):
        """Marks a hung task failed for good (called by the worker watchdog). The thread
        still running it can no longer change its status, progress or error."""
        self._handleError(message)
        self.end_time = datetime.now()
        self._abandoned = True
        if self.job_name:
            _record_job(self.job_name, "error", message)

    @property
    def abandoned(self):
        return getattr(self, "_abandoned", False)

    @property
    def stat(self):
        return self._stat

    @stat.setter
    def stat(self, x):
        if not self.abandoned:
            self._stat = x

    @property
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, x):
        if not 0 <= x <= 1:
            raise ValueError("Task progress should within [0, 1] range")
        if not self.abandoned:
            self._progress = x

    @property
    def error(self):
        return self._error

    @error.setter
    def error(self, x):
        if not self.abandoned:
            self._error = x

    @property
    def runtime(self):
        return (self.end_time or datetime.now()) - self.start_time

    @property
    def dead(self):
        """Determines whether or not this task can be garbage collected

        We have a separate dictating this because there may be certain tasks that want to override this
        """
        # By default, we're good to clean a task if it's "Done"
        return self.stat in (STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED, STAT_CANCELLED)

    @property
    def self_cleanup(self):
        return self._self_cleanup

    @self_cleanup.setter
    def self_cleanup(self, is_self_cleanup):
        self._self_cleanup = is_self_cleanup

    @property
    def scheduled(self):
        return self._scheduled

    @scheduled.setter
    def scheduled(self, is_scheduled):
        self._scheduled = is_scheduled

    def _handleError(self, error_message):
        self.stat = STAT_FAIL
        self.progress = 1
        self.error = error_message
        self.done_event.set()

    def _handleSuccess(self):
        self.stat = STAT_FINISH_SUCCESS
        self.progress = 1
        self.done_event.set()

    def __str__(self):
        # name may be a lazy translation (N_), which __str__ must not return as-is
        return str(self.name)
