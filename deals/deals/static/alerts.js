// The watchlist (★, kept in this browser) and price alerts: the check after each catalog rebuild, the toast and
// notification, the deals chime, and real Web Push (the 🔔 panel), which also hears about the watchlist.
import { $, esc, fmt, isIOS, LABELS, store } from "./util.js";
import { lang, t } from "./t.js";
import { act, watch } from "./state.js";
import { getJSON, idsURL } from "./api.js";
import { menu, panel, toast } from "./sheets.js";

const tx = s => esc(t(s));
export function saveWatch() { store.set("watch", watch); syncAlerts(); const n = Object.keys(watch).length;
  for (const id of ["watchN", "tabWatchN"]) { $(id).hidden = !n; $(id).textContent = n; } }
export function askNotify() { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); }
export function toggleWatch(d, btn) {
  if (watch[d.id]) { delete watch[d.id]; toast(t("Removed from your watchlist.")); }
  else { watch[d.id] = { target: null, last: d.price, name: d.name, store: d.store };
         toast(t(`Watching it. You'll hear when the price drops.`)); askNotify(); }
  saveWatch();
  if (btn) btn.setAttribute("aria-pressed", String(!!watch[d.id]));
}
export async function checkWatch() {  // asked with its ETag, and only when the catalog was rebuilt
  const ids = Object.keys(watch);
  if (!ids.length) return;
  const r = await getJSON(idsURL(ids));
  if (!r) return;
  let changed = false;
  for (const d of r.items) {
    const w = watch[d.id]; if (!w) continue;
    const hitTarget = w.target && d.price <= w.target && (w.last == null || w.last > w.target);
    const dropped = w.last != null && d.price < w.last;
    if (hitTarget || dropped) notify(`${hitTarget ? "Target reached" : "Price drop"}: ${d.name}`,
      `${fmt(w.last)} → ${fmt(d.price)} EGP at ${LABELS[d.store] || d.store}`, d.id);
    if (w.last !== d.price) { w.last = d.price; changed = true; }
  }
  if (changed) saveWatch();
}
function notify(title, body, id) {
  chime();
  toast(`<b>${tx(title)}</b><br>${tx(body)}`, () => act.showProduct(id), true);
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    const n = new Notification(title, { body, icon: "/icon.svg", tag: "deal-" + id });
    n.onclick = () => { window.focus(); act.showProduct(id); };
  }
}

// the deals chime (its own sound, played while the site is open)
let audio;
export function chime() {
  if (!alertsPrefs().sound) return;
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const now = audio.currentTime;
    [[784, 0], [1047, .12], [1319, .24], [1568, .36]].forEach(([f, at], i) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.type = i === 3 ? "triangle" : "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, now + at); g.gain.linearRampToValueAtTime(.22, now + at + .02);
      g.gain.exponentialRampToValueAtTime(.001, now + at + (i === 3 ? .6 : .25));
      o.connect(g).connect(audio.destination); o.start(now + at); o.stop(now + at + .7);
    });
    if (navigator.vibrate) navigator.vibrate([90, 60, 90, 60, 260]);
  } catch {}
}

// price-drop alerts (push)
function alertsPrefs() { return Object.assign({ enabled: false, all_drops: false, min_drop: 20, stores: [], hubs: [], categories: [], words: [], sound: true, predict: true },
  store.get("alerts", {})); }
