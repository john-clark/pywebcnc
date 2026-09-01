#!/usr/bin/env bash

# pywebcnc installer
# Run from the cloned repository as the account that should own/run PM2
# (normally the dietpi user). Do NOT run as root.

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/pywebcnc"
VENV_DIR="$INSTALL_DIR/venv"
NGINX_SITE="/etc/nginx/sites-available/pywebcnc"
NGINX_LINK="/etc/nginx/sites-enabled/pywebcnc"
JSCUT_DIR="$INSTALL_DIR/jscut"
JSCUT_VERSION="gh-pages"
KIRIMOTO_DIR="$INSTALL_DIR/kirimoto"
KIRIMOTO_VERSION="master"
CNCJS_VERSION="1.10.3"
CNCJS_PORT="8000"
FILESERVER_PORT="8088"
FILESERVER_DIR="$HOME/CNC"
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

fail() {
  err "$*"
  echo
  echo "Installation aborted. Fix the problem above and run ./install.sh again."
  exit 1
}

run() {
  "$@"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    err "Command failed: $*"
    return "$rc"
  fi
  return 0
}

require_file() {
  local file="$1"
  if [[ ! -f "$SCRIPT_DIR/$file" ]]; then
    err "Missing repository file: $file"
    return 1
  fi
  if [[ ! -s "$SCRIPT_DIR/$file" ]]; then
    err "Repository file is empty: $file"
    return 1
  fi
  ok "Found: $file"
  return 0
}

require_dir() {
  local dir="$1"
  if [[ ! -d "$SCRIPT_DIR/$dir" ]]; then
    err "Missing repository directory: $dir"
    return 1
  fi
  ok "Found: $dir/"
  return 0
}

check_command() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "Command available: $cmd"
    return 0
  fi
  warn "Command not currently available: $cmd"
  return 1
}

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] || [ "$ERRORS" -ne 0 ]; then
    echo
    echo "============================================================"
    echo " Installation did not complete successfully"
    echo "============================================================"
    echo "Errors recorded: $ERRORS"
  fi
}
trap on_exit EXIT

echo
echo "============================================================"
echo " pywebcnc installer"
echo "============================================================"
echo

# ------------------------------------------------------------
# Basic account checks
# ------------------------------------------------------------

if [[ "$(id -u)" -eq 0 ]]; then
  fail "Do not run install.sh as root. Run it as the normal PM2 user (for example: dietpi)."
fi

CURRENT_USER="$(id -un)"
log "Running as user: $CURRENT_USER"
log "Repository: $SCRIPT_DIR"

if ! command -v sudo >/dev/null 2>&1; then
  fail "sudo is required but was not found."
fi

if ! sudo -n true >/dev/null 2>&1; then
  warn "sudo may request your password during installation."
else
  ok "sudo access verified."
fi

# -------------------------------------------------------------
# Detect CPU architecture
# -------------------------------------------------------------

MACHINE="$(uname -m)"

case "$MACHINE" in
  armv6l|armv6*)
    CPU_TYPE="armv6"
    NODE_PLATFORM_ARCH="armv6l"
    warn "ARMv6 detected. Standard Debian/DietPi nodejs packages require ARMv7+ instructions and will fail."
    ;;
  armv7l|armv7*)
    CPU_TYPE="armv7"
    NODE_PLATFORM_ARCH="armv7l"
    INSTALL_NODE_VIA_APT=true
    ;;
  aarch64|arm64)
    CPU_TYPE="arm64"
    NODE_PLATFORM_ARCH="arm64"
    INSTALL_NODE_VIA_APT=true
    ;;
  x86_64|amd64)
    CPU_TYPE="amd64"
    NODE_PLATFORM_ARCH="x64"
    INSTALL_NODE_VIA_APT=true
    ;;
  i386|i686)
    CPU_TYPE="i386"
    NODE_PLATFORM_ARCH="x86"
    INSTALL_NODE_VIA_APT=true
    ;;
  *)
    fail "Unsupported CPU architecture: $MACHINE"
    ;;
