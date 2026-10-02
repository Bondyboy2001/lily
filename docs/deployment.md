# Deployment

Details beyond the [README](../README.md) quick start.

## Reverse proxies

Lily works behind nginx, Caddy, Traefik or Cloudflare Tunnel. By default it trusts no
`X-Forwarded-*` headers, so a client reaching Lily directly can't spoof its IP. Set
`TRUSTED_PROXY_COUNT` to the number of proxies in front of it:

| Chain | `TRUSTED_PROXY_COUNT` |
|---|---|
| Direct | `0` |
| Proxy → Lily | `1` |
| Cloudflare Tunnel → proxy → Lily | `2` |

Frequent "Session protection triggered" warnings in the log mean the count is too low.
Set `SESSION_COOKIE_SECURE=true` when the proxy serves HTTPS.

## Network shares

Set `NETWORK_SHARE_MODE=true` when the library or `/config` is on NFS or SMB. It turns
off SQLite WAL (share locking is unreliable), skips the recursive `chown` of the library
and watches the ingest folder by polling instead of `inotify`.

Docker Desktop on macOS and Windows is detected and polls too. `CWA_WATCH_MODE=poll`
forces polling anywhere.

## Environment variables

| Variable | Default | |
|---|---|---|
| `PUID` / `PGID` | `1000` | Owner of the files Lily writes |
| `TZ` | — | Timezone |
| `HARDCOVER_TOKEN` | — | Hardcover API key |
| `CROSSREF_MAILTO` | — | Contact address for Crossref paper searches |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Semantic Scholar API key for paper searches |
| `SECRET_KEY` | generated | Session signing key |
| `TRUSTED_PROXY_COUNT` | `0` | See above |
| `SESSION_COOKIE_SECURE` | `false` | Secure cookies over HTTPS |
| `NETWORK_SHARE_MODE` | `false` | See above |
| `CWA_WATCH_MODE` | `auto` | `poll` forces polling |
| `CWA_PORT_OVERRIDE` | `8083` | Port inside the container |
| `DB_BACKUP_DIR` | `/config/backup/db` | Snapshot folder |
| `DISABLE_LIBRARY_AUTOMOUNT` | `false` | Skip library detection at start |

## Backups

Every night Lily snapshots `app.db`, `cwa.db` and the library's `metadata.db` into
`/config/backup/db/<timestamp>/` and keeps the last seven. Each new snapshot is test
restored; a failure shows in the log. Snapshots on the `/config` volume won't survive
losing it, so mount another folder and point `DB_BACKUP_DIR` at it.

To restore one:

1. `docker compose stop lily`
2. Copy the current databases somewhere safe.
3. Copy the snapshot's `app.db` and `cwa.db` into `/config`, and `metadata.db` into the
   library folder.
4. Delete any `-wal` and `-shm` files next to the restored databases.
5. `docker compose start lily`

Snapshots are plain SQLite files, so `sqlite3` can open one to recover single rows.

## Upgrading

Pull the new image and recreate the container: `docker compose pull && docker compose up -d`.
Check `/health` returns 200. To roll back, run the previous tag and restore the snapshot
taken before the upgrade.

## Releases

Pushing a `v*` tag builds amd64 and arm64 images and publishes them to
`ghcr.io/bondyboy2001/lily`, and to Docker Hub when its secrets are set
(`.github/workflows/release.yml`).
