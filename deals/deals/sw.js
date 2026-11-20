// Live deals: makes the site installable, opens instantly on a repeat visit, and shows price-drop notifications,
// even when it's closed.
//
// What is cached, and how:
// - the page shell ("/", "/live"), i18n.js, the icons and the manifest: stale-while-revalidate. The device answers at once
//   and the network refreshes the copy in the background; when the shell changed, open pages are told ("shell-updated").
// - the last home-view /api/deals answer (the page marks that request with X-Home-View): network first, kept so the next
//   visit can paint cards before the network answers (the page reads it itself, then asks the network anyway).
// - never: /admin, anything that isn't a GET (POSTs), /api/push/*, any other /api/ call, other sites.
// Bump VERSION on every deploy that changes the shell: the new worker takes over at once and deletes the old caches.
const VERSION = "2026-11-20.1";
const SHELL = "deals-shell-" + VERSION;
const DATA = "deals-data-v1";           // the page reads this one too (DATA_CACHE in live.html)
const PRECACHE = ["/", "/i18n.js", "/icon-192.png", "/icon.svg", "/manifest.webmanifest"];
const SHELL_PATHS = new Set(["/", "/live", "/i18n.js", "/icon.svg", "/icon-192.png", "/icon-512.png", "/icon-maskable.png", "/manifest.webmanifest"]);
const VIBRATE = [90, 60, 90, 60, 260];  // the deals "da-da-daaa" buzz (Android); the sound is the phone's own

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    // cache: "reload" so a new worker never copies an old shell out of the HTTP cache
    await Promise.all(PRECACHE.map(u => fetch(u, { cache: "reload" }).then(r => r.ok && c.put(u, r)).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("deals-shell-") && k !== SHELL) await caches.delete(k);
    await self.clients.claim();
  })());
});

const never = url => url.pathname.startsWith("/admin") || url.pathname.startsWith("/api/push/");

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;                       // POSTs and the rest: straight to the network
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || never(url)) return;
  if (url.pathname === "/api/deals" && req.headers.get("X-Home-View") === "1" && !req.headers.get("If-None-Match")) {
    e.respondWith(homeView(req));
    return;
  }
  const shellKey = req.mode === "navigate" ? (url.pathname === "/" || url.pathname === "/live" ? "/" : null)
    : SHELL_PATHS.has(url.pathname) && !url.search ? url.pathname : null;
  if (shellKey) e.respondWith(staleWhileRevalidate(e, shellKey));
  // everything else (/api/*, /p/ID, /stores, …) goes to the network untouched
});

async function homeView(req) {
  const r = await fetch(req);
  if (r.ok && r.status === 200) {
    const c = await caches.open(DATA);
    for (const k of await c.keys()) await c.delete(k);      // only the latest home view is kept
    await c.put(req.url, r.clone());
  }
  return r;
}

async function staleWhileRevalidate(e, key) {
  const c = await caches.open(SHELL);
  const hit = await c.match(key);
  const net = fetch(key === "/" ? "/" : e.request, { cache: "no-cache" }).then(async r => {
    if (!r.ok || r.status !== 200) return r;
    if (key === "/" && hit) {   // a new deploy: tell open pages so nobody stays on an old shell
      const [a, b] = await Promise.all([hit.clone().text(), r.clone().text()]);
      if (a !== b) {  // wait until the page that asked is up and listening
        await new Promise(res => setTimeout(res, 2500));
        for (const w of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) w.postMessage({ type: "shell-updated" });
      }
    }
    await c.put(key, r.clone());
    return r;
  });
  if (hit) { e.waitUntil(net.catch(() => {})); return hit; }
  return net;
}

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