esac

log "Detected machine: $MACHINE"
log "Selected CPU type: $CPU_TYPE"

# ------------------------------------------------------------
# Repository preflight -- BEFORE making system changes
# ------------------------------------------------------------

echo
echo "============================================================"
echo " Preflight: repository contents"
echo "============================================================"

REQUIRED_FILES=(
  "install.sh"
  "requirements.txt"
  "terminal_server.py"
  "dashboard_server.sh"
  "file_server.py"
  "nginx/pywebcnc"
  "web/index.html"
  "web/terminal.html"
)

REQUIRED_DIRS=(
  "web"
  "nginx"
)

for dir in "${REQUIRED_DIRS[@]}"; do
  require_dir "$dir" || true
done

for file in "${REQUIRED_FILES[@]}"; do
  require_file "$file" || true
done

if [[ "$ERRORS" -ne 0 ]]; then
  fail "Repository preflight failed. No system changes were made."
fi

if [[ ! -x "$SCRIPT_DIR/install.sh" ]]; then
  warn "install.sh is not executable. That is okay when started with 'bash install.sh'."
fi

# ------------------------------------------------------------
# Validate key file contents before changing the system
# ------------------------------------------------------------

log "Checking installer configuration files..."

grep -q "terminal-ws" "$SCRIPT_DIR/nginx/pywebcnc" || fail "nginx/pywebcnc does not contain the terminal WebSocket route."
grep -q "listen 80" "$SCRIPT_DIR/nginx/pywebcnc" || fail "nginx/pywebcnc does not listen on port 80."
grep -q "websocket" "$SCRIPT_DIR/web/terminal.html" || warn "web/terminal.html does not contain the word 'websocket'; verify the terminal page."
grep -q "python" "$SCRIPT_DIR/requirements.txt" || true

ok "Repository configuration checks passed."

# ------------------------------------------------------------
# System preflight
# ------------------------------------------------------------

echo
echo "============================================================"
echo " Preflight: system"
echo "============================================================"

ARCH="$(uname -m)"
DIST="unknown"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DIST="${PRETTY_NAME:-unknown}"
fi

log "Architecture: $ARCH"
log "Operating system: $DIST"

check_command sudo || true
check_command apt-get || fail "apt-get is required."
check_command systemctl || fail "systemctl is required."

FREE_KB="$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {print $4}')"
if [[ "${FREE_KB:-0}" =~ ^[0-9]+$ ]] && (( FREE_KB < 200000 )); then
  warn "Less than 200 MB free on the filesystem containing the repository."
else
  ok "Sufficient free space detected."
fi

if [[ -f /etc/debian_version ]]; then
  ok "Debian-family system detected."
else
  warn "This installer was designed for Debian/DietPi systems."
fi

# ------------------------------------------------------------
# Install apt dependencies
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Installing system dependencies"
echo "============================================================"

log "Updating apt package lists..."
run sudo apt-get update || fail "apt-get update failed."
ok "apt package lists updated."

APT_PACKAGES=(
  nginx
  python3
  python3-venv
  python3-pip
  curl
  git
  unzip
  ca-certificates
)

log "Installing: ${APT_PACKAGES[*]}"
run sudo apt-get install -y "${APT_PACKAGES[@]}" || fail "Required apt packages could not be installed."
ok "System dependencies installed."

# ------------------------------------------------------------
# Verify required commands after apt install
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Verifying installed commands"
echo "============================================================"

for cmd in python3 curl unzip git nginx systemctl; do
  check_command "$cmd" || fail "Required command '$cmd' is unavailable after package installation."
done

python3 --version
curl --version | head -n 1
nginx -v 2>&1

# ------------------------------------------------------------
# Python virtual environment / requirements
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Installing Python requirements"
echo "============================================================"

