// Small helpers every module uses: the DOM, escaping, numbers and money, the server's clock, this browser's storage,
// the store names and the link that opens a product in its store's app. No imports: the bottom of the module graph.

export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmt = n => n == null ? "" : Number(n).toLocaleString("en-EG", { maximumFractionDigits: 2 });
// a price on a card: the piasters in small type (so "3,299.62 EGP" and its was-price fit one line of a phone card);
// the struck-through was-price in whole pounds from 1,000 up (the exact one is in the product view)
export const money = n => { const [i, d] = fmt(n).split("."); return d ? `${i}<small class="dec">.${d.padEnd(2, "0")}</small>` : i; };
export const fmtWas = n => n != null && n >= 1000 ? fmt(Math.round(n)) : fmt(n);

// the server's clock: "ago" is measured from it, so a phone whose clock is off still says the right thing
let skew = 0;
export const nowS = () => Date.now() / 1000 + skew;
export const getSkew = () => skew;
export const setSkew = now => { if (now) skew = now - Date.now() / 1000; };
export const span = s => { s = Math.max(0, s);
  return s < 60 ? [Math.round(s), "s"] : s < 3600 ? [Math.round(s / 60), "min"] : s < 86400 ? [Math.round(s / 3600), "h"] : [Math.round(s / 86400), "d"]; };
export const ago = t => { if (!t) return "—"; const [n, u] = span(nowS() - t); return `${n} ${u} ago`; };
export const mins = s => s < 90 ? Math.round(s) + " s" : Math.round(s / 60) + " min";
export const jitter = (ms, j = .1) => ms * (1 - j + Math.random() * 2 * j);

export const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
                       set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };

export const isAndroid = /Android/i.test(navigator.userAgent), isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
export const appHref = d => (isAndroid ? d.links?.android : isIOS ? d.links?.ios : d.links?.web) || d.links?.web || d.url || "#";
export const appLabel = { talabat: "talabat", amazon_eg: "Amazon", breadfast: "Breadfast", jumia: "Jumia", dream2000: "Dream 2000", kenzz: "Kenzz", raya: "Raya" };
export const LABELS = { talabat: "talabat mart", amazon_eg: "Amazon.eg", breadfast: "Breadfast", jumia: "Jumia", dream2000: "Dream 2000", kenzz: "Kenzz", raya: "Raya Shop" };
