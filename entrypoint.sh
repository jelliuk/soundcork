#!/bin/sh
# entrypoint.sh
#
# Remaps the container's appuser UID/GID at runtime to match PUID/PGID
# environment variables supplied via docker run or docker-compose.
# This avoids volume mount permission errors when the host user differs
# from the build-time default of 1000:1000.
#
# Usage in docker-compose.yml:
#   environment:
#     - PUID=1001
#     - PGID=1001
#
# If PUID/PGID are not set, the build-time values are used unchanged
# and this script starts the application directly with no remapping.

PUID=${PUID:-1000}
PGID=${PGID:-1000}

CURRENT_UID=$(id -u appuser)
CURRENT_GID=$(getent group appgroup | cut -d: -f3)

if [ "${PGID}" != "${CURRENT_GID}" ]; then
    groupmod --gid "${PGID}" appgroup
fi

if [ "${PUID}" != "${CURRENT_UID}" ]; then
    usermod --uid "${PUID}" appuser
fi

# Ensure data and log directories are owned by the (potentially remapped) user
chown -R appuser:appgroup /soundcork/data /soundcork/logs

# Drop privileges and execute the CMD as appuser
exec gosu appuser "$@"
