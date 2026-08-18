#!/bin/bash

set -u

ERRORS=0

log() {
    echo "[INFO] $*"
}

ok() {
    echo "[ OK ] $*"
}

warn() {
    echo "[WARN] $*"
}

err() {
    echo "[ERROR] $*" >&2
    ERRORS=$((ERRORS + 1))
}

echo
echo "============================================================"
echo " PyWebCNC / CNCjs Cleanup"
echo "============================================================"
echo
echo "This will remove ONLY our CNC/PyWebCNC components:"
echo
echo "  - CNCjs"
echo "  - fileserver PM2 process"
echo "  - dashboard PM2 process"
echo "  - terminal PM2 process"
echo "  - PyWebCNC files"
echo "  - JSCut files"
echo "  - custom PyWebCNC nginx configuration"
echo "  - python3-websockets"
echo
echo "It will KEEP:"
echo
echo "  - PM2"
echo "  - Node.js"
echo "  - npm"
echo "  - Python"
echo "  - nginx"
echo
read -r -p "Continue? [y/N]: " ANSWER

if [[ ! "$ANSWER" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    err "Run this as the normal dietpi user, not root."
    exit 1
fi


# ------------------------------------------------------------
# Remove ONLY our PM2 applications
# ------------------------------------------------------------

if command -v pm2 >/dev/null 2>&1; then

    for APP in cncjs fileserver dashboard terminal; do

        if pm2 describe "$APP" >/dev/null 2>&1; then

            log "Removing PM2 process: $APP"

            if pm2 delete "$APP"; then
                ok "Removed PM2 process: $APP"
            else
                err "Failed to remove PM2 process: $APP"
            fi

        else
            log "PM2 process not found: $APP"
        fi

    done

    # Keep PM2 itself.
    pm2 save >/dev/null 2>&1 || true

else
    warn "PM2 is not installed."
fi


# ------------------------------------------------------------
# Remove CNCjs package
# ------------------------------------------------------------

if command -v npm >/dev/null 2>&1; then

    if npm list -g cncjs >/dev/null 2>&1; then

        log "Removing CNCjs..."

        if npm uninstall -g cncjs; then
            ok "CNCjs removed."
        else
            err "npm failed to remove CNCjs."
        fi

    else
        log "CNCjs is not installed through npm."
    fi

fi


# Remove leftover CNCjs directories only
if [ -d /usr/local/lib/node_modules/cncjs ]; then
    sudo rm -rf /usr/local/lib/node_modules/cncjs
    ok "Removed leftover CNCjs directory."
fi

sudo rm -rf /usr/local/lib/node_modules/.cncjs-* 2>/dev/null || true


# ------------------------------------------------------------
# Remove our PyWebCNC directories
# ------------------------------------------------------------

DIRS=(
    "$HOME/dashboard"
    "$HOME/terminal"
    "$HOME/pywebcnc"
    "$HOME/pywebcnc-final"
    "$HOME/pywebcnc-jscut"
    "/opt/pywebcnc"
    "/var/www/jscut"
    "/tmp/jscut"
)

for DIR in "${DIRS[@]}"; do

    if [ -e "$DIR" ]; then

        log "Removing: $DIR"

        if sudo rm -rf "$DIR"; then
            ok "Removed $DIR"
        else
            err "Could not remove $DIR"
        fi

    fi

done


# ------------------------------------------------------------
# Remove our Nginx configuration
# ------------------------------------------------------------

NGINX_FILES=(
    /etc/nginx/sites-enabled/cnc-dashboard
    /etc/nginx/sites-enabled/pywebcnc
    /etc/nginx/sites-enabled/fileserver
    /etc/nginx/sites-available/cnc-dashboard
    /etc/nginx/sites-available/pywebcnc
    /etc/nginx/sites-available/fileserver
)

for FILE in "${NGINX_FILES[@]}"; do

    if [ -e "$FILE" ] || [ -L "$FILE" ]; then

        log "Removing Nginx configuration: $FILE"

        if sudo rm -f "$FILE"; then
            ok "Removed $FILE"
        else
            err "Could not remove $FILE"
        fi

    fi

done


# ------------------------------------------------------------
# Restore Nginx default site
# ------------------------------------------------------------

if [ -f /etc/nginx/sites-available/default ]; then

    log "Restoring Nginx default site..."

    if sudo ln -sf \
        /etc/nginx/sites-available/default \
        /etc/nginx/sites-enabled/default
    then
        ok "Nginx default site enabled."
    else
        err "Could not enable Nginx default site."
    fi

fi


# ------------------------------------------------------------
# Test and reload Nginx
# ------------------------------------------------------------

if sudo nginx -t; then

    ok "Nginx configuration is valid."

    if sudo systemctl reload nginx; then
        ok "Nginx reloaded."
    else
        err "Could not reload Nginx."
    fi

else

    err "Nginx configuration test failed."
fi


# ------------------------------------------------------------
# Remove Python WebSocket package
# ------------------------------------------------------------

if dpkg-query -W -f='${Status}' python3-websockets 2>/dev/null |
    grep -q "install ok installed"
then

    log "Removing python3-websockets..."

    if sudo apt remove -y python3-websockets; then
        ok "python3-websockets removed."
    else
        err "Could not remove python3-websockets."
    fi

fi


# ------------------------------------------------------------
# Clean unused dependencies
# ------------------------------------------------------------

sudo apt autoremove -y >/dev/null 2>&1 || \
    warn "apt autoremove reported an issue."


# ------------------------------------------------------------
# Verify PM2
# ------------------------------------------------------------

echo
echo "============================================================"
echo " PM2 verification"
echo "============================================================"

if command -v pm2 >/dev/null 2>&1; then

    ok "PM2 is still installed."

    pm2 list

else

    err "PM2 is no longer available."
fi


# ------------------------------------------------------------
# Verify Node
# ------------------------------------------------------------

echo
echo "============================================================"
echo " Node/npm verification"
echo "============================================================"

if command -v node >/dev/null 2>&1; then
    ok "Node.js preserved: $(node --version)"
else
    warn "Node.js is not currently in PATH."
fi

if command -v npm >/dev/null 2>&1; then
    ok "npm preserved: $(npm --version)"
else
    warn "npm is not currently in PATH."
fi


# ------------------------------------------------------------
# Check for remaining services
# ------------------------------------------------------------

echo
echo "============================================================"
echo " Listening ports"
echo "============================================================"

ss -ltnp 2>/dev/null |
    grep -E ':80 |:8000 |:8088 |:8080 |:8090 ' ||
    echo "No CNC/PyWebCNC ports currently listening."


# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

echo
echo "============================================================"

if [ "$ERRORS" -eq 0 ]; then

    echo " Cleanup completed successfully"
    echo "============================================================"
    echo
    echo "Removed:"
    echo "  CNCjs"
    echo "  fileserver"
    echo "  dashboard"
    echo "  terminal"
    echo "  PyWebCNC"
    echo "  JSCut"
    echo "  custom Nginx configuration"
    echo
    echo "Preserved:"
    echo "  PM2"
    echo "  Node.js"
    echo "  npm"
    echo "  Python"
    echo "  Nginx"
    echo

    exit 0

else

    echo " Cleanup completed with $ERRORS error(s)"
    echo "============================================================"
    exit 1

fi