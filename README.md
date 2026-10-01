<img src="cps/static/icon.png" width="120" alt="Lily">

# Lily

**A self-hosted digital library that gives you Calibre-Web's web UI with Calibre's full feature set.**

[![Docker Hub](https://img.shields.io/badge/docker-coldestpillow%2Flily-2496ed)](https://hub.docker.com/r/coldestpillow/lily)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![Upstream](https://img.shields.io/badge/fork%20of-Calibre--Web%20Automated-8a8a8a)](https://github.com/crocodilestick/calibre-web-automated)

<img src="README_images/CWA-Homepage.png" alt="Lily home screen">

Drop a book into the ingest folder. Lily imports it, fetches metadata, enforces your
cover, and backs it up — automatically.

---

## Why

[Calibre](https://calibre-ebook.com/) is powerful but awkward to containerise: it leans
on a KasmVNC server that is near-unusable on mobile and heavy on a small home server.
[Calibre-Web](https://github.com/janeczku/calibre-web) is lightweight with a modern UI,
but missing features that make many people run both in parallel.

Lily merges the two: Calibre-Web's interface, Calibre's capabilities, plus automation on
top.

## Features

Everything stock Calibre-Web does — per-user permissions, OPDS feeds, metadata editing,
in-browser reading, 20+ languages, content hiding — plus:

| | |
|---|---|
| **Automatic ingest** | Imports new EPUB, PDF and DjVu books as they arrive. |
| **Cover & metadata enforcement** | Edits made in the web UI are written back to the book files, not just the database. |
| **Library auto-detect** | No library? Lily creates one. Have one? Lily finds it and registers it. |
| **Duplicate detection** | Hybrid SQL + fuzzy matching, with one-click merge and scheduled scans. |
| **Automatic metadata fetch** | Optional on ingest, with provider fallback and fill-missing-only mode. |
| **Nightly backups** | Snapshots of all three databases in `/config/backup`, and a copy of every deleted book kept for a while in case you need it back. |
| **Batch edit & delete** | Select many books, act once. |
| **Update notifications** | In-app notice when a new release is available. |
| **Manual library refresh** | Re-process anything stranded in the ingest folder. |
| **Extra metadata providers** | Hardcover, Open Library, Google Scholar. |

Most of these are toggleable under Settings → Import & Metadata.

## Install

Multi-arch images (`linux/amd64`, `linux/arm64`) are published to Docker Hub as
**[`coldestpillow/lily`](https://hub.docker.com/r/coldestpillow/lily)** (the same images are
on GHCR as `ghcr.io/bondyboy2001/lily`). Each release is tagged `X.Y.Z`, `X.Y` and `latest`;
pin an exact version so an upgrade only happens when you change the tag.

```bash
mkdir lily && cd lily
$EDITOR docker-compose.yml    # paste the template below; set the version, timezone and paths
docker compose up -d
```

Then open <http://localhost:8083>. To build from source instead, clone the repo, uncomment
`build: .` in its `docker-compose.yml` and run `docker compose up -d --build`.

<details>
<summary>Full <code>docker-compose.yml</code> template</summary>

```yaml
services:
  lily:
    # Pin an exact release, e.g. coldestpillow/lily:1.2.3
    image: coldestpillow/lily:X.Y.Z
    container_name: lily
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/London
      # Hardcover API key, if you use Hardcover as a metadata provider
      # https://docs.hardcover.app/api/getting-started/
      - HARDCOVER_TOKEN=your_hardcover_api_key_here
      - NETWORK_SHARE_MODE=false
      - CWA_PORT_OVERRIDE=8083
      # Optional: your own private session-signing key (e.g. `openssl rand -hex 32`).
      # Without it Lily generates one and keeps it in app.db.
      # - SECRET_KEY=change-me-to-a-long-random-string
    volumes:
      # Config, logs, backups. Use an empty folder for a fresh install;
      # point at your existing /config to migrate from Calibre-Web.
      - /path/to/config/folder:/config
      # Ingest folder. Contents are DELETED after processing.
      - /path/to/ingest:/cwa-book-ingest
      # Your Calibre library. Empty is fine — Lily creates one.
      - /path/to/calibre/library:/calibre-library
      # Optional: Calibre plugins
      - /path/to/calibre/plugins:/config/.config/calibre/plugins
      # Optional: keep database backups off the config volume
      - /path/to/backups:/backups
    ports:
      - 8083:8083
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

</details>

### The three required volumes

Keep these as separate directories — nesting binds inside each other causes errors.

- **`/config`** — state that keeps Lily running. Any empty folder for a fresh install.
  If migrating from Calibre-Web, point this at your existing `/config` to carry over
  users and settings.
- **`/cwa-book-ingest`** — **everything here is deleted after processing.** Only dump
  finished downloads here; do not download directly into it.
- **`/calibre-library`** — your Calibre library folder. If several are present, Lily
  mounts the largest; check the logs to see which. One library per instance.

### After installing

1. Log in with the default credentials below. Lily sends you straight to a
   *Change Password* page and won't open anything else until you pick a new password.
2. **Settings → Import & Metadata**: turn on *Uploads from the browser* and choose the
   import and metadata options.
3. Make sure your library is mounted at `/calibre-library`; Lily finds it there (there is no
   page to point it elsewhere).
4. Drop a book in the ingest folder to confirm it works.

> **Default login** — username `harry`, password `harry10`. The password must be changed at
> first login; OPDS stays blocked until it is.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | `1000` | User and group IDs for file ownership |
| `TZ` | — | Your timezone, e.g. `Europe/London` |
| `HARDCOVER_TOKEN` | — | API key for the Hardcover metadata provider |
| `NETWORK_SHARE_MODE` | `false` | Set `true` for NFS/SMB libraries |
| `CWA_PORT_OVERRIDE` | `8083` | Change the web server port |
| `TRUSTED_PROXY_COUNT` | `0` | Number of trusted reverse proxies — set to `1` behind nginx/Caddy |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS |
| `SECRET_KEY` | generated | Private key that signs session cookies; set your own to keep it out of `app.db` |

Behind a reverse proxy or on a network share? See **[docs/deployment.md](docs/deployment.md)**.

## Migrating from Calibre-Web

1. Stop your Calibre-Web instance.
2. Map your old `/books` bind to Lily's `/calibre-library`, and mount the **same**
   `/config` folder (copy it first if you want a safety net).
3. `docker compose up -d`.

Your users, shelves and settings carry over. If the web UI doesn't load, start on the
same port Calibre-Web used.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt -c requirements.lock
.venv/bin/python -m pytest tests/unit -q          # unit tests (see pytest.ini for markers)
.venv/bin/python -m ruff check cps scripts tests
.venv/bin/python -m mypy
docker compose up -d --build                      # build and run an image from this checkout
```

[docs/architecture.md](docs/architecture.md) explains how the code is laid out, and
[docs/deployment.md](docs/deployment.md) how releases are published and deployed.

## Affiliated projects

- **[Shelfmark](https://github.com/calibrain/shelfmark)** — web interface for searching
  and requesting book downloads, designed to work with Lily.
- **[Calibre Web Companion](https://github.com/doen1el/calibre-web-companion)** — an
  unofficial Flutter app for browsing and downloading from Calibre-Web and Lily.
  [Google Play](https://play.google.com/store/apps/details?id=de.doen1el.calibreWebCompanion)
  · [F-Droid](https://f-droid.org/en/packages/de.doen1el.calibreWebCompanion/)

> Lily does not support or endorse piracy of copyrighted material, and is not
> responsible for user behaviour.

## Credits

Lily is a personal redesign of
[Calibre-Web Automated](https://github.com/crocodilestick/calibre-web-automated) by
crocodilestick and contributors, which is itself built on
[Calibre-Web](https://github.com/janeczku/calibre-web) and
[Calibre](https://calibre-ebook.com/). All of the heavy lifting is theirs — please
support the original project on
[Ko-fi](https://ko-fi.com/crocodilestick) or their
[Discord](https://discord.gg/EjgSeek94R).

Batch editing courtesy of [@jmarmarsh1207](https://github.com/jmarmstrong1207).
ibdb.dev access donated by [@chad3814](https://github.com/chad3814).

Licensed under [GPL-3.0](LICENSE).
