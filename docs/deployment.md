# Deployment notes

Technical deployment detail for Lily. For the quick start, see the [README](../README.md).

---

## Network shares (NFS/SMB)

Set `NETWORK_SHARE_MODE=true` when your library or config lives on a network share.

This single flag:

- disables SQLite WAL on `metadata.db` and `app.db` (NFS/SMB locking is unreliable)
- skips recursive `chown` of the library, which commonly fails on network filesystems
- switches the ingest and metadata watchers from `inotify` to polling

Network shares are slower than local disks, but they are fully supported with this enabled.

### SQLite WAL mode

Lily enables Write-Ahead Logging on local disks for better concurrency. Some network
filesystems do not fully support WAL or reliable file locking, which causes intermittent
`database is locked` errors and carries a corruption risk. `NETWORK_SHARE_MODE=true`
disables WAL on both the Calibre `metadata.db` and the `app.db` settings database.
Default is `false` (WAL enabled).

### File watching

By default Lily uses Linux `inotify` (via `inotifywait`) to detect new files in the
ingest folder, with minimal latency and overhead.

On network shares, filesystem events can be unreliable or unavailable, so
`NETWORK_SHARE_MODE=true` switches the ingest and metadata watcher services to a
polling watcher. This is more reliable on NAS mounts, at the cost of slightly higher
I/O and up to a few seconds of latency.

On Docker Desktop (Windows/macOS) the container runs in a LinuxKit/WSL2 VM and
host-mounted paths may not propagate `inotify` reliably. Lily auto-detects Docker
Desktop at startup and prefers the same polling watcher.

To force polling regardless of share mode, set `CWA_WATCH_MODE=poll`.

---

## Reverse proxies

Works with nginx, Caddy, Traefik, Cloudflare Tunnel and similar.

Lily uses Werkzeug's `ProxyFix` to handle `X-Forwarded-For`, `X-Forwarded-Proto` and
related headers. By default it trusts **no** proxy (`TRUSTED_PROXY_COUNT=0`) and ignores
`X-Forwarded-*` entirely, because trusting those headers while Lily is also reachable
directly lets any client spoof its IP and bypass login rate limits.

| Proxy chain | `TRUSTED_PROXY_COUNT` |
|---|---|
| Direct (no proxy) | `0` (default) |
| Cloudflare Tunnel → Lily | `1` |
| Cloudflare Tunnel → nginx → Lily | `2` |

**Why this matters:** session protection validates requests against the client IP and
rate limiting is keyed on it. Too few trusted proxies means the IP appears to change
between requests, causing "Session protection triggered" warnings and forced re-logins.
Too many means clients can forge their IP.

**Troubleshooting:** frequent session protection warnings in the logs means the proxy
chain depth is wrong — adjust this variable to match.

---

## Security environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies whose `X-Forwarded-*` headers are trusted. Set to `1` behind a single reverse proxy. |
| `SESSION_COOKIE_SECURE` | `false` | Set to `true` when Lily is served over HTTPS, so session and remember-me cookies are only sent over HTTPS. |
| `NETWORK_SHARE_MODE` | `false` | See [Network shares](#network-shares-nfssmb) above. |
| `CWA_WATCH_MODE` | `auto` | Force `poll` to override watcher auto-detection. |
| `CWA_PORT_OVERRIDE` | `8083` | Change the web server port. |

---

## Database backups and restore

Lily snapshots `app.db`, `cwa.db` and your library's `metadata.db` nightly at the start
of the maintenance window, into `/config/backup/db/<timestamp>/`, keeping the last 7 by
default (configurable in Lily Settings).

Snapshots on the same volume as `/config` will not survive losing that volume. To keep
them elsewhere, mount a separate folder (e.g. at `/backups`, see `docker-compose.yml`) and
either set `DB_BACKUP_DIR=/backups` or enter the folder under Settings → Database Backups
(`/admin/db_backups`; the setting wins over the env var). Rotation counts each database
separately, so if one database's backups keep failing its last good snapshots are kept.

The same page sets how long copies in `/config/processed_books/imported` and `failed`
are kept (default 30 days, 0 = forever); older files are removed nightly.

### Restoring a snapshot from the web UI

Settings → Database Backups lists every snapshot with its date, size and databases.
Restore runs as a background task (see Tasks): it checks each snapshot file with
`PRAGMA integrity_check`, saves the current databases to a `<timestamp>_pre-restore`
snapshot, pauses ingest, swaps the databases in and reconnects. Restart Lily after
restoring `app.db`. Pre-restore snapshots are never rotated; delete them by hand.

### Restoring a snapshot by hand

1. Stop the container: `docker compose stop lily`
2. Pick a snapshot folder, e.g. `/config/backup/db/20260927_030000/`
3. Keep a copy of the current files, then copy the snapshot over them:
   - `app.db` and `cwa.db` go to `/config/`
   - `metadata.db` goes to the root of your Calibre library
4. Delete any leftover `app.db-wal`/`-shm`, `cwa.db-wal`/`-shm` and
   `metadata.db-wal`/`-shm` next to the restored files — they belong to the old database
5. Start the container again: `docker compose start lily`

Snapshots are self-contained SQLite files with no `-wal` sidecar, so they can also be
opened directly with `sqlite3` to inspect or recover individual rows.


## Runbook

### Deploying to the NAS
If the NAS can reach Docker Hub, pull the published multi-arch image directly:
`docker pull coldestpillow/lily:latest`. To deploy to an offline NAS, build and transfer a
tarball instead:
1. Build the image: `docker buildx build --platform linux/amd64 -t lily:nas-amd64 --load .`
   (retry if gcc segfaults building `faust-cchardet`), then `docker save lily:nas-amd64 | gzip > lily-amd64.tar.gz`.
2. Copy the tarball to the NAS `docker/lily/` share and `docker load -i lily-amd64.tar.gz`.
3. Stop any other app using the same library, and back up `metadata.db`, `app.db` and `cwa.db` first.
4. `docker compose up -d`. The first start can take about 2 minutes (ownership fix on a large library).
5. Check `/health` and the log for `Starting Calibre Web...`.

### Upgrading
Take a snapshot (Admin -> Database backups -> Back up now), deploy the new image, confirm `/health`.
To roll back, redeploy the previous image tag and restore the snapshot if migrations ran.

### Verifying that backups restore
The nightly backup task restores every new snapshot into a scratch directory and fails loudly if it
does not open or has no tables. To check by hand, from the source tree:

```
python scripts/db_backup.py /config/backups            # newest snapshot
python scripts/db_backup.py /config/backups 20260930_020000
```

### Failure modes worth knowing
- Startup now aborts if Flask-WTF (CSRF) or Flask-Limiter is not installed, instead of running unprotected.
- Backup failures and verification failures appear as a failed "Backup Databases" task in the task list.
