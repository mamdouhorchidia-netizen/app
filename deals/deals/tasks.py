"""A store as a set of pages to check. Each Task fetches one page and returns (offers, follow-up tasks):
page 1 of a list discovers pages 2..n, a department discovers its sub-lists. The live scheduler revisits
tasks on its own timetable; run_once walks them all once, depth first, for a one-off collection."""

from dataclasses import dataclass, field
from typing import Callable, Iterator

from .models import Offer


@dataclass(eq=False)
class Task:
    key: str
    run: Callable[[], tuple[list[Offer], list["Task"]]]
    last_run: float = 0.0
    last_change: float = 0.0
    fails: int = field(default=0, repr=False)


def run_once(tasks: list[Task], max_pages: int = None) -> Iterator[Offer]:
    """Every task and follow-up once (each product once), stopping after max_pages requests."""
    queue, seen, keys, done = list(tasks), set(), {t.key for t in tasks}, 0
    while queue:
        if max_pages is not None and done >= max_pages:
            return
        task = queue.pop(0)
        offers, follow = task.run()
        done += 1
        new = [f for f in follow if f.key not in keys]
        keys.update(f.key for f in new)
        queue[:0] = new  # depth first: a list's next page before the next list
        for o in offers:
            if o.sku not in seen:
                seen.add(o.sku)
                yield o
