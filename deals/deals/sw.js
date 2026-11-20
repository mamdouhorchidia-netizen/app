// Live deals: makes the site installable and shows price-drop notifications, even when it's closed.
const VIBRATE = [90, 60, 90, 60, 260];  // the deals "da-da-daaa" buzz (Android); the sound is the phone's own

// The page shell is kept as a last good copy, so the site still opens with no connection and says so, instead of the
// browser's error page: the page and every stylesheet and module it loads (its /static/<build>/ addresses: the
// build is a hash of all of them, so a kept page and its files always belong together). Network first, always: the
// server sends these with an ETag and no-cache, so a deploy shows at once; the copy is used only when the network
// fails. A new page replaces the kept one only once all its files are kept too, and the old build's files are then
// dropped. Prices (/api/*) are never cached.
const SHELL = "deals-shell-v4";
const isShell = path => path === "/" || path === "/live";  // "/?deals=1…" is the same page
const isAsset = path => path.startsWith("/static/");
const assetsOf = html => [...new Set([...html.matchAll(/(?:href|src)="(\/static\/[^"?#]+)"/g)].map(m => m[1]))];
const cacheKey = url => isShell(url.pathname) ? "/" : url.pathname;

async function keep(res) {  // a fresh page: keep it with all its files, or keep nothing new
  const want = assetsOf(await res.clone().text()), c = await caches.open(SHELL);
  if (!want.length) return;
  const have = new Set((await c.keys()).map(r => new URL(r.url).pathname));
  await c.addAll(want.filter(u => !have.has(u)));  // throws when one fails: the page kept before stays, with its files
  await c.put("/", res);
  for (const r of await c.keys()) { const path = new URL(r.url).pathname; if (isAsset(path) && !want.includes(path)) await c.delete(r); }
}

self.addEventListener("install", e => {
  e.waitUntil(fetch("/").then(res => res.ok ? keep(res) : null).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== SHELL && k.startsWith("deals-shell")) await caches.delete(k);
  await self.clients.claim();
})()));
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (!(isShell(url.pathname) || isAsset(url.pathname))) return;  // everything else: the network, untouched
  e.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok && res.type === "basic") {
        if (isShell(url.pathname)) e.waitUntil(keep(res.clone()).catch(() => {}));
        return res;
      }
      if (res.status < 500) return res;
      return (await caches.match(cacheKey(url), { cacheName: SHELL })) || res;  // the server is down behind a proxy
    } catch (err) {
      const hit = await caches.match(cacheKey(url), { cacheName: SHELL });
      if (hit) return hit;
      throw err;
    }
  })());
});

self.addEventListener("push", e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch { m = { title: "Price drop", body: e.data && e.data.text() }; }
  e.waitUntil((async () => {
    await self.registration.showNotification(m.title || "Price drop", {
      body: m.body || "", icon: "/icon-192.png", badge: "/icon-192.png", image: m.image || undefined,
      tag: m.tag || "deals", renotify: true, vibrate: VIBRATE, data: { url: m.url || "/" },
      actions: [{ action: "open", title: "Open deal" }],
    });
    // an open page plays its own chime
    for (const c of await self.clients.matchAll({ type: "window" })) c.postMessage({ type: "deal-push", m });
  })());
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL(e.notification.data && e.notification.data.url || "/", self.location.origin).href;
  e.waitUntil((async () => {
    for (const c of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) {
      if (new URL(c.url).origin === self.location.origin) { await c.focus(); c.navigate(url); return; }
    }
    await self.clients.openWindow(url);
  })());
});
