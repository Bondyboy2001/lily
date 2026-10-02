# Running Integration Tests in Docker-in-Docker Environments

## Problem

When running Lily's integration tests inside a Docker container (like a dev container), bind mounts don't work because:

1. Tests create bind mounts from paths like `/tmp/pytest-xxx`
2. These paths exist **inside the dev container**
3. Docker daemon runs on the **host machine**
4. Host Docker daemon can't access paths inside the dev container
5. Result: Test container sees empty directories

## Solution

Use Docker volumes instead of bind mounts when in Docker-in-Docker scenarios.

## Usage

### For CI/CD (Default - Uses Bind Mounts)

```bash
# Run tests normally
pytest tests/integration/ -v
```

This uses the standard bind mount approach which works perfectly in GitHub Actions and other CI environments.

### For Local Dev Containers (Uses Docker Volumes)

```bash
# Set environment variable
export USE_DOCKER_VOLUMES=true

# Run tests
pytest tests/integration/ -v
```

Or as a one-liner:

```bash
USE_DOCKER_VOLUMES=true pytest tests/integration/ -v
```

## How It Works

1. **conftest.py** checks the `USE_DOCKER_VOLUMES` environment variable
2. If `true`, imports fixtures from **conftest_volumes.py**
3. Overrides `test_volumes`, `cwa_container`, `ingest_folder`, `library_folder` fixtures
4. These use Docker volumes instead of bind mounts
5. VolumeHelper class provides file operations via `docker cp`

## Architecture

### Standard Mode (CI)
```
Host Machine
  └─> Test Container
      └─> Bind Mount: /tmp/pytest-xxx → /cwa-book-ingest
          ✅ Works: Docker daemon can access /tmp
```

### Docker Volume Mode (DinD)
```
Dev Container (tests run here)
  └─> Docker Daemon (on host)
      └─> Test Container
          └─> Docker Volume: cwa_test_ingest_xxx
              ✅ Works: Docker manages volumes on host
```

## VolumeHelper API

When `USE_DOCKER_VOLUMES=true`, fixtures return `VolumeHelper` instances instead of `Path`:

```python
# Copy file into volume
ingest_folder.copy_to(epub_path)

# Check if file exists
if ingest_folder.file_exists("book.epub"):
    ...

# Copy file out of volume
library_folder.copy_from("metadata.db", local_path)

# List files
files = library_folder.list_files("*.epub")

# Create subdirectories
sub_folder = ingest_folder / "batch"
```

## Test Compatibility

The original tests are **100% compatible** with both modes. No test code changes needed because:

1. `shutil.copy2(src, folder / "file")` works with Path objects ✅
2. `(folder / "file").exists()` works with Path objects ✅  
3. Standard Path operations all work ✅

The VolumeHelper class intentionally does NOT implement all Path methods to avoid confusion. Tests using advanced Path features will need adjustments when using volume mode.

## Files

- `tests/conftest.py` - Main fixtures, conditionally loads volume mode
- `tests/conftest_volumes.py` - Docker volume implementations

## Environment Detection

Volume mode is opt-in: set `USE_DOCKER_VOLUMES=true` to use it. In bind-mount mode, `conftest.py` also checks that the container can see a sentinel file in the ingest folder; if it can't, it falls back to copying files in and out with `docker cp`.
