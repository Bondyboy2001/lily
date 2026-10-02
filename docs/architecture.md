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
| `cwa-auto-library`, `cwa-process-recovery`, `cwa-init` | First-start library setup, crash recovery, initialisation. |

The ingest and enforcement processes call back into the web app over internal endpoints
guarded by `cps/internal_api.py` (`@internal_only`, shared secret from
`scripts/cwa_internal_auth.py`).

## Databases

| File | Owner | Holds |
|---|---|---|
| `metadata.db` | Calibre | The library: books, authors, tags, comments, identifiers (`cps/db.py`). |
| `app.db` | Lily | Users, shelves, read status, sessions, queues, server settings (`cps/ub.py`, `cps/config_sql.py`). |
| `cwa.db` | Lily | Lily settings and statistics (`scripts/cwa_db.py`, schema in `scripts/cwa_schema.sql`). |

New `app.db` columns and tables are added by the migrations in `ub.py`; new `cwa.db`
settings are columns in `cwa_schema.sql`, which is synced on start.

## The web app (`cps/`)

`main.py` registers every blueprint. A few modules are deliberately split into several
files that attach their routes to **one** blueprint, so endpoint names (`web.login`,
`admin.edit_user`, `edit-book.upload`) stay stable:

| Blueprint | Files |
|---|---|
| `web` | `web.py` (browsing, details, reader entry), `web_auth.py` (sign-in, password, profile), `web_lists.py` (author/series/... lists), `web_files.py` (covers, serving, downloads), `web_typeahead.py` |
| `admin` | `admin.py` (users and their restrictions, maintenance endpoints) |
| `edit-book` | `editbooks.py` (editing, deletion), `editbooks_upload.py`, `editbooks_bulk.py` |

The main file imports its siblings **at the bottom**, after everything they import from
it is defined. Keep it that way.

Other blueprints: `opds`, `shelf`, `search`, `metadata` (provider search), `duplicates`,
`logs`, and the `cwa_functions/` package (Import & Metadata settings, service
status, ingest endpoints). There are no settings pages for the library location, backups,
book recovery, failed imports, statistics or tasks: the library is found at
`/calibre-library`, and backups, delete recovery and failed-import handling run on their
defaults without a UI.

### Duplicates

`duplicate_rules.py` (pure rules: hashing, which book to keep) → `duplicate_detection.py`
(SQL and Python scans, dismissed groups) → `duplicate_index.py` (the key index) →
`duplicates.py` (routes and auto-resolution). `tasks/duplicate_scan.py` runs scans.

### Background tasks

`services/worker.py` runs `CalibreTask` subclasses from `tasks/` (database backups, library
mirror, thumbnails, duplicate scan, ...).
`schedule.py` registers the recurring ones.

### Pure modules

Logic that needs no Flask or database lives in Flask-free modules so it can be tested
alone: `scripts/db_backup.py`, `scripts/library_mirror.py`,
`scripts/ingest_failures.py`, `scripts/metadata_suggestions.py`, `cps/duplicate_rules.py`.
The ones under `scripts/` are importable from both the web app and the ingest process;
the type-checked set is listed in `pyproject.toml` (`[tool.mypy]`).

## Security model

- Sessions: HttpOnly cookies, `SameSite` set, `Secure` when `SESSION_COOKIE_SECURE=true`.
- CSP: `'unsafe-eval'` only on the book edit page and the readers (`web.py`,
  `_EVAL_ENDPOINTS`).
- Admin routes use `@admin_required`; `tests/unit/test_admin_access_control.py` checks them.

## Tests

| Where | What |
|---|---|
| `tests/unit/` | Fast tests. `lily_env.py` builds a real Flask app on real `app.db` and `metadata.db`; a few older duplicate tests exec modules against stubs (`duplicate_loader.py`). |
| `tests/smoke/` | Import and wiring checks. |
| `tests/e2e/smoke.mjs` | Browser run: sign in, visit pages, fail on HTTP errors and CSP violations. |
| `tests/integration/` | Ingest runs against a real container (CI: pushes to main). |

New blueprints must be added in `main.py` **and** `tests/unit/lily_env.py`.

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m ruff check cps scripts tests
.venv/bin/vulture
.venv/bin/python -m mypy
```
