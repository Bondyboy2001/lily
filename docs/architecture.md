# Architecture

How Lily's code is laid out. For running it, see the [README](../README.md) and
[deployment notes](deployment.md).

## Processes

One container runs several [s6](https://skarnet.org/software/s6-overlay/) services
(`root/etc/s6-overlay/s6-rc.d/`):

| Service | Does |
|---|---|
| `svc-calibre-web-automated` | The web app (`cps.py` → `cps/`). |
| `cwa-ingest-service` | Watches the ingest folder and runs `scripts/ingest_processor.py` on new files. |
| `metadata-change-detector` | Notices metadata changes and runs `scripts/cover_enforcer.py`. |
| `cwa-auto-library`, `cwa-auto-zipper`, `cwa-process-recovery`, `cwa-init` | First-start library setup, archive zipping, crash recovery, initialisation. |

The ingest and enforcement processes call back into the web app over internal endpoints
guarded by `cps/internal_api.py` (`@internal_only`, shared secret from
`scripts/cwa_internal_auth.py`).

## Databases

| File | Owner | Holds |
|---|---|---|
| `metadata.db` | Calibre | The library: books, authors, tags, comments, identifiers (`cps/db.py`). |
| `app.db` | Lily | Users, shelves, read status, sessions, queues, server settings (`cps/ub.py`, `cps/config_sql.py`). |
| `cwa.db` | Lily | Lily settings and statistics (`scripts/cwa_db.py`, schema in `scripts/cwa_schema.sql`). |
| `gdrive.db` | Lily | Google Drive settings (`cps/gdriveutils.py`). |

New `app.db` columns and tables are added by the migrations in `ub.py`; new `cwa.db`
settings are columns in `cwa_schema.sql`, which is synced on start.

## The web app (`cps/`)

`main.py` registers every blueprint. A few modules are deliberately split into several
files that attach their routes to **one** blueprint, so endpoint names (`web.login`,
`admin.db_backups`, `edit-book.upload`) stay stable:

| Blueprint | Files |
|---|---|
| `web` | `web.py` (browsing, details, reader entry), `web_auth.py` (sign-in, 2FA, password, profile), `web_lists.py` (author/series/... lists), `web_files.py` (covers, serving, downloads), `web_typeahead.py` |
| `admin` | `admin.py` (config, users, tasks), `admin_backups.py` (backups, restore, mirror, failed imports) |
| `edit-book` | `editbooks.py` (editing, deletion), `editbooks_upload.py`, `editbooks_bulk.py` |

The main file imports its siblings **at the bottom**, after everything they import from
it is defined. Keep it that way.

Other blueprints: `opds`, `shelf`, `search`, `metadata` (provider search), `tasks`,
`duplicates`, `gdrive`, `about`, `account_security` (2FA, API token), `reading`
(My Reading), `suggestions` (metadata suggestion review), and the `cwa_functions/`
package (Lily settings, stats, logs, ingest endpoints).

### Duplicates

`duplicate_rules.py` (pure rules: hashing, which book to keep) → `duplicate_detection.py`
(SQL and Python scans, dismissed groups) → `duplicate_index.py` (the key index) →
`duplicates.py` (routes and auto-resolution). `tasks/duplicate_scan.py` runs scans.

### Background tasks

`services/worker.py` runs `CalibreTask` subclasses from `tasks/` (backups, restore, library
mirror, thumbnails, duplicate scan, Hardcover and metadata suggestions, ...), one at a time,
with a watchdog that fails a task stuck past `LILY_TASK_TIMEOUT_HOURS` and moves the queue on.
`schedule.py` registers the recurring ones. Tasks that set `job_name` have their runs recorded
in the cwa.db `job_status` table (`services/job_status.py`), which drives the admin banner in
`layout.html` and the `checks` in `/health`.

### Pure modules

Logic that needs no Flask or database lives in Flask-free modules so it can be tested
alone: `cps/totp.py`, `scripts/db_backup.py`, `scripts/library_mirror.py`,
`scripts/job_status.py`, `scripts/ingest_failures.py`, `scripts/metadata_suggestions.py`,
`cps/duplicate_rules.py`.
The ones under `scripts/` are importable from both the web app and the ingest process;
the type-checked set is listed in `pyproject.toml` (`[tool.mypy]`).

## Security model

- Sessions: HttpOnly cookies, `SameSite` set, `Secure` when `SESSION_COOKIE_SECURE=true`.
- Optional TOTP second factor (`totp.py`, `web_auth.py`); with it on, OPDS uses a personal
  API token instead of the password. Wrong-code lockout is in memory, per process.
- CSP: `'unsafe-eval'` only on the book edit page and the readers (`web.py`,
  `_EVAL_ENDPOINTS`).
- Admin routes use `@admin_required`; `tests/unit/test_admin_access_control.py` checks them.

## Tests

| Where | What |
|---|---|
| `tests/unit/` | Fast tests. `lily_env.py` builds a real Flask app on real `app.db` and `metadata.db`; a few older duplicate tests exec modules against stubs (`duplicate_loader.py`). |
| `tests/smoke/` | Import and wiring checks. |
| `tests/e2e/smoke.mjs` | Browser run: sign in, visit pages, fail on HTTP errors and CSP violations. |
| `tests/integration/`, `tests/docker/` | Full container runs (CI: main/dev). |

New blueprints must be added in `main.py` **and** `tests/unit/lily_env.py`.

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m ruff check cps scripts tests
.venv/bin/python -m mypy
```
