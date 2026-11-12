#!/usr/bin/env bash
# The live deals site as one more Coolify app on the Vorath server: https://deals.vorath.cloud
#
#   sudo bash add_deals.sh [domain] [branch]       defaults: deals.vorath.cloud, main
#
# Needs: the Vorath server scripts (/root/vorath/src, from docs/DEPLOY.md), this repository cloned at
# /root/vorath/deals-src with /root/vorath/deals_deploy_key (see deals/README.md, "On the Vorath server"),
# and a DNS A record for the domain pointing at this server.
# Creates the Coolify app "deals" (Dockerfile in /deals, port 8800, /healthz, volume /data), deploys it, and
# installs /etc/cron.d/vorath-deals: every 5 minutes, a new commit on the branch is deployed (update.sh).
# Backups (backup.sh) and the uptime watch (uptime.sh) pick the app up by themselves: they cover every Coolify app.
set -euo pipefail
. /root/vorath/src/scripts/server/common.sh
DOMAIN="${1:-deals.vorath.cloud}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%/}"
BRANCH="${2:-main}"
APPNAME=deals
KEYFILE=$VH/deals_deploy_key
SRC=$VH/deals-src
REPO=$(git -C "$SRC" remote get-url origin)
[ -f "$KEYFILE" ] || { echo "No $KEYFILE: follow deals/README.md, 'On the Vorath server', step 1."; exit 1; }
[ -n "$(app_uuid "$APPNAME")" ] && { echo "A Coolify app named $APPNAME already exists. To update it: bash $SRC/deals/deploy/update.sh"; exit 1; }

echo "1/6 DNS"
HERE_IP=$(curl -fsS https://api.ipify.org || true)
DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)
if [ -z "$DNS_IP" ]; then
  echo "   $DOMAIN has no address yet. Add an A record: $DOMAIN -> $HERE_IP (Hostinger: hPanel > Domains > vorath.cloud > DNS)."
  echo "   The app is created anyway; HTTPS starts working a few minutes after the record exists."
elif [ "$DNS_IP" != "$HERE_IP" ]; then
  echo "   $DOMAIN points at $DNS_IP, but this server is $HERE_IP. Fix the A record, or HTTPS will not start."
else
  echo "   $DOMAIN -> $HERE_IP"
fi

echo "2/6 Deploy key in Coolify"
KEY=$(api "$C/security/keys" | jget '([k["uuid"] for k in d if k["name"]=="github-app-deals-deploy"] or [""])[0]')
if [ -z "$KEY" ]; then
  KEY=$(python3 -c 'import json,sys; print(json.dumps({"name":"github-app-deals-deploy","description":"read-only deploy key: deals site repository","private_key":open(sys.argv[1]).read()}))' "$KEYFILE" \
    | api -X POST "$C/security/keys" -d @- | jget 'd["uuid"]')
fi

echo "3/6 Creating $APPNAME at https://$DOMAIN from $REPO ($BRANCH)"
PROJECT=$(api "$C/projects" | jget '[p["uuid"] for p in d if p["name"]=="Vorath"][0]')
SERVER=$(api "$C/servers" | jget 'd[0]["uuid"]')
REQ=$(PROJECT="$PROJECT" SERVER="$SERVER" KEY="$KEY" REPO="$REPO" BRANCH="$BRANCH" DOMAIN="$DOMAIN" APPNAME="$APPNAME" python3 - <<'EOF'
import json, os
e = os.environ
print(json.dumps({
    "project_uuid": e["PROJECT"], "environment_name": "production", "server_uuid": e["SERVER"], "private_key_uuid": e["KEY"],
    "git_repository": e["REPO"], "git_branch": e["BRANCH"], "build_pack": "dockerfile",
    "base_directory": "/deals", "dockerfile_location": "/Dockerfile", "ports_exposes": "8800",
    "name": e["APPNAME"], "description": "Live deals: talabat mart, Amazon.eg, Breadfast", "domains": "https://" + e["DOMAIN"],
    "is_auto_deploy_enabled": False, "is_force_https_enabled": True,
    "health_check_enabled": True, "health_check_path": "/healthz", "health_check_port": "8800", "health_check_return_code": 200,
    "health_check_interval": 30, "health_check_timeout": 5, "health_check_retries": 3, "health_check_start_period": 30,
    "limits_memory": "768m", "instant_deploy": False}))
EOF
)
APP=$(api -X POST "$C/applications/private-deploy-key" -d "$REQ" | jget 'd["uuid"]')
env_set "$APP" TZ "Africa/Cairo"

echo "4/6 Persistent volume /data"
api -X POST "$C/applications/$APP/storages" -d "{\"type\":\"persistent\",\"name\":\"$APPNAME-data\",\"mount_path\":\"/data\"}" >/dev/null \
  || echo "   (add the volume by hand: Coolify > $APPNAME > Persistent storage > name $APPNAME-data, path /data)"

echo "5/6 Updates on every push to $BRANCH (cron, every 5 minutes)"
mkdir -p $VH/deals
echo "$BRANCH" > $VH/deals/branch
printf '*/5 * * * * root bash %s/deals/deploy/update.sh >> %s/deals/cron.log 2>&1\n' "$SRC" "$VH" > /etc/cron.d/vorath-deals
chmod 644 /etc/cron.d/vorath-deals

echo "6/6 First deployment (2-4 minutes)"
api -X POST "$C/deploy" -d "{\"uuid\":\"$APP\",\"force\":false}" >/dev/null
git -C "$SRC" rev-parse HEAD > $VH/deals/released

cat <<EOF

Done. $APPNAME is building: https://$DOMAIN/
  Watch it:            Coolify > Vorath > $APPNAME > Deployments, then https://$DOMAIN/healthz
  First prices:        within a minute; a full pass over each store ~15 minutes
  Breadfast:           deals/README.md, "Breadfast on the server"
  Update log:          $VH/deals/log
EOF
