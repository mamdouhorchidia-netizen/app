// Live deals: makes the site installable and shows price-drop notifications, even when it's closed.
const VIBRATE = [90, 60, 90, 60, 260];  // the deals "da-da-daaa" buzz (Android); the sound is the phone's own

// The page shell (its translations and its design tokens) is kept as a last good copy, so the site still opens with no connection and
// says so, instead of the browser's error page. Network first, always: the server sends these with an ETag and
// no-cache, so a deploy shows at once; the copy is used only when the network fails. Prices (/api/*) are never cached.
const SHELL = "deals-shell-v3";
const SHELL_URLS = ["/", "/i18n.js", "/tokens.css"];
const shellKey = url => url.pathname === "/live" ? "/" : url.pathname;  // "/?deals=1…" is the same page

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_URLS)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== SHELL && k.startsWith("deals-shell")) await caches.delete(k);
  await self.clients.claim();
})()));
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (!(url.pathname === "/" || url.pathname === "/live" || SHELL_URLS.includes(url.pathname))) return;  // everything else: the network, untouched
  e.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        e.waitUntil(caches.open(SHELL).then(c => c.put(shellKey(url), copy)).catch(() => {}));
        return res;
      }
      if (res.status < 500) return res;
      return (await caches.match(shellKey(url), { cacheName: SHELL })) || res;  // the server is down behind a proxy
    } catch (err) {
      const hit = await caches.match(shellKey(url), { cacheName: SHELL });
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
