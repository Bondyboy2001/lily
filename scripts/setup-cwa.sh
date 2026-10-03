#!/bin/bash
# Lays out the image's own files under a staging root (the Dockerfile's app stage, which
# copies it onto / in one layer): the app's writable dirs and permissions, the s6
# services, and the CLI aliases. Run as root from <staging root>/app/calibre-web-automated.
set -euo pipefail
out=${1:?usage: setup-cwa.sh <staging root>}

# s6 service definitions and other rootfs overlays live in ./root
mkdir -p "$out"
cp -R root/. "$out"/
rm -R root/

# Required directories for metadata enforcement and the ingest/library mounts
install -d -o abc -g abc metadata_change_logs metadata_temp
install -d -o abc -g abc "$out"/cwa-book-ingest "$out"/calibre-library

# Ownership and permissions
chown -R abc:abc "$out"/etc/s6-overlay
chmod +x "$out"/etc/s6-overlay/s6-rc.d/{cwa-auto-library,cwa-ingest-service,cwa-init,cwa-process-recovery,metadata-change-detector,calibre-binaries-setup,svc-calibre-web-automated}/run

# Aliases for `docker exec -it lily bash` (root's .bashrc reads ~/.bash_aliases)
install -d -m 700 "$out"/root
cat > "$out"/root/.bash_aliases << 'ALIASES'
# Calibre-Web Automated Aliases
alias cwa-check='bash /app/calibre-web-automated/scripts/check-cwa-services.sh'
alias cwa-change-dirs='nano /app/calibre-web-automated/dirs.json'
cover-enforcer () {
    python3 /app/calibre-web-automated/scripts/cover_enforcer.py "$@"
}
ALIASES
