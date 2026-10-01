#!/bin/bash

# The app is owned by root and read-only for abc. These are the only places under it that
# the services (running as abc) write: metadata change logs and export temp files for the
# cover/metadata enforcer, and the cache dir (cps/cache, CACHE_DIR). Keep this list in step
# with the cwa-init service, which re-owns the same directories on every start.
APP_WRITABLE_DIRS=(
    /app/calibre-web-automated/metadata_change_logs
    /app/calibre-web-automated/metadata_temp
    /app/calibre-web-automated/cps/cache
)

make_dirs () {
    chown -R root:root /app/calibre-web-automated
    chmod -R go-w /app/calibre-web-automated
    for dir in "${APP_WRITABLE_DIRS[@]}"; do
        install -d -o abc -g abc "$dir"
    done
    install -d -o abc -g abc /cwa-book-ingest
    install -d -o abc -g abc /calibre-library
}

# s6 scripts stay owned by root: the oneshots among them run as root at every start
change_script_permissions () {
    chown -R root:root /etc/s6-overlay
    chmod -R go-w /etc/s6-overlay
    chmod +x /etc/s6-overlay/s6-rc.d/cwa-auto-library/run
    chmod +x /etc/s6-overlay/s6-rc.d/cwa-auto-zipper/run
    chmod +x /etc/s6-overlay/s6-rc.d/cwa-ingest-service/run
    chmod +x /etc/s6-overlay/s6-rc.d/cwa-init/run
    chmod +x /etc/s6-overlay/s6-rc.d/cwa-process-recovery/run
    chmod +x /etc/s6-overlay/s6-rc.d/metadata-change-detector/run
    chmod +x /etc/s6-overlay/s6-rc.d/calibre-binaries-setup/run
    chmod +x /etc/s6-overlay/s6-rc.d/svc-calibre-web-automated/run
    chmod +x /app/calibre-web-automated/scripts/check-cwa-services.sh
    chmod +x /app/calibre-web-automated/scripts/compile_translations.sh
}

# Add aliases to .bashrc
add_aliases () {
    cat << 'EOF' >> ~/.bashrc

# Calibre-Web Automated Aliases
alias cwa-check='bash /app/calibre-web-automated/scripts/check-cwa-services.sh'
alias cwa-change-dirs='nano /app/calibre-web-automated/dirs.json'
cover-enforcer () {
    python3 /app/calibre-web-automated/scripts/cover_enforcer.py "$@"
}
EOF
    
    source ~/.bashrc
}

echo "Running docker image setup script..."
make_dirs
change_script_permissions
add_aliases
# Generate .mo files from .po files in translations directory
bash /app/calibre-web-automated/scripts/compile_translations.sh
# Bytecode now, as root: at run time abc can't write __pycache__ under the app any more
python3 -m compileall -q /app/calibre-web-automated/cps /app/calibre-web-automated/scripts \
    /app/calibre-web-automated/cps.py || echo "compileall reported errors (not fatal)"