log "Creating virtual environment: $VENV_DIR"
sudo rm -rf "$VENV_DIR"
sudo mkdir -p "$INSTALL_DIR"
run sudo python3 -m venv "$VENV_DIR" || fail "Could not create Python virtual environment."
run sudo "$VENV_DIR/bin/python" -m pip install --upgrade pip || fail "Could not upgrade pip in the virtual environment."
run sudo "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt" || fail "Python requirements could not be installed."
ok "Python requirements installed."

# ------------------------------------------------------------
# Install Node.js (Standalone Binary - No Apt Bloat)
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Installing Node.js and npm"
echo "============================================================"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  log "Node.js not found. Installing standalone binary for architecture: $NODE_PLATFORM_ARCH..."

  if [[ "$CPU_TYPE" == "armv6" ]]; then
    NODE_VERSION="v16.20.2"
  else
    NODE_VERSION="v18.19.0"
  fi

  NODE_TARBALL="node-$NODE_VERSION-linux-$NODE_PLATFORM_ARCH.tar.gz"
  NODE_URL="https://nodejs.org/dist/$NODE_VERSION/$NODE_TARBALL"

  TMP_NODE="$(mktemp -d)"
  log "Downloading $NODE_URL..."
  run curl -fL --retry 3 --connect-timeout 15 "$NODE_URL" -o "$TMP_NODE/$NODE_TARBALL" || fail "Failed to download Node.js binary."

  log "Extracting Node.js to /usr/local..."
  run sudo tar -xzf "$TMP_NODE/$NODE_TARBALL" -C /usr/local --strip-components=1 || fail "Failed to extract Node.js binaries."
  rm -rf "$TMP_NODE"

  ok "Node.js $NODE_VERSION installed successfully."
else
  ok "Existing Node.js detected: $(node --version)"
fi

NODE_VERSION_CHECK="$(node --version)"
NPM_VERSION_CHECK="$(npm --version)"
log "Node.js: $NODE_VERSION_CHECK"
log "npm: $NPM_VERSION_CHECK"

# ------------------------------------------------------------
# Install CNCjs
# ------------------------------------------------------------
echo "============================================================"
echo " Installing CNCjs"
echo "============================================================"

if npm list -g --depth=0 cncjs 2>/dev/null | grep -q "cncjs@$CNCJS_VERSION"; then
  ok "CNCjs $CNCJS_VERSION already installed."
else
  log "Installing CNCjs $CNCJS_VERSION..."
  run sudo npm install -g "cncjs@$CNCJS_VERSION" --unsafe-perm --legacy-peer-deps || fail "CNCjs installation failed."
  ok "CNCjs $CNCJS_VERSION installed."
fi

if ! command -v cncjs >/dev/null 2>&1; then
  fail "CNCjs command was not found after installation."
fi

ok "CNCjs command: $(command -v cncjs)"

# ------------------------------------------------------------
# Install pywebcnc application files
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Installing pywebcnc files"
echo "============================================================"

sudo rm -rf "$INSTALL_DIR/web"
sudo mkdir -p "$INSTALL_DIR/web"

run sudo cp -a "$SCRIPT_DIR/web/." "$INSTALL_DIR/web/" || fail "Could not copy web files."
run sudo cp "$SCRIPT_DIR/terminal_server.py" "$INSTALL_DIR/terminal_server.py" || fail "Could not copy terminal_server.py."
run sudo cp "$SCRIPT_DIR/dashboard_server.sh" "$INSTALL_DIR/dashboard_server.sh" || fail "Could not copy dashboard_server.sh."
run sudo cp "$SCRIPT_DIR/file_server.py" "$INSTALL_DIR/file_server.py" || fail "Could not copy file_server.py."
run sudo chmod 0755 "$INSTALL_DIR/dashboard_server.sh" "$INSTALL_DIR/file_server.py"|| fail "Could not make service scripts executable."
run sudo chown -R "$(id -u):$(id -g)" "$INSTALL_DIR/web" "$VENV_DIR" || fail "Could not set ownership on application files."
run sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR/terminal_server.py" "$INSTALL_DIR/dashboard_server.sh" "$INSTALL_DIR/file_server.py" || fail "Could not set application file ownership."
run sudo chown -R "$(id -u):$(id -g)" "$INSTALL_DIR" || fail "Could not set ownership on installation directory."
ok "Application files installed in $INSTALL_DIR"

