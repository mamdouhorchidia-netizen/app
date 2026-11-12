// Arabic (Egyptian, right to left) for the deals pages, with English one tap away.
// The pages are written in English; this turns what they show into Arabic as it appears (a MutationObserver),
// so the rendering code stays one piece. Product names are never touched: they sit in [dir=auto] elements.
(function () {
  const KEY = "lang";
  let lang = "ar";
  try { lang = localStorage.getItem(KEY) || "ar"; } catch {}

  const STORE = { "talabat mart": "طلبات مارت", "Amazon.eg": "أمازون مصر", "Jumia": "جوميا", "Breadfast": "بريدفاست" };
  const APP = { talabat: "طلبات", Amazon: "أمازون", Jumia: "جوميا", Breadfast: "بريدفاست", store: "المتجر" };
  const EXACT = {
    // header and filters
    "Live deals": "عروض لحظة بلحظة", "connecting…": "بيتصل…", "Filters": "فلاتر", "Install": "تثبيت",
    "Show": "اعرض", "Deals": "العروض", "All products": "كل المنتجات", "Store": "المتجر", "talabat hub": "فرع طلبات",
    "Discount type": "نوع الخصم", "Any": "أي نوع", "Platform": "خصم المتجر", "Price drop": "نزول السعر", "Discount": "الخصم",
    "any": "أي نسبة", "Availability": "التوفر", "In stock": "متاح", "Out": "خلصان", "All": "الكل", "Status": "الحالة",
    "Real deals only": "عروض حقيقية بس", "Changed in the last 24 h": "اتغير في آخر 24 ساعة",
    "Lowest price in 90 days": "أقل سعر في 90 يوم", "Store event deals": "عروض المناسبات", "Back in stock": "رجع تاني",
    "Sold in other stores too": "موجود في متاجر تانية", "Cheapest across stores": "الأرخص بين المتاجر",
    "My watchlist": "قايمة المتابعة", "Category": "القسم", "Price (EGP)": "السعر (ج.م)", "From": "من", "To": "لحد",
    "Reset filters": "امسح الفلاتر", "Show results": "اعرض النتايج", "All categories": "كل الأقسام",
    "Best deals first": "أحسن العروض الأول", "Biggest discount %": "أكبر خصم %", "Biggest saving (EGP)": "أكبر توفير (ج.م)",
    "Just changed": "اللي نزل دلوقتي", "Biggest drop vs usual": "أكبر نزول عن سعره", "Price: low to high": "السعر: من الأقل",
    "Price: high to low": "السعر: من الأعلى", "Price per kg / L / piece": "سعر الكيلو / اللتر / القطعة",
    "Best sellers": "الأكثر مبيعًا", "Name": "الاسم", "deals": "عرض", "deal": "عرض", "products": "منتج", "EGP": "ج.م",
    // tags and cards
    "lowest in 90 days": "🔥 أقل سعر", "back in stock": "رجع تاني", "cheapest of all stores": "الأرخص في كل المتاجر",
    "discount never moves": "خصم ثابت (مش حقيقي)", "out of stock": "خلصان", "Buy now": "اشتري دلوقتي",
    "Good price": "سعر كويس", "Fair price": "سعره العادي", "Wait": "استنى", "Cheaper elsewhere": "أرخص في مكان تاني",
    "Out of stock": "خلصان", "11.11 Deal": "عرض 11.11", "Limited time deal": "عرض لفترة محدودة", "Flash sale": "فلاش سيل",
    "Platform discount": "خصم المتجر", "Any availability": "أي توفر", "Real deals": "عروض حقيقية", "Changed 24 h": "اتغير 24 ساعة",
    "Lowest 90 d": "أقل سعر 90 يوم", "Event deals": "عروض المناسبات", "In other stores": "في متاجر تانية", "Watchlist": "المتابعة",
    // product view
    "Price history": "تاريخ السعر", "Same product in other stores": "نفس المنتج في متاجر تانية",
    "Tell me when it's at or below": "نبهني لما يوصل أو يقل عن", "Watch": "تابِع", "Update alert": "حدّث التنبيه",
    "Open in browser": "افتح في المتصفح", "Share": "شارك", "Add to basket": "ضيف للسلة", "cheapest": "الأرخص",
    "Open ↗": "افتح ↗", "now": "دلوقتي", "Touch or point at the chart to see each price.": "المس الرسم عشان تشوف كل سعر.",
    "One price so far: the chart fills in as it changes.": "سعر واحد لحد دلوقتي: الرسم هيكمل لما السعر يتغير.",
    "Open on the web": "افتح على الويب", "Go to store": "روح للمتجر",
    // toasts, alerts, basket, install
    "Removed from your watchlist.": "اتشال من قايمة المتابعة.",
    "Watching it. You'll hear when the price drops.": "بنتابعه. هنبلغك أول ما سعره ينزل.",
    "Link copied.": "اتنسخ اللينك.", "Copied": "اتنسخ", "Watching for any price drop.": "بنتابع أي نزول في السعر.",
    "Added to your basket. 🧺 shows where it's cheapest.": "اتضاف للسلة. 🧺 هتوريك فين أرخص.",
    "🔔 Price-drop alerts": "🔔 تنبيهات نزول الأسعار", "Alerts on this device": "التنبيهات على الجهاز ده",
    "Products on my watchlist ★ (and their target prices)": "المنتجات اللي بتابعها ★ (والسعر اللي مستنيه)",
    "Any price drop of at least": "أي نزول في السعر بنسبة لا تقل عن", "…only at these stores (none = all)": "…من المتاجر دي بس (ولا حاجة = الكل)",
    "…only these talabat hubs": "…فروع طلبات دي بس", "…only these categories": "…الأقسام دي بس",
    "…only names containing (comma-separated)": "…الأسماء اللي فيها (افصل بفاصلة)",
    "Deals chime and buzz while the site is open": "صوت التنبيه والاهتزاز والموقع مفتوح", "Save": "احفظ",
    "Send a test": "ابعت تجربة", "▶ Hear the chime": "▶ اسمع الصوت", "Alerts are on. 🔔": "التنبيهات اشتغلت 🔔",
    "Alerts are off.": "التنبيهات اتقفلت.", "Turn alerts on and save first.": "شغّل التنبيهات واحفظ الأول.",
    "Test sent: it should arrive in a few seconds.": "التجربة اتبعتت: هتوصل في ثواني.",
    "🧺 Basket": "🧺 السلة", "Cheapest plan": "أرخص طريقة", "Empty basket": "فضّي السلة",
    "Install Live deals": "ثبّت عروض لحظة بلحظة", "Installed. Find Live deals on your home screen.": "اتثبت. هتلاقيه على الشاشة الرئيسية.",
    "Couldn't hear that: try again, or type.": "مسمعتش كويس: جرب تاني أو اكتب.",
    "starting: the first prices arrive within a minute": "بيبدأ: أول الأسعار هتوصل خلال دقيقة",
    "can't reach the server, retrying…": "مش قادر يوصل للسيرفر، بيحاول تاني…",
    "Nothing matches these filters yet. The first sweep of each store takes about 15 minutes, and price-drop deals need a few days of history.":
      "مفيش نتايج للفلاتر دي لسه. أول لفة على كل متجر بتاخد حوالي ربع ساعة، وعروض نزول السعر محتاجة كام يوم تاريخ.",
    "Your watchlist is empty: tap ★ on any product to follow its price.": "قايمة المتابعة فاضية: دوس ★ على أي منتج عشان تتابع سعره.",
    // stores, coupons, categories pages
    "Stores": "المتاجر", "Categories": "الأقسام", "Coupons": "كوبونات", "Home": "الرئيسية", "Recently viewed": "كمّل من حيث وقفت",
    "All stores": "كل المتاجر", "Tap a code to copy it": "دوس على الكوبون يتنسخ على طول", "Just dropped": "اللي نزل دلوقتي",
    "See all": "شوف الكل", "🎟️ Coupons": "🎟️ كوبونات", "🧺 Add to basket": "🧺 ضيف للسلة", "Product page": "صفحة المنتج",
    "★ Watch": "★ تابع السعر", "More deals like this": "عروض شبهه", "Lowest in 90 days": "أقل سعر في 90 يوم",
    "Highest in 90 days": "أعلى سعر في 90 يوم", "Usual price": "السعر المعتاد", "Price changes": "مرات تغيير السعر",
    "Not found": "مش موجود", "This product isn't tracked (any more).": "المنتج ده مش متتابع (خلاص).",
    "Prices are checked around the clock from each store's public pages. A price can change at the store before it changes here.":
      "الأسعار بتتحدّث طول اليوم من صفحات المتاجر نفسها. ممكن السعر يتغير في المتجر قبل ما يتغير هنا.", "Telegram channel": "قناة التليجرام", "No coupons yet.": "مفيش كوبونات لسه.",
  };
  const RX = [
    [/^Open in (talabat|Amazon|Jumia|Breadfast|store) app ↗$/, (m, a) => `افتح في تطبيق ${APP[a]} ↗`],
    [/^Open in (talabat|Amazon|Jumia|Breadfast|store) ↗$/, (m, a) => `افتح في ${APP[a]} ↗`],
    [/^changed (.+)$/, "نزل $1"], [/^usually (.+)$/, "عادة $1"], [/^save (.+)$/, "وفّر $1"], [/^saves (.+) against.*$/, "بتوفر $1"],
    [/^([\d,.]+) at (.+)$/, (m, p, s) => `${p} في ${STORE[s] || s}`],
    [/^In basket \((\d+)\)$/, "في السلة ($1)"], [/^🧺 In basket \((\d+)\)$/, "🧺 في السلة ($1)"],
    [/^tracked since (.+)$/, "متابَع من $1"], [/^up to (\d+)% off$/, "خصومات لحد $1%"], [/^until (.+)$/, "لحد $1"],
    [/^deals · ([\d,]+) products$/, "عرض · $1 منتج"], [/^deals · up to (\d+)% off$/, "عرض · خصومات لحد $1%"],
    [/^live$/, "شغال دلوقتي"], [/^paused$/, "متوقف شوية"], [/^not running$/, "مش شغال"],
    [/^Saved (\d+) coupon\(s\)\.$/, "اتحفظ $1 كوبون."], [/^tracked (\d+) (s|min|h|d)$/, (m, n, u) => `متابَع من ${n} ${{ s: "ث", min: "د", h: "س", d: "يوم" }[u]}`], [/^tracked (.+)$/, "متابَع من $1"],
    [/^(\d+) talabat hubs?$/, "$1 فرع طلبات"], [/^(\d+%\+?|\d+–\d+%) off$/, "خصم $1"],
    [/^(\d+) platform discounts · (\d+) price drops$/, "$1 خصم متجر · $2 نزول سعر"],
    [/^Understood:/, "فهمت:"], [/^under ([\d.]+) EGP$/, "أقل من $1 ج.م"], [/^over ([\d.]+) EGP$/, "أكتر من $1 ج.م"],
    [/^cheapest per kg \/ L$/, "الأرخص للكيلو/اللتر"], [/^cheapest first$/, "الأرخص الأول"], [/^biggest discount$/, "أكبر خصم"],
    [/^changed in 24 h$/, "اتغير في 24 ساعة"], [/^in stock$/, "متاح"],
    // advice and reasons from the server
    [/^([\d.]+)% off the store's own price$/, "خصم $1% من سعر المتجر"],
    [/^([\d.]+)% under its usual ([\d.,]+) EGP$/, "أقل $1% من سعره المعتاد $2 ج.م"],
    [/^and ([\d.]+)% under its usual price$/, "وأقل $1% من سعره المعتاد"], [/^lowest price in 90 days\.?$/i, "أقل سعر في 90 يوم"],
    [/^price changed in the last 24 h$/, "السعر اتغير في آخر 24 ساعة"], [/^cheapest of the stores that sell it$/, "الأرخص بين المتاجر اللي بتبيعه"],
    [/^same 'discounted' price for ([\d.]+) days: the old price may never be charged$/, "نفس السعر 'المخفّض' من $1 يوم: يمكن السعر القديم عمره ما اتباع بيه"],
    [/^([\d.]+)% off\.$/, "خصم $1%."], [/^Usually ([\d.,]+) EGP: it's above its normal price now\.$/, "عادة $1 ج.م: سعره دلوقتي أعلى من العادي."],
    [/^([\d.,]+) EGP in another store\.$/, "$1 ج.م في متجر تاني."], [/^The 'discount' never moves: it's the normal price\.$/, "الخصم ثابت: ده سعره العادي."],
    [/^Its usual price \(([\d.,]+) EGP\)\.$/, "سعره المعتاد ($1 ج.م)."], [/^Not enough history yet to say more\.$/, "مفيش تاريخ كفاية لسه."],
    [/^Watch it to hear when it's back\.$/, "تابعه عشان تعرف أول ما يرجع."],
    // units
    [/\/ piece$/, "/ للقطعة"], [/\/ 100 ml$/, "/ 100 مل"], [/\/ 100 g$/, "/ 100 جم"], [/\/ L$/, "/ لتر"], [/\/ kg$/, "/ كيلو"],
    // times and the status line (inside longer text)
    [/(\d+) s ago/g, "من $1 ث"], [/(\d+) min ago/g, "من $1 د"], [/(\d+) h ago/g, "من $1 س"], [/(\d+) d ago/g, "من $1 يوم"],
    [/paused, back in ([\d]+) (s|min)/g, (m, n, u) => `متوقف، راجع بعد ${n} ${u === "s" ? "ث" : "د"}`],
    [/full sweep ([\d]+) (s|min)/g, (m, n, u) => `لفة كاملة ${n} ${u === "s" ? "ث" : "د"}`],
    [/ · first sweep/g, " · أول لفة"], [/ · watching/g, " · بيراقب"], [/ · changed /g, " · نزل "],
    [/price changes in the last hour/g, "تغيير سعر في آخر ساعة"], [/\bEGP\b/g, "ج.م"],
    [/talabat mart|Amazon\.eg|Jumia|Breadfast/g, m => STORE[m]],
  ];
  const SKIP = "[dir=auto],script,style,code,textarea,[data-noi18n],input,svg";
  const ATTRS = ["placeholder", "title", "aria-label"];
  const originals = new WeakMap();

  function tr(s) {
    if (lang !== "ar" || !s || !/[A-Za-z]/.test(s)) return s;
    const k = s.trim();
    if (EXACT[k]) return s.replace(k, EXACT[k]);
    let o = k;
    for (const [re, rep] of RX) {
      if (re.global) o = o.replace(re, rep);
      else if (re.test(o)) { o = o.replace(re, rep); if (!re.global) break; }
    }
    return o === k ? s : s.replace(k, o);
  }
  function textNode(n) {
    const p = n.parentElement;
    if (!p || p.closest(SKIP)) return;
    const before = n.nodeValue, after = tr(before);
    if (after !== before) { originals.set(n, before); n.nodeValue = after; }
  }
  function element(el) {
    if (el.closest && el.closest("[data-noi18n]")) return;
    for (const a of ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && /[A-Za-z]/.test(v) && !(a === "placeholder" && el.id === "q")) {
        const t = tr(v); if (t !== v) el.setAttribute(a, t);
      }
    }
  }
  function walk(root) {
    if (lang !== "ar") return;
    if (root.nodeType === 3) return textNode(root);
    if (root.nodeType !== 1) return;
    element(root);
    root.querySelectorAll("[placeholder],[title],[aria-label]").forEach(element);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
    nodes.forEach(textNode);
  }
  function setDir() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    const b = document.getElementById("langBtn");
    if (b) { b.textContent = lang === "ar" ? "EN" : "ع"; b.title = lang === "ar" ? "English" : "العربية"; }
    if (lang === "ar") {
      if (document.title === "Live Deals") document.title = "عروض لحظة بلحظة";
      const q = document.getElementById("q");
      if (q) q.placeholder = "جرّب: لبن أقل من 50 من طلبات · حفاضات أرخص للكيلو";
    }
  }
  const mo = new MutationObserver(list => {
    if (lang !== "ar") return;
    for (const m of list) {
      if (m.type === "characterData") textNode(m.target);
      else if (m.type === "attributes") element(m.target);
      else m.addedNodes.forEach(walk);
    }
  });
  function start() {
    setDir();
    walk(document.body);
    mo.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    const b = document.getElementById("langBtn");
    if (b) b.onclick = () => { try { localStorage.setItem(KEY, lang === "ar" ? "en" : "ar"); } catch {} location.reload(); };
  }
  window.I18N = { get lang() { return lang; }, tr };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