async function pushSubscription(create) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub && create) {
    const { key } = await (await fetch("/api/push/key")).json();
    const raw = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/") + "==".slice(0, (4 - key.length % 4) % 4)), c => c.charCodeAt(0));
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
  }
  return sub;
}
let syncT;
function syncAlerts() {  // the server needs the watchlist and choices to know what to tell this device
  clearTimeout(syncT);
  syncT = setTimeout(async () => {
    const a = alertsPrefs(); if (!a.enabled) return;
    try {
      const sub = await pushSubscription(false); if (!sub) return;
      const targets = {}; Object.entries(watch).forEach(([k, v]) => { if (v.target) targets[k] = v.target; });
      await fetch("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON(), prefs: { ...a, watch: Object.keys(watch).map(Number), targets, lang } }) });
    } catch {}
  }, 800);
}
function openAlerts() {
  const a = alertsPrefs(), supported = "PushManager" in window && "serviceWorker" in navigator;
  const facets = window.lastFacets || {};
  const cats = Object.keys(facets.category || {}).slice(0, 30);
  const hubs = Object.keys(facets.hub || {});
  panel(`<h2>${tx("🔔 Price-drop alerts")}</h2>
    ${supported ? "" : `<p class="tip">${tx(isIOS ? "On iPhone, alerts work from the installed app: tap ⬇︎ Install first (iOS 16.4+)." : "This browser can't receive alerts.")}</p>`}
    <div class="set">
      <label><input type="checkbox" id="aOn" ${a.enabled ? "checked" : ""} ${supported ? "" : "disabled"}> <b>${tx("Alerts on this device")}</b></label>
      <label><input type="checkbox" checked disabled>${tx(" Products on my watchlist ★ (and their target prices)")}</label>
      <label><input type="checkbox" id="aPredict" ${a.predict !== false ? "checked" : ""}>${tx(" ⏳ Before a watched product's predicted sale")}</label>
      <label><input type="checkbox" id="aAll" ${a.all_drops ? "checked" : ""}>${tx(" Any price drop of at least ")}<input type="number" id="aMin" min="5" max="90" step="5" value="${a.min_drop}"> %</label>
      <div><div class="tip">${tx("…only at these stores (none = all)")}</div><div class="pick" id="aStores">${Object.entries(LABELS).map(([k, l]) =>
        `<label><input type="checkbox" value="${k}" ${a.stores.includes(k) ? "checked" : ""}>${tx(" " + l)}</label>`).join("")}</div></div>
      ${hubs.length > 1 ? `<div><div class="tip">${tx("…only these talabat hubs")}</div><div class="pick" id="aHubs">${hubs.map(h => { const [id, n] = h.split("|");
        return `<label><input type="checkbox" value="${id}" ${a.hubs.map(String).includes(id) ? "checked" : ""}>${tx(" " + n)}</label>`; }).join("")}</div></div>` : ""}
      <div><div class="tip">${tx("…only these categories")}</div><div class="pick" id="aCats">${cats.map(c =>
        `<label><input type="checkbox" value="${esc(c)}" ${a.categories.includes(c) ? "checked" : ""}>${tx(" " + c)}</label>`).join("")}</div></div>
      <label style="display:block">${tx("…only names containing (comma-separated)")}<input type="text" id="aWords" value="${esc(a.words.join(", "))}" placeholder="${tx("milk, nescafe, حفاضات")}"></label>
      <label><input type="checkbox" id="aSound" ${a.sound ? "checked" : ""}>${tx(" Deals chime and buzz while the site is open")}</label>
    </div>
    <div class="row"><button class="btn primary" id="aSave">${tx("Save")}</button><button class="btn" id="aTest">${tx("Send a test")}</button><button class="btn" id="aHear">${tx("▶ Hear the chime")}</button></div>
    <p class="tip">${tx("Alerts arrive even when the site is closed, at most one bundle every 5 minutes. When the site is closed, the phone plays its notification sound with the deals buzz pattern; browsers don't allow a custom sound there.")}</p>`);
  $("aHear").onclick = () => { const was = alertsPrefs(); store.set("alerts", { ...was, sound: true }); chime(); store.set("alerts", was); };
  $("aSave").onclick = async () => {
    const pick = id => [...document.querySelectorAll(`#${id} input:checked`)].map(i => i.value);
    const next = { enabled: $("aOn").checked, all_drops: $("aAll").checked, min_drop: +$("aMin").value || 20,
      stores: pick("aStores"), hubs: document.getElementById("aHubs") ? pick("aHubs").map(Number) : [], categories: pick("aCats"),
      words: $("aWords").value.split(",").map(x => x.trim()).filter(Boolean), sound: $("aSound").checked,
      predict: $("aPredict").checked };
    try {
      if (next.enabled) {
        if (Notification.permission !== "granted" && await Notification.requestPermission() !== "granted") {
          toast(t("Notifications are blocked for this site: allow them in the browser's site settings.")); return; }
        await pushSubscription(true);
      } else {
        const sub = await pushSubscription(false);
        if (sub) { await fetch("/api/push/unsubscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) }); await sub.unsubscribe(); }
      }
      store.set("alerts", next); syncAlerts();
      toast(t(next.enabled ? "Alerts are on. 🔔" : "Alerts are off.")); $("panel").close();
    } catch (e) { toast(t("Couldn't turn alerts on: " + (e.message || e))); }
  };
  $("aTest").onclick = async () => {
    const sub = await pushSubscription(false).catch(() => null);
    if (!sub) { toast(t("Turn alerts on and save first.")); return; }
    syncAlerts(); await new Promise(r => setTimeout(r, 1200));
    const r = await fetch("/api/push/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) });
    const j = await r.json().catch(() => ({}));
    // the server's own error text is left to the language fallback
    if (r.ok) toast(t("Test sent: it should arrive in a few seconds.")); else if (j.error) toast(j.error, null, false, true); else toast(t("Test failed."));
  };
}
export function initAlerts() {
  $("alertsBtn").onclick = openAlerts;
  // a push that arrives while the page is open: the chime, and a toast (its text as the server wrote it)
  navigator.serviceWorker && navigator.serviceWorker.addEventListener("message", e => {
    if (e.data && e.data.type === "deal-push") { chime(); toast(`<b>${esc(e.data.m.title)}</b><br>${esc(e.data.m.body)}`, () => location.href = e.data.m.url || "/", true, true); }
  });
}
export function initAlertsMenu() {
  $("alertsMi").onclick = () => { menu(false); $("moreBtn").focus(); openAlerts(); };  // its panel gives focus back to ⋯
  $("tabAlerts").onclick = openAlerts;
}
