// The live page's entry point: wires the modules together and starts it. The imports have no cycles; in layers,
// each importing only from the ones before it:
//   util · t (the language) · state · sheets (menu, sheet, dialogs, toasts, keyboard) · search · api
//   · card · status (offline banner, freshness pill, store status) · filters · alerts (watchlist, push) · basket
//   · grid (cards on screen, patching) · product (the product view) · brief · ticker · live (the refresh engine) · main
// The two calls that go back up (apply the filters, show a product) go through state.act, which this fills in.
import { $, isIOS } from "./util.js";
import { t } from "./t.js";
import { act, readURL, writeURL } from "./state.js";
import { initVoice, showUnderstood } from "./search.js";
import { initKeyboard, initSheets, menu, panel, toast } from "./sheets.js";
import { initStatus, renderStatus } from "./status.js";
import { activeChips, initFilters, syncControls } from "./filters.js";
import { checkWatch, initAlerts, initAlertsMenu, saveWatch } from "./alerts.js";
import { initBasket, saveBasket } from "./basket.js";
import { initGrid, initGridInput, load } from "./grid.js";
import { initProduct, showProduct } from "./product.js";
import { loadBrief } from "./brief.js";
import { loadTicker } from "./ticker.js";
import { initLive, poll, refreshSeen } from "./live.js";

// the page's own markup in the page's language (normally done already, before first paint, by /i18n.js)
window.I18N?.shell?.();
// the language fallback: toasts whose text arrives from elsewhere (pushes relayed by the service worker, a server's
// own error message) can't be written translated; everything else on the page is, as it is drawn
window.I18N?.observe?.($("toasts"));

function apply() { writeURL(); activeChips(); load(); loadBrief(); }
act.apply = apply;
act.showProduct = showProduct;

document.querySelectorAll("#nav a, #moreMenu a.mi, #tabHome").forEach(a => { const u = new URL(a.href);
  if (u.pathname === location.pathname && u.search === location.search) { a.classList.add("on"); a.setAttribute("aria-current", "page"); } });

// the wiring, in the order the page has always added it
initGrid(); initLive(); initStatus(); initProduct(); initFilters(); initSheets(); initGridInput(); initKeyboard();
initVoice();

// installable app
let installEvent = null;
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installEvent = e; $("installBtn").hidden = false; });
if (!standalone && isIOS) $("installBtn").hidden = false;
const b = s => `<b>${t(s)}</b>`, li = (...xs) => `<li>${xs.join("")}</li>`;
$("installBtn").onclick = async () => {
  menu(false); $("moreBtn").focus();
  if (installEvent) { installEvent.prompt(); const r = await installEvent.userChoice; if (r.outcome === "accepted") $("installBtn").hidden = true; installEvent = null; return; }
  panel(`<h2>${t("Install Live deals")}</h2>${isIOS
    ? `<ol>${li(t("Tap the "), b("Share"), t(" button (the square with an arrow) at the bottom of Safari."))}
    ${li(t("Choose "), b("Add to Home Screen"), t(", then "), b("Add"), ".")}${li(t("Open it from the new icon. Alerts work only from the installed app on iPhone (iOS 16.4 or later)."))}</ol>`
    : `<ol>${li(t("In Chrome, open the "), b("⋮"), t(" menu."))}${li(t("Choose "), b("Install app"), t(" (or "), b("Add to Home screen"), t(")."))}</ol>`}`);
};
window.addEventListener("appinstalled", () => { $("installBtn").hidden = true; toast(t("Installed. Find Live deals on your home screen.")); });

initAlerts(); initBasket(); initAlertsMenu();

readURL(); syncControls(); saveWatch(); saveBasket(); activeChips(); showUnderstood(); renderStatus();
// first the page, then the pulse every ~10 s (it starts the 30 s "checked" refresh once there are cards)
load().then(() => { poll(); refreshSeen(); });
loadTicker().catch(() => {}); loadBrief(); checkWatch().catch(() => {});
const pid = new URLSearchParams(location.search).get("p"); if (pid) showProduct(+pid);
