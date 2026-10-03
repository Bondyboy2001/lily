# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Registers the nightly and scheduled background tasks (backups, mirror, cleanup, scans)."""

import datetime
import os

from . import config, constants, logger
from .services.background_scheduler import BackgroundScheduler, CronTrigger
# Re-exported: cps.admin reads the feature flag as `schedule.use_APScheduler`.
from .services.background_scheduler import use_APScheduler  # noqa: F401
from .tasks.clean import TaskClean
from .tasks.thumbnail import TaskGenerateCoverThumbnails, TaskClearCoverThumbnailCache
from .tasks.thumbnail_migration import check_and_migrate_thumbnails
from .services.worker import WorkerThread

log = logger.create()


def get_scheduled_tasks():
    tasks = []
    # Delete temp folder
    tasks.append([lambda: TaskClean(), 'delete temp', True])

    # Generate all missing book cover thumbnails
    if config.schedule_generate_book_covers:
        tasks.append([lambda: TaskClearCoverThumbnailCache(0), 'delete superfluous book covers', True])
        tasks.append([lambda: TaskGenerateCoverThumbnails(), 'generate book covers', False])

    return tasks


def end_scheduled_tasks():
    worker = WorkerThread.get_instance()
    for __, __, __, task, __ in worker.tasks:
        if task.scheduled and task.is_cancellable:
            worker.end_task(task.id)


def register_scheduled_tasks():
    scheduler = BackgroundScheduler()

    if scheduler:
        # Remove all existing jobs
        scheduler.remove_all_jobs()

        start = config.schedule_start_time
        duration = config.schedule_duration

        # Register scheduled tasks
        timezone_info = datetime.datetime.now(datetime.UTC).astimezone().tzinfo
        scheduler.schedule_tasks(tasks=get_scheduled_tasks(), trigger=CronTrigger(hour=start,
                                                   timezone=timezone_info))
        _schedule_duplicate_scan(scheduler, timezone_info)
        end_time = calclulate_end_time(start, duration)
        scheduler.schedule(func=end_scheduled_tasks, trigger=CronTrigger(hour=end_time.hour, minute=end_time.minute,
                                                                         timezone=timezone_info),
                           name="end scheduled task")

        _schedule_db_backup(scheduler, start, timezone_info)
        _schedule_processed_books_cleanup(scheduler, start, timezone_info)
        _schedule_library_mirror(scheduler, start, timezone_info)

        # Kick-off tasks, if they should currently be running
        if should_task_be_running(start, duration):
            scheduler.schedule_tasks_immediately(tasks=get_scheduled_tasks())


def register_startup_tasks():
    scheduler = BackgroundScheduler()

    if scheduler:
        start = config.schedule_start_time
        duration = config.schedule_duration

        # Run thumbnail migration on startup (one-time operation)
        try:
            check_and_migrate_thumbnails()
        except Exception:
            # Don't let migration failures stop the application, but say so
            log.exception("scheduler: thumbnail migration failed; continuing with startup")

        # The arXiv shelf takes over from the Papers shelf (one-time operation)
        try:
            from .services.arxiv_shelf import replace_papers_shelf_once
            replace_papers_shelf_once(os.path.join(constants.CONFIG_DIR, ".cwa_migrations", "arxiv_shelf_v1"))
        except Exception as e:
            log.warning("Could not replace the Papers shelf with the arXiv shelf: %s", e)

        # Run scheduled tasks immediately for development and testing
        # Ignore tasks that should currently be running, as these will be added when registering scheduled tasks
        if constants.APP_MODE in ['development', 'test'] and not should_task_be_running(start, duration):
            scheduler.schedule_tasks_immediately(tasks=get_scheduled_tasks())
        else:
            scheduler.schedule_tasks_immediately(tasks=[[lambda: TaskClean(), 'delete temp', True]])


def should_task_be_running(start, duration):
    now = datetime.datetime.now()
    start_time = datetime.datetime.now().replace(hour=start, minute=0, second=0, microsecond=0)
    end_time = start_time + datetime.timedelta(hours=duration // 60, minutes=duration % 60)
    return start_time < now < end_time


def calclulate_end_time(start, duration):
    start_time = datetime.datetime.now().replace(hour=start, minute=0)
    return start_time + datetime.timedelta(hours=duration // 60, minutes=duration % 60)


def _schedule_duplicate_scan(scheduler, timezone_info):
    """Schedule background duplicate scan based on CWA settings."""
    try:
        import sys as _sys
        if '/app/calibre-web-automated/scripts/' not in _sys.path:
            _sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB
        from .tasks.duplicate_scan import TaskDuplicateScan
        from apscheduler.triggers.cron import CronTrigger

        db = CWA_DB()
        enabled = bool(db.cwa_settings.get('duplicate_scan_enabled', 0))
        cron_expr = (db.cwa_settings.get('duplicate_scan_cron') or '').strip()

        if not enabled:
            return

        if cron_expr:
            trigger = CronTrigger.from_crontab(cron_expr, timezone=timezone_info)
        else:
            # manual/after_import handled elsewhere
            return

        scheduler.schedule_task(lambda: TaskDuplicateScan(full_scan=True, trigger_type='scheduled'),
                                user='System', trigger=trigger, name='duplicate scan', hidden=False)
    except Exception:
        # Scheduling is best-effort and never blocks startup, but a job that is missing
        # must show up in the log
        log.exception("scheduler: job setup failed; continuing with remaining jobs")


def _schedule_db_backup(scheduler, start_hour, timezone_info):
    """Nightly sqlite snapshots of app.db, cwa.db and metadata.db at the start of the
    configured maintenance window. Kept separate from get_scheduled_tasks() so it is not
    re-run every time the schedule is re-registered or tasks are kicked off immediately."""
    try:
        from .tasks.db_backup import TaskBackupDatabases
        scheduler.schedule_task(lambda: TaskBackupDatabases(), user='System',
                                trigger=CronTrigger(hour=start_hour, minute=0, timezone=timezone_info),
                                name='backup databases', hidden=False)
    except Exception:
        # Scheduling is best-effort and never blocks startup, but a job that is missing
        # must show up in the log
        log.exception("scheduler: job setup failed; continuing with remaining jobs")


def _schedule_library_mirror(scheduler, start_hour, timezone_info):
    """Nightly copy of new/changed book files to cwa_settings.library_mirror_dir. Always
    scheduled (hidden); the task does nothing while no mirror folder is set."""
    try:
        from .tasks.library_mirror import TaskMirrorLibrary
        scheduler.schedule_task(lambda: TaskMirrorLibrary(), user='System',
                                trigger=CronTrigger(hour=start_hour, minute=45, timezone=timezone_info),
                                name='mirror library files', hidden=True)
    except Exception:
        # Scheduling is best-effort and never blocks startup, but a job that is missing
        # must show up in the log
        log.exception("scheduler: job setup failed; continuing with remaining jobs")


def _schedule_processed_books_cleanup(scheduler, start_hour, timezone_info):
    """Nightly cleanup of /config/processed_books: the retired imported/ copies go whole, failed/
    by age (cwa_settings.processed_books_retention_days, default 30, 0 = keep forever)."""
    try:
        from .tasks.processed_cleanup import TaskCleanProcessedBooks
        scheduler.schedule_task(lambda: TaskCleanProcessedBooks(), user='System',
                                trigger=CronTrigger(hour=start_hour, minute=30, timezone=timezone_info),
                                name='clean processed books', hidden=True)
    except Exception:
        # Scheduling is best-effort and never blocks startup, but a job that is missing
        # must show up in the log
        log.exception("scheduler: job setup failed; continuing with remaining jobs")
