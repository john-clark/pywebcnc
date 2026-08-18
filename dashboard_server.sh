#!/usr/bin/env bash
set -euo pipefail
cd /opt/pywebcnc/web
exec python3 -m http.server 8080 --bind 0.0.0.0
