#!/usr/bin/env bash
# Import a Breadfast app capture into the running deals app (the site starts refreshing Breadfast within a minute).
#
#   sudo bash import_breadfast.sh /root/capture.har [--price-divisor 100]
#
# Copy the .har to the server first, e.g. from your computer: scp capture.har root@<server>:/root/
# The capture holds your Breadfast session: it is deleted from the server once imported.
set -euo pipefail
. /root/vorath/src/scripts/server/common.sh
HAR="${1:?the .har file, e.g. /root/capture.har}"; shift
[ -f "$HAR" ] || { echo "No file $HAR"; exit 1; }
CID=$(app_cid "$(app_uuid deals)")
[ -n "$CID" ] || { echo "The deals app is not running."; exit 1; }
docker cp "$HAR" "$CID:/data/capture.har"
docker exec "$CID" chown 1000:1000 /data/capture.har
docker exec --user 1000:1000 "$CID" python -m deals import-har /data/capture.har "$@" \
  || { docker exec "$CID" rm -f /data/capture.har; exit 1; }
docker exec "$CID" rm -f /data/capture.har
rm -f "$HAR"
echo "Imported. The capture was deleted; the session is kept in the app's volume (/data/breadfast_requests.json)."
