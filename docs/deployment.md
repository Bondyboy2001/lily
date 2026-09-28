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
| `TRUSTED_PROXY_IPS` | `127.0.0.0/8,::1/128` (loopback only) | CIDRs allowed to send the *reverse proxy login header* (Admin → Configuration → "Allow Reverse Proxy Authentication"). The header is ignored from any other source address. Checked against the socket that actually connected, not `X-Forwarded-For`. If your proxy runs in another container or on another host, list its address, e.g. `172.18.0.5/32`. See [Reverse proxy authentication](#reverse-proxy-authentication-header-login) below. |
| `SESSION_COOKIE_SECURE` | `false` | Set to `true` when Lily is served over HTTPS, so session and remember-me cookies are only sent over HTTPS. |
| `NETWORK_SHARE_MODE` | `false` | See [Network shares](#network-shares-nfssmb) above. |
| `CWA_WATCH_MODE` | `auto` | Force `poll` to override watcher auto-detection. |
| `CWA_PORT_OVERRIDE` | `8083` | Change the web server port. |

### Reverse proxy authentication (header login)

With "Allow Reverse Proxy Authentication" enabled, Lily logs in whichever user the
configured header names, so it must only accept that header from your proxy.
`TRUSTED_PROXY_IPS` lists the addresses it is accepted from.

**Upgrade note:** earlier versions trusted loopback *and* every private range
(`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) by default, which let any
device on the LAN, or any container on a shared Docker network, log in as any user by
sending the header itself. The default is now loopback only. If header login stops
working after upgrading, Lily logs `Ignoring reverse proxy login header from untrusted
address <ip>`: add that address (your proxy's) to `TRUSTED_PROXY_IPS`, for example

```yaml
    environment:
      - TRUSTED_PROXY_IPS=172.18.0.5/32
```

Give the proxy container a fixed IP (or a dedicated Docker network with a small subnet)
so the entry stays valid. Setting the old private ranges again restores the previous
behaviour, but only do that if nothing untrusted can reach Lily's port directly.

Other hardening defaults:

- Failed KOReader (KOSync) logins are rate limited per username (5/minute, 60/hour) when
  the rate limiter is enabled. Successful syncs are never throttled.

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

---

## Gmail / email setup

Sending books over email uses Calibre's mail configuration at
`/app/calibre-web-automated/gmail.json`.

The upstream [Calibre-Web mailserver guide](https://github.com/janeczku/calibre-web/wiki/Setup-Mailserver#gmail)
covers it, but Gmail is a fiddly process — a plain SMTP server is usually less painful.
