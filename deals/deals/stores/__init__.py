"""One module per store. Each has STORE (its id) and collect(fetcher, ...) yielding Offers."""

from . import amazon_eg, breadfast, dream2000, jumia, kenzz, raya, talabat

STORES = {
    talabat.STORE: talabat,
    amazon_eg.STORE: amazon_eg,
    breadfast.STORE: breadfast,
    jumia.STORE: jumia,
    dream2000.STORE: dream2000,
    kenzz.STORE: kenzz,
    raya.STORE: raya,
}

LABELS = {
    talabat.STORE: "talabat mart",
    amazon_eg.STORE: "Amazon.eg",
    breadfast.STORE: "Breadfast",
    jumia.STORE: "Jumia",
    dream2000.STORE: "Dream 2000",
    kenzz.STORE: "Kenzz",
    raya.STORE: "Raya Shop",
}
