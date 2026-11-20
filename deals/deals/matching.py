"""The same product in different stores: same pack size, same first word (usually the brand), and most
of the other name words in common. Cautious on purpose: a missed match costs nothing, a wrong one
sends someone to the wrong product."""

import re
from collections import defaultdict

from .units import pack_size

# Container words (can, bottle, glass...) are kept: a can and a bottle of the same drink are different products.
_STOP = set("""a an and the of with for in on new pack packs piece pieces pcs pc x ml l lt ltr liter litre g gm gr
 kg free bundle offer artwork edition size flavor flavour flavored flavoured drink drinks soft""".split())
_SAME = {"cans": "can", "bottles": "bottle", "pet": "bottle", "plastic": "bottle", "tin": "can", "tins": "can"}
_VARIANT = set("""zero diet light lite sugarfree decaf decaffeinated max extra mini classic original plus
 vanilla chocolate strawberry mango orange apple guava lemon mint cherry berry coconut caramel hazelnut
 spicy hot sweet salted unsalted skimmed skim low full fat whole kids baby""".split())
_WORD = re.compile(r"[a-z؀-ۿ]+")


def words(name: str) -> list[str]:
    return [_SAME.get(w, w) for w in _WORD.findall(name.lower()) if w not in _STOP and len(w) > 1]


def similar(a: list[str], b: list[str]) -> bool:
    """Same first word, and one name's words contain the other's: "Pepsi Can" ~ "Pepsi Soft Can", but
    "Nescafe Latte" !~ "Nescafe Mocha" (each has a word the other lacks: a different variant)."""
    if not a or not b or a[0] != b[0]:
        return False
    sa, sb = set(a), set(b)
    if (sa - sb) and (sb - sa):
        return False
    if (sa ^ sb) & _VARIANT:  # "Coca-Cola Zero" is not "Coca-Cola"
        return False
    return len(sa & sb) / len(sa | sb) >= 0.5


def key(name: str, size=None):
    """(bucket, words): only names in the same bucket can be the same product. A bucket is the pack size and
    the first word, since similar() needs both to agree; None when the name can't match anything (no pack size,
    or no words). `size`: pack_size(name) when it's already known."""
    size = pack_size(name) if size is None else size
    if not size:
        return None, None
    w = words(name)
    return ((round(size[0], 3), size[1], w[0]) if w else None), w


def match_group(group: list) -> dict:
    """group: (id, store, words) of one bucket, in catalog order. Returns id -> ids of the same product in other
    stores, for the ids that have any. A bucket's answer depends on its own members only, so a catalog that keeps
    each bucket's answer needs to redo only the buckets whose members changed."""
    if len(group) < 2 or len({store for _, store, _ in group}) < 2:
        return {}
    pairs = defaultdict(list)
    for i, (a, sa, wa) in enumerate(group):
        for b, sb, wb in group[i + 1:]:
            if sa != sb and similar(wa, wb):
                pairs[a].append((b, sb))
                pairs[b].append((a, sa))
    # Keep a pair only when each is the other's single candidate in that store: a product that fits
    # several products of one store is ambiguous there.
    unique = {}
    for pid, found in pairs.items():
        per_store = defaultdict(list)
        for oid, store in found:
            per_store[store].append(oid)
        unique[pid] = [oids[0] for oids in per_store.values() if len(oids) == 1]
    out = {}
    for pid, oids in unique.items():
        for oid in oids:
            if pid in unique.get(oid, []):
                out.setdefault(pid, []).append(oid)
    return out


def match(items: list[dict]) -> dict[int, list[int]]:
    """items: dicts with id, store, name. Returns id -> ids of the same product in other stores.
    Pairs are only looked for inside a bucket (same pack size and first word), so one big pack size
    ("500 g") doesn't make it quadratic in the whole catalog."""
    buckets = defaultdict(list)
    for it in items:
        k, w = key(it["name"])
        if k:
            buckets[k].append((it["id"], it["store"], w))
    out = defaultdict(list)
    for group in buckets.values():
        out.update(match_group(group))
    return out
