# Copilot Instructions for Calibre-Web Automated

## Project Overview
Calibre-Web Automated (CWA) is a fork of Calibre-Web that adds automated ebook processing and management. It's a Flask web application running in Docker with Python 3.13, combining a modern web UI with Calibre's command-line tools for ebook manipulation.

## Architecture

### Multi-Process Service Model (s6-overlay)
CWA uses **s6-overlay** for process supervision. Services are defined in `/root/etc/s6-overlay/s6-rc.d/`:
- **cwa-init**: One-time initialization (directory setup, permissions, Qt6 compatibility checks)
- **svc-calibre-web-automated**: Main Flask application
- **cwa-ingest-service**: File watcher that triggers ebook import via `ingest_processor.py`
- **metadata-change-detector**: Monitors `metadata.db` for changes to trigger cover/metadata enforcement
- **cwa-auto-zipper**: Daily compression of processed book backups
- **cwa-auto-library**: Automatic library detection and mounting

Services communicate via filesystem locks (`/tmp/*.lock`), SQLite databases, and status files (`/config/cwa_ingest_status`).

### Database Architecture
**Three separate SQLite databases** (never consolidate):
1. **`metadata.db`** (Calibre library) - Book metadata, managed by Calibre tools
2. **`app.db`** (Flask settings) - User accounts, permissions, CW configuration in `_Settings` table
3. **`cwa.db`** (CWA tracking) - Stats, settings, and audit logs. Schema in `scripts/cwa_schema.sql`, accessed via `CWA_DB` class

**WAL Mode**: Enabled by default on local disks for better concurrency. Disabled when `NETWORK_SHARE_MODE=true` due to NFS/SMB limitations.

### Flask Blueprint Organization
Core blueprints in `cps/main.py`:
- **CWA-specific**: `switch_theme`, `library_refresh`, `cwa_stats`, `cwa_settings`, `cwa_logs`, `profile_pictures`
- **Stock CW**: `web`, `opds`, `admin`, `editbook`, `shelf`, etc.

Each blueprint is a self-contained module in `cps/` (e.g., `cps/web.py`, `cps/cwa_functions.py`).

### Background Task System
**WorkerThread** (`cps/services/worker.py`) manages async tasks:
- Tasks inherit from `CalibreTask` base class
- Common tasks: `TaskConvert`, `TaskEmail`, `TaskBackupMetadata`, `TaskGenerateCoverThumbnails`
- Scheduled via APScheduler in `cps/schedule.py` (e.g., daily backups, thumbnail generation)
- Tasks stored in ImprovedQueue, status tracked with constants: `STAT_WAITING`, `STAT_STARTED`, `STAT_FINISH_SUCCESS`, etc.

### Automation Scripts
Python scripts in `/app/calibre-web-automated/scripts/`:
- **`ingest_processor.py`**: Core ingest logic - file validation, Calibre import (files are imported as-is, never converted)
- **`cover_enforcer.py`**: Applies UI metadata changes to actual ebook files using `ebook-meta`
- **`cwa_db.py`**: Database wrapper class for CWA tracking database
- **`auto_library.py`**: Library auto-detection and mounting logic

Scripts use **filesystem locks** to prevent concurrent execution (e.g., `ingest_processor.lock`).

## Development Workflows

### Local Development Setup
1. **Build the image**: `docker compose up -d --build`
2. **Development compose**: Use `docker-compose.yml.dev` with volume mounts for live-reload:
   ```yaml
   volumes:
     - ./cps:/app/calibre-web-automated/cps  # Live-edit Python
     - ./scripts:/app/calibre-web-automated/scripts
   ```
3. **Start container**: `docker compose -f docker-compose.yml.dev up -d`
4. **Default login**: harry/harry10 (change immediately)

### Testing Strategy
**No formal test suite exists** - manual testing workflow:
1. Drop test ebooks into `/cwa-book-ingest` bind
2. Monitor logs: `docker logs -f calibre-web-automated` or CWA Logs page in UI
3. Check CWA Stats page for import/enforcement counts
4. Verify `cwa.db` tables for audit trails: `cwa_import`, `cwa_enforcement`

### Debugging Techniques
- **Service-specific logs**: Check `/config/log_archive/` for timestamped logs from each service
- **Lock file inspection**: Check `/tmp/*.lock` to identify stuck processes
- **Database queries**: Use `sqlite3 /config/cwa.db` to inspect stats/settings
- **Ingest queue**: Check `/config/cwa_ingest_retry_queue` for pending files
- **Status tracking**: Read `/config/cwa_ingest_status` for current ingest state

### Common Calibre Commands
CWA shells out to Calibre binaries (installed in `/app/calibre/`):
- **Import**: `calibredb add <file> --library-path=/calibre-library`
- **Convert**: `ebook-convert input.azw output.epub` (28 supported input formats)
- **Metadata**: `ebook-meta file.epub --set-cover=cover.jpg --title="New Title"`
- **Library check**: `calibredb list --library-path=/calibre-library --limit=1`

