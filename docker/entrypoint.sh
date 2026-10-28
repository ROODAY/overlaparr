#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
OVERLAPARR_PORT="${OVERLAPARR_PORT:-8686}"
export OVERLAPARR_PORT

if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" overlaparr
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /config -M -s /usr/sbin/nologin overlaparr
fi

mkdir -p /config
chown -R "$PUID:$PGID" /config

# Database migrations run inside the app factory at startup; no separate step here.
exec gosu "$PUID:$PGID" /app/backend/.venv/bin/python -m overlaparr.server