# ------------------------------------------------------------
# Python syntax preflight after copy
# ------------------------------------------------------------
log "Checking terminal_server.py syntax..."
run "$VENV_DIR/bin/python" -m py_compile "$INSTALL_DIR/terminal_server.py" || fail "terminal_server.py failed Python syntax validation."
ok "Python syntax check passed."

# ------------------------------------------------------------
# Prepare file server directory
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Preparing CNC file server"
echo "============================================================"

run mkdir -p "$FILESERVER_DIR" || fail "Could not create file server directory: $FILESERVER_DIR"
run chmod 0755 "$FILESERVER_DIR" || fail "Could not set file server directory permissions."
run chown "$(id -u):$(id -g)" "$FILESERVER_DIR" || fail "Could not set file server directory ownership."
ok "File server directory: $FILESERVER_DIR"

# ------------------------------------------------------------
# Install JSCut
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Installing JSCut"
echo "============================================================"

TMP_JSCUT="$(mktemp -d)"
cleanup_jscut() {
  rm -rf "$TMP_JSCUT"
}

JSCUT_ARCHIVE_URL="https://github.com/tbfleming/jscut/archive/refs/heads/${JSCUT_VERSION}.zip"
log "Downloading JSCut: $JSCUT_ARCHIVE_URL"
if run curl -fL --retry 3 --connect-timeout 15 "$JSCUT_ARCHIVE_URL" -o "$TMP_JSCUT/jscut.zip"; then
  if run unzip -q "$TMP_JSCUT/jscut.zip" -d "$TMP_JSCUT"; then
    JSCUT_SRC="$TMP_JSCUT/jscut-${JSCUT_VERSION}"
    if [[ -d "$JSCUT_SRC" && -s "$JSCUT_SRC/jscut.html" ]]; then
      sudo rm -rf "$JSCUT_DIR"
      sudo mkdir -p "$JSCUT_DIR"
      run sudo cp -a "$JSCUT_SRC/." "$JSCUT_DIR/"
      run sudo chown -R "$(id -u):$(id -g)" "$JSCUT_DIR"
      ok "JSCut installed in $JSCUT_DIR"
    else
      warn "Extracted JSCut source directory missing or invalid."
    fi
  else
    warn "JSCut archive extraction failed."
  fi
else
  warn "JSCut download failed."
fi
cleanup_jscut

# ------------------------------------------------------------
# Installing Kiri:Moto
# ------------------------------------------------------------
echo "============================================================"
echo " Installing Kiri:Moto via @gridspace/app-server"
echo "============================================================"

KIRIMOTO_URL="https://github.com/GridSpace/grid-apps/archive/refs/heads/master.zip"
TMP_KIRI="/tmp/grid-apps"
GRID_APPS_DIR="/opt/pywebcnc/web/grid-apps"

log "Downloading and installing grid-apps for Kiri:Moto..."
rm -rf "$TMP_KIRI"
mkdir -p "$TMP_KIRI"
curl -L "$KIRIMOTO_URL" -o "$TMP_KIRI/master.zip"
unzip -q "$TMP_KIRI/master.zip" -d "$TMP_KIRI"

SRC_DIR="$TMP_KIRI/grid-apps-master"

