// Live deals: makes the site installable and shows price-drop notifications, even when it's closed.
const VIBRATE = [90, 60, 90, 60, 260];  // the deals "da-da-daaa" buzz (Android); the sound is the phone's own

self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
// network first; the site needs fresh prices, so nothing is served from a cache
self.addEventListener("fetch", () => {});

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
