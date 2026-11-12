"""Pack sizes from product names, for price per kg / litre / piece.

  "Coca Cola Soft Drink Pet Bottle, 1.45L x2"      -> 2.9 L
  "Suntop Mango Fruit Drink 250 ml X3"             -> 0.75 L
  "V7 Vitamin Sparkling Drinks, 6x300ml"           -> 1.8 L
  "Lipton Black Tea Soft , 500 gm"                 -> 0.5 kg
  "Waterkind Acai Drink Mix (10 Sachets), 60g"     -> 0.06 kg
  "جهينة لبن ١ لتر"                                -> 1 L
Returns None when the name doesn't say.
"""

import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
_UNITS = {
    "kg": ("kg", 1), "kilo": ("kg", 1), "kgs": ("kg", 1), "كيلو": ("kg", 1), "كجم": ("kg", 1),
    "g": ("kg", 0.001), "gm": ("kg", 0.001), "gr": ("kg", 0.001), "grams": ("kg", 0.001), "gram": ("kg", 0.001),
    "gms": ("kg", 0.001), "جم": ("kg", 0.001), "جرام": ("kg", 0.001),
    "l": ("L", 1), "lt": ("L", 1), "ltr": ("L", 1), "liter": ("L", 1), "litre": ("L", 1), "liters": ("L", 1),
    "litres": ("L", 1), "لتر": ("L", 1),
    "ml": ("L", 0.001), "مل": ("L", 0.001), "cl": ("L", 0.01),
}
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(kgs?|kilo|grams?|gms?|gr|g|ltrs?|lt|liters?|litres?|l|ml|cl|كيلو|كجم|جرام|جم|لتر|مل)"
_SIZE = re.compile(_NUM + r"\s*" + _UNIT + r"(?![a-z؀-ۿ])", re.I)
_COUNT_BEFORE = re.compile(r"(\d+)\s*[x×]\s*" + _NUM + r"\s*" + _UNIT + r"(?![a-z])", re.I)
_COUNT_AFTER = re.compile(r"[x×]\s*(\d+)(?!\s*(?:[.,]\d)|\d)", re.I)
_PACK = re.compile(r"(?:pack of|pcs?\s*of|عدد)\s*(\d+)|(\d+)\s*(?:pcs|pieces|piece|pack|packs|bottles|cans|"
                   r"diapers|nappies|wipes|rolls|tablets|capsules|sachets|bags|قطع|قطعة|حفاضة|كيس)\b", re.I)
_MULTIPACK = re.compile(r"(\d+)\s*(?:pcs|pieces|piece|pack|packs|bottles|cans|قطع|قطعة)\b", re.I)  # N of the size
_RANGE = re.compile(r"\d+(?:[.,]\d+)?\s*-\s*" + _NUM + r"\s*" + _UNIT, re.I)  # "9-14kg": a weight range, not a size
_FREE = re.compile(r"(\d+)\s*\+\s*(\d+)\s*(?:free|مجان)", re.I)


def pack_size(name: str):
    """(amount, unit) with unit "kg", "L" or "pc"; None when unknown."""
    s = name.translate(_DIGITS).replace(",", " , ")
    s = _RANGE.sub(" ", s)
    m = _COUNT_BEFORE.search(s)
    if m:
        count, qty, unit = int(m.group(1)), float(m.group(2).replace(",", ".")), m.group(3).lower()
    else:
        sizes = _SIZE.findall(s)
        if not sizes:
            p = _PACK.search(s)
            n = int(p.group(1) or p.group(2)) if p else 0
            return (float(n), "pc") if n > 1 else None
        qty, unit = float(sizes[-1][0].replace(",", ".")), sizes[-1][1].lower()
        c = _COUNT_AFTER.search(s)
        count = int(c.group(1)) if c else 1
        if count == 1:
            mp = _MULTIPACK.search(s)
            count = int(mp.group(1)) if mp else 1
    free = _FREE.search(s)
    if free:  # "x3 + 1 free" -> 4; "250ml, 2 + 1 free" -> 3
        paid, extra = int(free.group(1)), int(free.group(2))
        if count in (1, paid):
            count = paid + extra
    base, factor = _UNITS.get(unit, (None, None))
    if not base or qty <= 0 or not 1 <= count <= 200:
        return None
    return round(qty * factor * count, 4), base


def unit_price(price: float, name: str):
    """(price per unit, unit label) e.g. (41.38, "L"); None when the pack size is unknown."""
    size = pack_size(name)
    if not size or size[0] <= 0:
        return None
    return round(price / size[0], 2), size[1]
