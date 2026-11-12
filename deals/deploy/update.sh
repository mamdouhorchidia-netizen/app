#!/usr/bin/env bash
# Every 5 minutes (cron, installed by add_deals.sh): when the deals branch moved on GitHub, deploy it.
# Log: /root/vorath/deals/log. The checkout at /root/vorath/deals-src is pulled first, so this script updates itself.
set -uo pipefail
. /root/vorath/src/scripts/server/common.sh
S=$VH/deals
SRC=$VH/deals-src
export GIT_SSH_COMMAND="ssh -i $VH/deals_deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
exec 9>$S/lock
flock -n 9 || exit 0
log() { echo "$(date -Is) $*" >> $S/log; }

BRANCH=$(cat $S/branch)  # written by add_deals.sh
NEW=$(git ls-remote "$(git -C $SRC remote get-url origin)" "refs/heads/$BRANCH" 2>/dev/null | cut -f1)
[ -n "$NEW" ] || exit 0
[ "$NEW" = "$(cat $S/released 2>/dev/null)" ] && exit 0

git -C $SRC fetch -q --depth 1 origin "$BRANCH" && git -C $SRC checkout -q -B "$BRANCH" FETCH_HEAD \
  || { log "could not fetch $BRANCH"; exit 1; }
APP=$(app_uuid deals)
[ -n "$APP" ] || { log "no Coolify app named deals"; exit 1; }
api -X POST "$C/deploy" -d "{\"uuid\":\"$APP\",\"force\":false}" >/dev/null || { log "Coolify refused the deployment of ${NEW:0:7}"; exit 1; }
echo "$NEW" > $S/released
log "deploying ${NEW:0:7}"