if [[ -d "$SRC_DIR" ]]; then
  sudo rm -rf "$GRID_APPS_DIR"
  sudo mkdir -p "$GRID_APPS_DIR"
  sudo cp -a "$SRC_DIR/." "$GRID_APPS_DIR/"
  
  log "Installing grid-apps npm dependencies and WASM binaries..."
  cd "$GRID_APPS_DIR"
  npm install --production
  
  sudo chown -R "$(id -u):$(id -g)" "$GRID_APPS_DIR"
  
  log "Registering Kiri:Moto server with PM2 on port 8091..."
  pm2 delete pywebcnc-kirimoto 2>/dev/null || true
  pm2 start gs-app-server --name "pywebcnc-kirimoto" --cwd "$GRID_APPS_DIR" -- --port 8091
  pm2 save
  
  ok "Kiri:Moto installed and running via gs-app-server on port 8091"
else
  warn "Extracted grid-apps source directory missing or invalid."
fi

# ------------------------------------------------------------
# Nginx configuration
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Configuring nginx"
echo "============================================================"

if [[ ! -s "$SCRIPT_DIR/nginx/pywebcnc" ]]; then
  fail "nginx/pywebcnc became unavailable during installation."
fi

run sudo cp "$SCRIPT_DIR/nginx/pywebcnc" "$NGINX_SITE" || fail "Could not install nginx configuration."
run sudo rm -f /etc/nginx/sites-enabled/default || fail "Could not disable nginx default site."
run sudo ln -sfn "$NGINX_SITE" "$NGINX_LINK" || fail "Could not enable pywebcnc nginx site."

log "Testing nginx configuration..."
run sudo nginx -t || fail "nginx configuration test failed."
ok "nginx configuration is valid."

run sudo systemctl enable nginx || fail "Could not enable nginx at boot."
run sudo systemctl restart nginx || fail "Could not restart nginx."
ok "nginx is running."

# ------------------------------------------------------------
# PM2
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Configuring PM2"
echo "============================================================"

if ! command -v pm2 >/dev/null 2>&1; then
  if ! command -v npm >/dev/null 2>&1; then
    fail "PM2 is not installed and npm is unavailable."
  fi

  log "PM2 is not installed. Installing PM2 globally..."
  run sudo npm install -g pm2 || fail "PM2 installation failed."
  ok "PM2 installed."
else
  ok "Using existing PM2: $(command -v pm2)"
fi

if ! pm2 ping >/dev/null 2>&1; then
  fail "PM2 is installed but the PM2 daemon could not be started. Resolve PM2 before continuing."
fi
ok "PM2 daemon is responding."

# ------------------------------------------------------------
# Configuring PM2 Ecosystem
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Configuring pywebcnc PM2 services"
echo "============================================================"

for APP in cncjs pywebcnc-fileserver pywebcnc-dashboard pywebcnc-terminal; do
  pm2 delete "$APP" >/dev/null 2>&1 || true
done

ECOSYSTEM_FILE="$INSTALL_DIR/ecosystem.config.js"

log "Creating PM2 ecosystem configuration at $ECOSYSTEM_FILE..."
sudo tee "$ECOSYSTEM_FILE" > /dev/null <<EOF
module.exports = {
  apps: [
  {
    name: "cncjs",
    script: "$(command -v cncjs)",
    args: ["--port", "$CNCJS_PORT"],
    cwd: "$HOME"
  },
  {
    name: "pywebcnc-fileserver",
    script: "file_server.py",
    interpreter: "$VENV_DIR/bin/python",
    cwd: "$INSTALL_DIR",
    env: {
      PYTHONUNBUFFERED: "1"
    }
  },
  {
    name: "pywebcnc-dashboard",
    script: "dashboard_server.sh",
    interpreter: "bash",
    cwd: "$INSTALL_DIR",
    env: {
      PYTHONUNBUFFERED: "1"
    }
  },
  {
    name: "pywebcnc-terminal",
    script: "terminal_server.py",
    interpreter: "$VENV_DIR/bin/python",
    cwd: "$INSTALL_DIR",
    env: {
      PYTHONUNBUFFERED: "1"
    }
  }
  ]
};
EOF

run sudo chown "$(id -u):$(id -g)" "$ECOSYSTEM_FILE" || fail "Could not set ownership on ecosystem file."

