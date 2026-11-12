#!/bin/sh
# Hand /data to the app user, drop root, and run under tini when the image has it.
set -e
INIT=""
if [ -x /usr/bin/tini ] && [ "$$" = "1" ]; then INIT="/usr/bin/tini --"; fi
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  [ "$(stat -c %u /data)" = "1000" ] || chown -R 1000:1000 /data
  export HOME=/home/app
  exec $INIT setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec $INIT "$@"
