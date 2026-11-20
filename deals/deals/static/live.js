// The refresh engine: a tiny pulse every ~10 s while the page is in view (conditional: 304 when nothing moved), and
// the rest only when it says something moved: the cards (patched in place), the brief, the watchlist, the ticker.
// Every 30 s, when the cards on screen were last checked. Nothing at all while the tab is hidden; a growing backoff
// (10, 20, 40… 300 s) while the server can't be reached, and Retry.
import { $, jitter, setSkew } from "./util.js";
import { clearETags, pollDelay, pulse as getPulse, seen } from "./api.js";
import { contact, net, renderStatus, setOffline, setStatus } from "./status.js";
import { load, noteSeen, refreshDeals, seenIds, tickAgo, view } from "./grid.js";
import { refreshBrief } from "./brief.js";
import { checkWatch } from "./alerts.js";
import { loadTicker, resetTicker, tickerVersion } from "./ticker.js";

let pollT = 0, polling = false, failN = 0, pv = null, pbuilt = null, noSeenAPI = false, seenT = 0;
function schedule() {
  clearTimeout(pollT); pollT = 0;
  if (document.hidden) return;  // nothing at all while the tab is hidden
  pollT = setTimeout(poll, pollDelay(failN));
}
export async function poll() {
  if (polling || document.hidden) return;
  polling = true; clearTimeout(pollT);
  try { if (net.hasPulse) await pulse(); else await fallbackPoll(); failN = 0; }
  catch (e) { failN++; setOffline(true); }
  polling = false;
  schedule();
}
async function pulse() {
  let r;
  try { r = await getPulse(); }
  catch (e) { if (e.status === 404) { net.hasPulse = false; return fallbackPoll(); } throw e; }  // an older server
  if (!r) { contact(); if (view.dealsStale) await refreshDeals(); return; }  // 304: nothing moved
  contact(r.now);
  setStatus(r.status || [], r.changes_last_hour || 0);
  const down = pv != null && r.v < pv;  // renumbered (a restored database): start over
  if (down) { clearETags(); resetTicker(); }
  const built = r.catalog ? r.catalog.built : null, jobs = [];
  if (built != null && (built !== pbuilt || down || view.dealsStale)) jobs.push(refreshDeals(), refreshBrief(), checkWatch());
  if (r.v !== tickerVersion() || down) jobs.push(loadTicker());
  await Promise.all(jobs);
  pv = r.v; pbuilt = built; r.keep();  // only now: a failed step is tried again on the next pulse
}
async function fallbackPoll() {  // no /api/pulse: the cards themselves, asked conditionally
  const moved = await refreshDeals();
  contact();
  await Promise.all([loadTicker(), moved ? refreshBrief() : null, moved ? checkWatch() : null]);
}
// every 30 s while in view: when the cards on screen (and near it) were last checked, at most 60
export async function refreshSeen() {
  clearTimeout(seenT); seenT = 0;
  if (document.hidden || noSeenAPI) return;
  const ids = seenIds();
  if (ids.length && !failN) {
    try {
      const r = await seen(ids);
      setSkew(r.now);
      for (const [id, at] of Object.entries(r.seen || {})) noteSeen(+id, at);
      tickAgo($("grid"));
    } catch (e) { if (e.status === 404) noSeenAPI = true; }
  }
  if (!document.hidden && !noSeenAPI) seenT = setTimeout(refreshSeen, jitter(30000, .05));
}
export function initLive() {
  $("retry").onclick = async () => {
    $("retry").disabled = true;
    failN = 0;
    try { if (!view.items.length) await load(); await poll(); } finally { $("retry").disabled = false; }
  };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(pollT); clearTimeout(seenT); pollT = seenT = 0; return; }
    renderStatus(); tickAgo(); poll(); refreshSeen();  // back in view: catch up at once
  });
  addEventListener("online", () => { failN = 0; poll(); });
  // the clock on screen: the freshness pill and status every second, the "… ago" times every 5 s
  let secs = 0;
  setInterval(() => { if (document.hidden) return; renderStatus(); if (++secs % 5 === 0) tickAgo(); }, 1000);
}