Always use `--library-path` flag explicitly. Never modify `metadata.db` directly.

## Code Conventions

### Error Handling Pattern
```python
from cps import logger
log = logger.create()

try:
    # operation
except SpecificError as e:
    log.error(f"Context-specific error message: {e}")
    flash(_("User-facing error message"), category="error")
    # Always continue or return gracefully
```

### Database Session Management
**SQLAlchemy sessions must be managed per-thread**:
```python
from cps.ub import init_db_thread
init_db_thread()  # Call at start of any background task/script

# Use scoped sessions from calibre_db
from cps import calibre_db
calibre_db.ensure_session()  # Before each request
```

### File Path Conventions
- **Ingest folder**: `/cwa-book-ingest` (DESTRUCTIVE - files deleted after processing)
- **Calibre library**: `/calibre-library` (contains `metadata.db` and book folders)
- **Processed backups**: `/config/processed_books/{imported,failed}/`
- **Config/settings**: `/config/` (contains all three SQLite databases)
- **Temp files**: `/config/.cwa_conversion_tmp/` (cleaned by scheduled tasks)

### Blueprint Route Decorators
Use consistent decorator stacking:
```python
@blueprint.route('/path')
@login_required_if_no_ano  # Respects anonymous browsing setting
@limiter.limit("10 per minute")  # Rate limiting when enabled
def route_handler():
    # CWA custom auth check
    if not current_user.role_admin():
        abort(403)
```

### Translation Support
All user-facing strings must use Babel:
```python
from flask_babel import gettext as _, lazy_gettext as _l

flash(_("Book successfully imported"))  # Runtime translation
form_label = _l("Upload File")  # Lazy eval for form definitions
```

Compile translations: `scripts/compile_translations.sh`

## Critical Integration Points

### Network Share Mode
Set `NETWORK_SHARE_MODE=true` environment variable when deploying on NFS/SMB:
- **Disables**: WAL mode on databases, recursive `chown` operations
- **Enables**: Polling-based file watcher instead of inotify (unreliable on network mounts)
- **Fallback watcher**: `scripts/watch_fallback.py` polls every 5 seconds instead of using `inotifywait`

### Metadata Provider System
Pluggable providers in `cps/metadata_provider/`:
- **Enabled providers**: Configured via JSON in `cwa_settings.metadata_providers_enabled`
- **Provider types**: Google Books (default), Hardcover (requires API key), ISBNdb, Goodreads (deprecated)
- **Hardcover**: Set `HARDCOVER_TOKEN` env var for API access

## File Format Support
**Import formats**: epub, pdf and djvu (plus audiobooks)

**Special handling**:
- **Audiobooks**: M4B/M4A support in `scripts/audiobook.py` (experimental)

## Environment Variables
- `TZ`: Timezone for scheduling (default: UTC)
- `PUID`/`PGID`: User/group IDs for file ownership (default: 1000)
- `CWA_PORT_OVERRIDE`: Override default port 8083
- `NETWORK_SHARE_MODE`: Enable NFS/SMB compatibility mode
- `CWA_WATCH_MODE`: Force polling watcher (`poll`) or inotify (default)
- `HARDCOVER_TOKEN`: API key for Hardcover metadata provider
- `COOKIE_PREFIX`: Custom prefix for session cookies
- `TRUSTED_PROXY_COUNT`: Number of proxies to trust for X-Forwarded-* headers (default: 1, use 2+ for CF Tunnel + reverse proxy)

## Common Pitfalls
1. **Don't import SQLite on main thread**: Always use `init_db_thread()` in background tasks
2. **Never directly edit metadata.db**: Use `calibredb` or Calibre API
3. **Lock files**: Clean `/tmp/*.lock` files when debugging stuck processes
4. **File watching**: Docker Desktop + inotify = unreliable; auto-detected and switched to polling
5. **WAL mode errors**: Usually means network share deployment without `NETWORK_SHARE_MODE=true`
6. **Port binding**: Ports below 1024 need `cap_add: [NET_BIND_SERVICE]` in docker-compose
7. **Calibre plugins**: Requires `customize.py.json` in `/config/.config/calibre/` to register
8. **Session protection errors**: Behind multiple proxies? Set `TRUSTED_PROXY_COUNT` to match your proxy chain depth

## Version Management
- **Installed version**: `/app/CWA_RELEASE` (baked at build time)
- **Latest stable**: `/app/CWA_STABLE_RELEASE` (fetched at startup from GitHub API)
- **Update notifications**: Shown in UI when installed < stable (admin users only)
- **Build args**: `VERSION` and `BUILD_DATE` in Dockerfile

## Contributing Guidelines
- Follow SPDX headers in all Python files
- Update `CONTRIBUTORS` file for new contributors
- Open an issue on Bondyboy2001/lily to discuss major changes first
- Test with both local disk and network share deployments
