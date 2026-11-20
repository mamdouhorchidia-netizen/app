// How the page is doing with the server: the offline banner (with the age of the prices on screen), the freshness
// pill ("● Live · updated 8 s ago", amber when stale, red when offline) and the status sheet it opens (each store).
import { $, ago, getSkew, mins, nowS, setSkew } from "./util.js";
import { L, LOC, agoL, t } from "./t.js";
import { openDialog } from "./sheets.js";

// shared with the refresh engine: hasPulse is false on an older server without /api/pulse
export const net = { offline: false, lastOK: 0, status: [], changes: 0, hasPulse: true };

export function setStatus(status, changes) { net.status = status; net.changes = changes; renderStatus(); }
export function contact(now) { setSkew(now); net.lastOK = Date.now() / 1000; setOffline(false); }
export function setOffline(on) {
  if (on === net.offline) return;
  net.offline = on;
  document.body.classList.toggle("is-offline", on);
  if (on) {
    const when = new Date(net.lastOK * 1000), old = Date.now() / 1000 - net.lastOK > 20 * 3600;
    $("offlineWhen").textContent = t(!net.lastOK ? "No prices loaded yet."
      : `Prices shown are from ${old ? when.toLocaleString(LOC(), { dateStyle: "medium", timeStyle: "short" }) : when.toLocaleTimeString(LOC(), { hour: "numeric", minute: "2-digit" })}.`);
  }
  $("offline").hidden = !on;
  renderStatus();
}

// the status sheet (one row per store) and the freshness pill: built once, then only changed text is rewritten;
// a row is translated only when its English changed (every second most rows say the same thing again)
let statusKey = "";
export function renderStatus() {
  const el = $("status");
  const rows = net.status.length ? net.status.map(s => {
    let line = ` ${s.products.toLocaleString()}`;
    if (s.paused_until) line += ` · paused, back in ${mins(s.paused_until - nowS())}`;
    else {
      if (s.live) line += s.last_change ? ` · changed ${ago(s.last_change)}` : " · watching";
      line += s.cycle_secs ? ` · full sweep ${mins(s.cycle_secs)}` : s.live ? " · first sweep" : ` · ${ago(s.updated)}`;
    }
    return [s.paused_until ? "paused" : s.live ? "live" : "idle", s.label, line.trim()];
  }).concat([["total", null, `${net.changes.toLocaleString()} price changes in the last hour`]])
    : [["total", null, !net.lastOK ? (net.offline ? "can't reach the server, retrying…" : "connecting…") : "starting: the first prices arrive within a minute"]];
  const key = rows.map(r => r[0]).join();
  if (key !== statusKey) {
    statusKey = key;
    el.replaceChildren(...rows.map(([k, b]) => { const r = document.createElement("div"); r.className = "srow " + k;
      if (b != null) { const d = document.createElement("span"); d.className = "sd"; r.append(d, document.createElement("b")); }
      r.append(document.createElement("span")); return r; }));
  }
  rows.forEach(([, b, line], i) => { const r = el.children[i];
    if (b != null && r._b !== b) { r._b = b; const tb = t(b); if (r.children[1].textContent !== tb) r.children[1].textContent = tb; }
    if (r._en !== line) { r._en = line; const tl = t(line); if (r.lastChild.textContent !== tl) r.lastChild.textContent = tl; } });
  renderFresh();
}
// how long since this page last heard from the server (it asks every ~10 s);
// amber once that is over a minute (or every store is paused), red while the server can't be reached
function renderFresh() {
  const age = net.lastOK ? Math.max(0, Date.now() / 1000 - net.lastOK) : null;
  const paused = net.status.length && net.status.every(s => s.paused_until || !s.live);
  const state = net.offline ? "off" : age == null || age > 60 || paused ? "stale" : "live";
  const when = age == null ? "" : age < 5 ? L("just now", "دلوقتي") : agoL(net.lastOK + getSkew());
  const text = state === "off" ? L("Offline", "مفيش اتصال") + (net.lastOK ? L(` · updated ${when}`, ` · اتحدّث ${when}`) : "")
    : age == null ? L("Connecting…", "بيتصل…")
    : (state === "live" ? L("Live", "مباشر") : paused ? L("Paused", "متوقف") : L("Not live", "مش محدّث")) + L(` · updated ${when}`, ` · اتحدّث ${when}`);
  if ($("freshT").textContent !== text) $("freshT").textContent = text;
  const cls = "fresh" + (state === "live" ? "" : " " + state);
  if ($("fresh").className !== cls) $("fresh").className = cls;
  const sub = L("Prices come from each store's own pages; this page asks for news every 10 seconds.", "الأسعار من صفحات المتاجر نفسها، والصفحة دي بتسأل على الجديد كل 10 ثواني.");
  if ($("statusSub").textContent !== sub) $("statusSub").textContent = sub;
}
export function initStatus() { $("fresh").onclick = () => { renderStatus(); openDialog($("statusDlg")); }; }
