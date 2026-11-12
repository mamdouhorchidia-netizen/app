"""One module per store. Each has STORE (its id) and collect(fetcher, ...) yielding Offers."""

from . import amazon_eg, breadfast, talabat

STORES = {
    talabat.STORE: talabat,
    amazon_eg.STORE: amazon_eg,
    breadfast.STORE: breadfast,
}

LABELS = {
    talabat.STORE: "talabat mart",
    amazon_eg.STORE: "Amazon.eg",
    breadfast.STORE: "Breadfast",
}
