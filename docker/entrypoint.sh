#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
OVERLAPARR_PORT="${OVERLAPARR_PORT:-8686}"
export OVERLAPARR_PORT

# The "overlaparr" user/group may already exist with other ids (container restarted
# with a changed PUID/PGID): move it to the requested ids instead of failing.
if getent group overlaparr >/dev/null 2>&1; then
    if [ "$(getent group overlaparr | cut -d: -f3)" != "$PGID" ]; then
        groupmod -o -g "$PGID" overlaparr
    fi
elif ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" overlaparr
fi

if getent passwd overlaparr >/dev/null 2>&1; then
    usermod -o -u "$PUID" -g "$PGID" overlaparr
elif ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /config -M -s /usr/sbin/nologin overlaparr
fi

mkdir -p /config
if [ "$(stat -c %u:%g /config)" != "$PUID:$PGID" ]; then
    chown -R "$PUID:$PGID" /config
fi

# Database migrations run inside the app factory at startup; no separate step here.
exec gosu "$PUID:$PGID" /app/backend/.venv/bin/python -m overlaparr.server
