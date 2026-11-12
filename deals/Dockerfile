# Live deals on the Vorath server: one Coolify app (deals.vorath.cloud), built from this folder.
#   /data       its volume: deals.sqlite3 (every price and its history), breadfast_requests.json (your app session)
#   port 8800   the page and its API; GET /healthz
#
#   Settings (Coolify environment, all optional):
#     TALABAT_DELAY / AMAZON_DELAY / BREADFAST_DELAY / JUMIA_DELAY   seconds between requests (1.5 / 8 / 5 / 3)
#     HOT_EVERY         recheck a shelf whose prices moved in the last 30 min every N seconds (60)
#     AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY / AMAZON_PARTNER_TAG   use Amazon's API instead of its public pages
#     CHROME_BIN        another Chrome/Chromium (default: the image's chromium)
#     DEALS_ADMIN_TOKEN turns on /admin: talabat hubs to follow, adding Breadfast (16+ characters; a secret)
#   /data also holds settings.json (hubs), talabat_hubs.json (which hub serves which area) and vapid.json (the
#   key that signs notifications: keep it, or every phone has to turn alerts on again)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Africa/Cairo \
    DEALS_DB=/data/deals.sqlite3 BREADFAST_REQUESTS=/data/breadfast_requests.json HOST=0.0.0.0 PORT=8800

# curl: Coolify's health check runs curl (or wget) inside the container; without it the app is marked unhealthy and
#       the proxy answers "no available server" · chromium: renders Amazon's Today's Deals (built by JavaScript) ·
# tzdata: Cairo time · tini: reaps child processes · util-linux: setpriv (drops root).
# APT=0 skips them (only for a network that blocks Debian's mirror; Coolify's health check then fails).
ARG APT=1
RUN if [ "$APT" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends curl chromium tzdata tini util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/*; fi

WORKDIR /app
# cryptography: encrypts and signs price-drop notifications (Web Push). The rest is Python's standard library.
RUN pip install --no-cache-dir cryptography==50.0.1
COPY deals ./deals
COPY docker-entrypoint.sh .
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app:app /data && chmod +x docker-entrypoint.sh

VOLUME ["/data"]
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'], timeout=4)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "deals", "run"]
