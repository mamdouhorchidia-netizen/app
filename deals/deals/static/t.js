// The page's language at render time. Every string the page draws goes through t() as it is written, so it is
// put on screen already in Arabic: the same EXACT / RX tables as the server pages (/i18n.js, loaded before the
// modules, render-blocking, which also set dir/lang and translated the page's own markup once before first paint).
// Times and counts that change by themselves are written per language here (L, agoL): no table run on every tick.
import { nowS, span } from "./util.js";

const I = window.I18N;
export const lang = I ? I.lang : "en";
export const AR = lang === "ar";
// an English string (or a server-sent one) in the page's language; tables memoise, so repeats are a lookup
export const t = s => AR && s ? I.tr(String(s)) : s;
export const L = (en, ar) => AR ? ar : en;
// dates and times in the page's language (Arabic months with the Western digits the rest of the page uses)
export const LOC = () => AR ? "ar-EG-u-nu-latn" : undefined;
export const UNIT_AR = { s: "ث", min: "د", h: "س", d: "يوم" };
export const agoL = t => { if (!t) return "—"; const [n, u] = span(nowS() - t); return AR ? `من ${n} ${UNIT_AR[u]}` : `${n} ${u} ago`; };