log "Starting PM2 apps via ecosystem file..."
run pm2 start "$ECOSYSTEM_FILE" || fail "Could not start PM2 services from ecosystem file."

ok "All PM2 services started."

run pm2 save || fail "Could not save PM2 process list."

# ------------------------------------------------------------
# Final verification
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Final verification"
echo "============================================================"

log "Waiting for services to start up..."
sleep 10
log "Checking PM2 services..."
pm2 status

python3 -c '
import subprocess, json, sys

try:
    res = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, check=True)
    apps = json.loads(res.stdout)
except Exception as e:
    print(f"[ERROR] Failed to query PM2 process list: {e}")
    sys.exit(1)

target_apps = ["cncjs", "pywebcnc-fileserver", "pywebcnc-dashboard", "pywebcnc-terminal"]
found_apps = {app.get("name"): app.get("pm2_env", {}).get("status") for app in apps}

verification_errors = 0
for target in target_apps:
    status = found_apps.get(target)
    if status == "online":
        print(f"[ OK ] PM2 app {target} is online.")
    else:
        print(f"[ERROR] PM2 app {target} is not online (status: {status or "missing"})")
        verification_errors += 1

sys.exit(verification_errors)
'
if [ $? -ne 0 ]; then
    ERRORS=$((ERRORS + 1))
fi

log "Checking CNCjs HTTP service..."
if curl -fsSI "http://127.0.0.1:${CNCJS_PORT}/" >/dev/null; then
  ok "CNCjs is responding on 127.0.0.1:${CNCJS_PORT}"
else
  err "CNCjs did not respond on 127.0.0.1:${CNCJS_PORT}"
fi

log "Checking file server HTTP service..."
if curl -fsS "http://127.0.0.1:${FILESERVER_PORT}/" >/dev/null; then
  ok "File server is responding on 127.0.0.1:${FILESERVER_PORT}"
else
  err "File server did not respond on 127.0.0.1:${FILESERVER_PORT}"
fi

log "Checking dashboard HTTP service..."
if curl -fsSI http://127.0.0.1:8080/ >/dev/null; then
  ok "Dashboard is responding on 127.0.0.1:8080"
else
  err "Dashboard did not respond on 127.0.0.1:8080"
fi

log "Checking nginx port 80..."
if curl -fsSI http://127.0.0.1/ >/dev/null; then
  ok "Nginx is responding on port 80"
else
  err "Nginx did not respond on port 80"
fi

log "Checking terminal WebSocket listener..."
if ss -ltn 2>/dev/null | grep -qE ':8090[[:space:]]'; then
  ok "Terminal listener is present on port 8090"
else
  err "Terminal listener is not listening on port 8090"
fi

log "Checking local JSCut..."
if [[ -s "$JSCUT_DIR/jscut.html" ]]; then
  ok "JSCut page is installed."
else
  warn "JSCut page is missing."
fi

log "Checking local Kiri:Moto..."
if [[ -s "$KIRIMOTO_DIR/index.html" ]]; then
  ok "Kiri:Moto application is installed."
else
  warn "Kiri:Moto application page is missing."
fi

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------
echo
echo "============================================================"

if [ "$ERRORS" -eq 0 ]; then
  echo " Installation completed successfully"
  echo "============================================================"
  echo
  echo "Dashboard: http://<PI-IP>/"
  echo "Direct: http://<PI-IP>:8080/"
  echo "JSCut CAM: http://<PI-IP>/jscut/"
  echo "Kiri:Moto CAM: http://<PI-IP>/kirimoto/"
  echo "CNCjs: http://<PI-IP>:8000/"
  echo "Fileserver: http://<PI-IP>:8088/"
  echo
  echo "PM2 services:"
  echo " pywebcnc-dashboard"
  echo " pywebcnc-terminal"
  echo
  exit 0
else
  echo " Installation completed with $ERRORS error(s)"
  echo "============================================================"
  echo
  echo "Review the [ERROR] messages above."
  exit 1
fi
