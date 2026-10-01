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
| `DB_BACKUP_DIR` | `/config/backup/db` | Where database snapshots go when the backups page leaves the folder empty. |
| `LILY_TASK_TIMEOUT_HOURS` | `6` | A background task running longer than this is marked failed and the queue moves on (see [Background jobs](#background-jobs)). `0` turns the watchdog off. |

---

## Database backups and restore

Lily snapshots `app.db`, `cwa.db` and your library's `metadata.db` nightly at the start
of the maintenance window, into `/config/backup/db/<timestamp>/` unless you choose another
folder. Everything below is set under Settings → Database Backups (`/admin/db_backups`),
which also has a **Back up databases** button for a snapshot right now (before an upgrade,
say).

### Where to keep them

The default `/config/backup/db` is usually on the same disk as `/config` and often the
library too, so a failed disk takes the books, the databases and their backups at once.
Mount a folder on a **different disk** (e.g. at `/backups`, see `docker-compose.yml`) and
either set `DB_BACKUP_DIR=/backups` or enter the folder on the backups page (the setting
wins over the env var). The page warns when the backup folder is on the same device as
the library.

Database snapshots do not contain book files. For those, set a **library mirror folder**
on a separate disk (e.g. `/backups/library` on the volume mounted at `/backups` in
`docker-compose.yml`). Each night, 45 minutes into the
maintenance window, new and changed book files and covers are copied there:

- Nothing is deleted from the mirror, so a book removed by mistake is still there.
- When a file changes, the previous mirror copy is moved to
  `<mirror>/.versions/<YYYY-MM-DD>/<path in the library>` first. Version folders older
  than 30 days (configurable, 0 = keep forever) are removed.
- A replacement that looks like damage is **not** copied and the task fails with
  "suspicious file(s) not replaced": an epub, kepub or cbz that fails zip validation, or
  any file that lost more than half its size. The good copy stays. If the change was
  intended, move the mirror copy away and run the mirror again.

Until a mirror folder is set the backups page shows a warning that book files are not
backed up.

### Retention

Snapshots are kept grandfather-father-son style, counted per database:

| Tier | Default | Keeps |
|---|---|---|
| Daily | 7 | The newest 7 snapshots |
| Weekly | 4 | The newest snapshot of each of the last 4 weeks |
| Monthly | 6 | The newest snapshot of each of the last 6 months |

The tiers overlap (this week's newest snapshot is also a daily one); set weekly or monthly
to 0 to turn that tier off. On top of that:

- Every new snapshot is restored into a scratch folder to prove it works and gets a
  `.verified` marker. The newest verified snapshot is never deleted.
- If verification fails, nothing is pruned that night.
- If the new `metadata.db` has fewer than 90% of the books in the previous good snapshot,
  the snapshot is marked **suspicious** (`.suspicious` marker, shown on the backups page),
  the task fails with a warning and nothing is pruned. This repeats every night until the
  books come back or you press **Accept** on the snapshot to confirm the deletion was
  intended; pruning then resumes from the next backup.
- Counting per database means that if one database's backups keep failing, its last good
  snapshots are kept.

The same page sets how long copies in `/config/processed_books/imported` and `failed`
are kept (default 30 days, 0 = forever); older files are removed nightly.

### Background jobs

The backup, the mirror, the processed-books cleanup, library-wide thumbnail generation
and duplicate scans record their last start, success and failure in `cwa.db`
(`job_status`). Admins see a banner at the top of every page when an enabled job's last
run failed, or when the backup, mirror or cleanup hasn't succeeded for 36 hours. The
backups page shows the last successful backup and mirror. `/health` includes the same
information under `checks` (per-job state, `backup_age_hours`); it never changes the HTTP
status, so the Docker healthcheck only reacts to the library being unreadable.

Tasks run one at a time. Conversions are killed after 10 minutes plus 1 minute per MB of
input (at most 4 hours). Any task that runs longer than `LILY_TASK_TIMEOUT_HOURS`
(default 6; the mirror is allowed 24) is marked failed and the queue moves on to the next
task. Python can't kill the stuck thread, so it is left running in the background; restart
Lily if the log shows a task watchdog message and something stays locked.

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
1. Build the image: `docker buildx build --platform linux/amd64 -t lily:nas-amd64 --load .`
   (retry if gcc segfaults building `faust-cchardet`), then `docker save lily:nas-amd64 | gzip > lily-amd64.tar.gz`.
2. Copy the tarball to the NAS `docker/lily/` share and `docker load -i lily-amd64.tar.gz`.
3. Stop any other app using the same library, and back up `metadata.db`, `app.db` and `cwa.db` first.
4. `docker compose up -d`. The first start can take about 2 minutes (ownership fix on a large library).
5. Check `/health` and the log for `Starting Calibre Web...`.

### Upgrading
Take a snapshot (Settings -> Database Backups -> Back up databases), deploy the new image, confirm `/health`.
To roll back, redeploy the previous image tag and restore the snapshot if migrations ran.

### Verifying that backups restore
The nightly backup task restores every new snapshot into a scratch directory and fails loudly if it
does not open or has no tables. To check by hand, from the source tree (use your backup folder if
you changed it):

```
python scripts/db_backup.py /config/backup/db            # newest snapshot
python scripts/db_backup.py /config/backup/db 20260930_020000
```

### Failure modes worth knowing
- Startup now aborts if Flask-WTF (CSRF) or Flask-Limiter is not installed, instead of running unprotected.
- Backup failures, verification failures and suspicious (shrunken) backups appear as a failed
  "Backup Databases" task in the task list and in the admin banner.
- A mirror that refused a damaged-looking file fails with "suspicious file(s) not replaced";
  the log names each file.
