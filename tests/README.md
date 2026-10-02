# Lily tests

## Layout

| Path | What it holds |
|---|---|
| `unit/` | Fast tests, no Docker. `lily_env.py` builds a real Flask app on a real `app.db` and `metadata.db`. The `test_lily_*_static.py` files check templates, CSS and JS against `docs/design.md`. |
| `smoke/` | Import and wiring checks: the app imports, `cwa.db` initialises, the ingest lock works. |
| `integration/` | Ingest runs against a real Lily container: drop a book in the ingest folder, check it lands in `metadata.db` and `cwa.db`. Needs Docker; skipped without it. |
| `e2e/smoke.mjs` | Node Playwright run against a live server: sign in, visit pages, fail on HTTP errors and CSP violations. |
| `fixtures/` | Sample Gutenberg EPUBs and `generate_synthetic.py`, which builds a minimal EPUB on the fly. |

`conftest.py` marks everything under `unit/` and `smoke/` as `unit`/`smoke`, and holds the container fixtures the integration tests use.

## Running locally

```bash
# Smoke + unit (what CI's Fast Tests job runs)
.venv/bin/python -m pytest -m "smoke or unit" -n auto

# Shared UI checks (run before finishing any UI change)
.venv/bin/python -m pytest tests/unit/test_lily_library_static.py tests/unit/test_lily_design_static.py \
  tests/unit/test_lily_admin_static.py tests/unit/test_lily_duplicates_static.py tests/unit/test_lily_reader_static.py

# Integration (builds and starts a container; run without -n)
.venv/bin/python -m pytest tests/integration

# Browser smoke against a running server
cd tests/e2e && npm install --no-save playwright && BASE_URL=http://localhost:8083 node smoke.mjs
```

## CI

`.github/workflows/tests.yml` runs on every push:

- **Lint**: ruff, vulture and mypy.
- **Fast Tests**: `pytest -m "smoke or unit" -n auto` on Python 3.13 with the `requirements.lock` pins.
- **Docker Build** (pull requests): builds the image, waits for `/health`, then runs `e2e/smoke.mjs`.
- **Integration Tests** (pushes to `main`, or a manual run with `run_integration`): `pytest tests/integration`.

`.github/workflows/release.yml` repeats lint, the smoke and unit tests and a container `/health` check before it publishes the image.
