// What opens on top of the page: the ⋯ menu, the filter sheet (a bottom sheet on phones, a drawer on tablets),
// the dialogs (product view, panels, store status), toasts. Focus stays inside whatever is open on top and goes
// back to its opener when it closes; a skip link comes first.
import { $, esc } from "./util.js";
import { t } from "./t.js";

// a toast says something for 6 s; a tap runs `onclick`. `raw`: html already escaped and in the page's language.
// `external`: text that arrived from elsewhere (a push relayed by the service worker, a server's error message),
// which the language fallback (I18N.observe on #toasts) translates if it can; everything else is written translated.
export function toast(html, onclick, raw, external) {
  const n = document.createElement("div"); n.className = "toast";
  if (!external) n.dataset.noi18n = "";
  n.innerHTML = raw ? html : esc(html);
  n.onclick = () => { if (onclick) onclick(); n.remove(); };
  $("toasts").append(n); setTimeout(() => n.remove(), 6000);
}
// a dialog gives focus back to what opened it (a card, a ticker item, the freshness pill, a tab)
export function openDialog(dlg) {
  if (dlg.open) return;
  const from = document.activeElement;
  dlg._from = from && from !== document.body ? from : null;
  dlg.showModal();
}
export const closeX = () => `<button class="x" aria-label="${esc(t("Close"))}" onclick="this.closest('dialog').close()">×</button>`;
export function panel(html) { $("pbody").innerHTML = closeX() + html; openDialog($("panel")); }

// the ⋯ menu
export const menu = open => { $("moreMenu").hidden = !open; $("moreBtn").setAttribute("aria-expanded", String(open));
  if (open) $("moreMenu").querySelector(".mi:not([hidden])")?.focus({ preventScroll: true }); };
// the filters: a bottom sheet on phones, a side drawer on tablets (the rail on wide screens needs neither)
let sheetFrom = null;
export const sheet = open => { const was = $("filters").classList.contains("open"), f = $("filters");
  f.classList.toggle("open", open);
  // open, it is a modal sheet (focus stays in it, see the Tab trap); closed, the rail or nothing
  if (open) { f.setAttribute("role", "dialog"); f.setAttribute("aria-modal", "true"); } else { f.removeAttribute("role"); f.removeAttribute("aria-modal"); }
  document.querySelector(".scrim")?.remove();
  if (open) { const s = document.createElement("div"); s.className = "scrim"; s.onclick = () => sheet(false); document.body.append(s);
    sheetFrom = document.activeElement; setTimeout(() => $("sheetX").focus({ preventScroll: true }), 50); }
  else if (was && sheetFrom && sheetFrom.isConnected) { sheetFrom.focus({ preventScroll: true }); sheetFrom = null; } };

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex='-1'])";
export const shown = el => el.checkVisibility ? el.checkVisibility({ visibilityProperty: true }) : !!el.offsetParent;
function topLayer() {  // what keeps focus now: a modal dialog, the ⋯ menu, the filter sheet
  const dlg = [...document.querySelectorAll("dialog[open]")].pop();
  if (dlg) return dlg;
  if (!$("moreMenu").hidden) return $("moreMenu");
  if ($("filters").classList.contains("open")) return $("filters");
  return null;
}

// the wiring, in the order the page has always added it (two document keydown listeners: Tab first, then keys)
export function initSheets() {
  $("filterBtn").onclick = () => sheet(true);
  $("sheetDone").onclick = $("sheetX").onclick = () => sheet(false);
  $("moreBtn").onclick = e => { e.stopPropagation(); menu($("moreMenu").hidden); };
  document.addEventListener("click", e => { if (!$("moreMenu").hidden && !e.target.closest(".more")) menu(false); });
  $("moreMenu").addEventListener("keydown", e => {
    const its = [...$("moreMenu").querySelectorAll(".mi")].filter(x => !x.hidden && x.offsetParent), i = its.indexOf(document.activeElement);
    if (e.key === "Escape") { menu(false); $("moreBtn").focus(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); its[(i + (e.key === "ArrowDown" ? 1 : its.length - 1)) % its.length]?.focus(); }
  });
  // the phone tab bar's search: the same box as the header's
  $("tabSearch").onclick = () => { scrollTo({ top: 0 }); $("q").focus(); };
}
export function initKeyboard() {
  // a skip link first; it lands on the first card
  $("skip").onclick = e => {
    e.preventDefault();
    const first = $("grid").querySelector("[data-detail]");
    if (first) { first.focus({ preventScroll: true }); first.closest(".card").scrollIntoView({ block: "center" }); }
    else { $("grid").tabIndex = -1; $("grid").focus(); }
  };
  document.addEventListener("keydown", e => {  // Tab and Shift+Tab go round inside whatever is on top
    if (e.key !== "Tab" || e.altKey || e.ctrlKey || e.metaKey) return;
    const box = topLayer(); if (!box) return;
    const f = [...box.querySelectorAll(FOCUSABLE)].filter(shown);
    if (!f.length) return;
    const a = document.activeElement, i = f.indexOf(a);
    if (!box.contains(a) || (e.shiftKey ? i <= 0 : i === f.length - 1)) { e.preventDefault(); (e.shiftKey ? f[f.length - 1] : f[0]).focus(); }
  });
  for (const id of ["dlg", "panel", "statusDlg"]) $(id).addEventListener("close", () => {
    const from = $(id)._from; $(id)._from = null;
    if (from && from.isConnected && shown(from)) from.focus({ preventScroll: true });
  });
  document.addEventListener("keydown", e => {
    if (e.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) { e.preventDefault(); $("q").focus(); }
    if (e.key === "Escape" && $("filters").classList.contains("open")) sheet(false);
    if (e.key === "Escape" && !$("moreMenu").hidden) { menu(false); $("moreBtn").focus(); }
  });
}
