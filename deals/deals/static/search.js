// Plain-language searches (English and Arabic: "milk under 50 at talabat", "حفاضات أقل من ٣٠٠ من طلبات"), what was
// understood shown under the box, and voice search.
import { $, esc, LABELS } from "./util.js";
import { t } from "./t.js";
import { F, act } from "./state.js";
import { toast } from "./sheets.js";

const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";
export function understand(text) {
  let s = " " + (text || "").replace(/[٠-٩]/g, c => AR_DIGITS.indexOf(c)) + " ";
  const set = {}, said = [];
  const take = (re, fn) => { s = s.replace(re, (...m) => { fn(m); return " "; }); };
  take(/\b(?:at least|over|above|min(?:imum)?)\s*(\d{1,2})\s*%(?:\s*off)?|(\d{1,2})\s*%\s*(?:off|\+|or more)|(?:خصم|تخفيض)\s*(\d{1,2})\s*%?/gi,
       m => { const v = m[1] || m[2] || m[3]; set.min = v; said.push(`${v}%+ off`); });
  take(/\b(?:under|below|less than|cheaper than|max(?:imum)?|up to)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|pounds?|جنيه)?|(?:أقل من|اقل من|تحت|بحد أقصى)\s*(\d+(?:\.\d+)?)\s*(?:جنيه)?|<\s*(\d+)/gi,
       m => { const v = m[1] || m[2] || m[3]; set.pmax = v; said.push(`under ${v} EGP`); });
  take(/\b(?:over|above|more than)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|pounds?)\b|(?:أكثر من|اكثر من|فوق)\s*(\d+(?:\.\d+)?)\s*(?:جنيه)?/gi,
       m => { const v = m[1] || m[2]; set.pmin = v; said.push(`over ${v} EGP`); });
  const stores = [];
  take(/(?:^|\s)(?:at |on |from |in |من |في )?(talabat|tmart|طلبات)(?=[\s,.]|$)/gi, () => stores.push("talabat"));
  take(/(?:^|\s)(?:at |on |from |in |من |في )?(amazon|أمازون|امازون)(?=[\s,.]|$)/gi, () => stores.push("amazon_eg"));
  take(/(?:^|\s)(?:at |on |from |in |من |في )?(breadfast|بريدفاست)(?=[\s,.]|$)/gi, () => stores.push("breadfast"));
  if (stores.length) { set.stores = [...new Set(stores)].join(","); said.push([...new Set(stores)].map(x => LABELS[x]).join(" or ")); }
  take(/\b(?:per|a|each|by the)\s*(?:kg|kilo|liter|litre|l|piece)\b|(?:للكيلو|للتر|بالكيلو|للقطعة)/gi, () => { set.sort = "unit"; said.push("cheapest per kg / L"); });
  take(/\b(cheapest|lowest price|cheap)\b|(?:أرخص|ارخص)/gi, () => { if (!set.sort) { set.sort = "price"; said.push("cheapest first"); } });
  take(/\b(biggest (?:discount|deal)s?|best deals?)\b|(?:أكبر خصم|اكبر خصم|أفضل عرض)/gi, () => { set.sort = "discount"; said.push("biggest discount"); });
  take(/\b(lowest ever|all[- ]time low|record low)\b|(?:أقل سعر)/gi, () => { set.status = "lowest"; said.push("lowest in 90 days"); });
  take(/\b(new deals|new today|just dropped|today)\b|(?:جديد النهارده|النهارده|اليوم)/gi, () => { set.status = (set.status ? set.status + "," : "") + "new"; said.push("changed in 24 h"); });
  take(/\b(in stock|available)\b|(?:متاح|متوفر)/gi, () => { set.stock = "in"; said.push("in stock"); });
  take(/\b(deals?|offers?|discounts?|sale)\b|(?:عروض|عرض|خصومات|تخفيضات)/gi, () => { set.deals = "1"; });
  const q = s.replace(/\b(?:show me|find|i want|search for|the|for)\b|(?:عايز|عاوز|ابحث عن|هات)/gi, " ").replace(/\s+/g, " ").trim();
  if (Object.keys(set).length && !("deals" in set)) set.deals = "0";  // "milk under 50" means every milk, not only deals
  return { q, set, said };
}

export function showUnderstood() {
  const nl = understand(F.q);
  $("interp").hidden = !nl.said.length;
  $("interp").innerHTML = nl.said.length ? esc(t("Understood: ")) + nl.said.map(x => `<b>${esc(t(x))}</b>`).join(" ")
    + (nl.q ? ` <b>${esc(t(`“${nl.q}”`))}</b>` : "") : "";
}

export function initVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  $("mic").hidden = false;
  $("mic").onclick = () => {
    const r = new SR();
    r.lang = /^ar/i.test(navigator.language) ? "ar-EG" : "en-US"; r.interimResults = false; r.maxAlternatives = 1;
    $("mic").classList.add("on");
    r.onresult = e => { $("q").value = e.results[0][0].transcript; F.q = $("q").value; showUnderstood(); act.apply(); };
    r.onend = () => $("mic").classList.remove("on");
    r.onerror = () => { $("mic").classList.remove("on"); toast(t("Couldn't hear that: try again, or type.")); };
    r.start();
  };
}
