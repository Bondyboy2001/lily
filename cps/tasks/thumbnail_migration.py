# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
from .. import logger, fs
from ..constants import CACHE_TYPE_THUMBNAILS, CONFIG_DIR

log = logger.create()

MIGRATION_MARKER = os.path.join(CONFIG_DIR, ".cwa_migrations", "thumbnail_flat_structure_v1")


def get_migration_status():
    """Check if the thumbnail migration has already been completed."""
    return os.path.isfile(MIGRATION_MARKER)


def set_migration_completed():
    """Mark the thumbnail migration as completed."""
    try:
        os.makedirs(os.path.dirname(MIGRATION_MARKER), exist_ok=True)
        with open(MIGRATION_MARKER, "w", encoding="utf-8") as marker:
            marker.write("done\n")
    except OSError as ex:
        log.error(f"Failed to mark thumbnail migration as completed: {ex}")


def migrate_thumbnail_structure():
    """
    One-time migration for existing CWA installations to move from
    subdirectory-based thumbnail storage to flat directory structure.

    This will:
    1. Clear all existing thumbnail database entries
    2. Remove old subdirectory structure
    3. Trigger regeneration of all thumbnails in new format
    """
    try:
        cache = fs.FileSystem()
        thumbnails_dir = cache.get_cache_dir(CACHE_TYPE_THUMBNAILS)

        # Check if migration is needed (look for old subdirectories)
        migration_needed = False
        subdirs_found = []

        if os.path.exists(thumbnails_dir):
            for item in os.listdir(thumbnails_dir):
                item_path = os.path.join(thumbnails_dir, item)
                # Look for hex subdirectories (00, 01, ..., ff, bo, etc.)
                if (os.path.isdir(item_path) and
                    len(item) == 2 and
                    item not in ['.', '..']):
                    subdirs_found.append(item)
                    migration_needed = True

        if not migration_needed:
            log.info("Thumbnail migration: No old subdirectories found, skipping migration")
            return

        log.info(f"Thumbnail migration: Found {len(subdirs_found)} old subdirectories with legacy thumbnails")
        log.info("Thumbnail migration: Using lazy migration strategy - legacy thumbnails will be replaced on-demand")
        log.info("Thumbnail migration: Old subdirectories will be cleaned up automatically as thumbnails regenerate")

        # Note: We don't delete thumbnails immediately anymore.
        # The TaskGenerateCoverThumbnails.create_book_cover_thumbnails() method already
        # detects legacy thumbnails (via legacy_naming check) and migrates them on-demand.
        # This prevents mass regeneration on first page load after update.

        # Mark migration as completed so this only runs once
        set_migration_completed()

    except Exception as ex:
        log.error(f"Thumbnail migration: Failed with error: {ex}")

def check_and_migrate_thumbnails():
    """
    Check if thumbnail migration is needed and run it if so.
    This should be called during application startup.
    """
    try:
        # Skip if already migrated
        if get_migration_status():
            log.debug("Thumbnail migration: Already completed, skipping")
            return

        migrate_thumbnail_structure()
    except Exception as ex:
        log.error(f"Thumbnail migration check failed: {ex}")
