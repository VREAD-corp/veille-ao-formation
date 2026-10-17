"""Figures of the data pages (region, theme, OPCO) and the automatic index rule.

Every number shown on those pages comes from here, computed from the stored notices at build time.

Index rule (seo plan §4.2), recomputed at every build, with hysteresis so that a page does not flap:
- index when published_12m >= 40 and the median daily open stock over the last 180 days >= 4;
- de-index (noindex, follow; out of the sitemap; the page stays reachable) when published_12m < 30 or median < 3;
- in between, keep the previous state.
The "previous state" is not stored: the rule is replayed day by day from RULE_START over the stored notices,
which gives the same answer at every build and needs no state file.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Iterable

from .model import Notice, fold

RULE_START = date(2026, 10, 17)  # first day the rule ran
INDEX_MIN_12M, INDEX_MIN_OPEN = 40, 4
DEINDEX_MAX_12M, DEINDEX_MAX_OPEN = 30, 3
OPEN_WINDOW_DAYS = 180
CLOSED_DAYS = 90


def counted(items: Iterable[Notice]) -> list[Notice]:
    """One record per procedure, cancelled ones left out."""
    return [t for t in items if t.status != "annule"]


def deadline_day(t: Notice) -> date | None:
    return date.fromisoformat(t.deadline[:10]) if t.deadline else None


class Timeline:
    """Daily open stock and publication dates of a set of notices."""

    def __init__(self, items: Iterable[Notice], first: date, last: date):
        self.first, self.last = first, last
        span = (last - first).days + 1
        diff = [0] * (span + 1)
        self.published: list[str] = []
        for t in counted(items):
            self.published.append(t.published)
            dl = deadline_day(t)
            if dl is None:
                continue
            # open on day d when published <= d < deadline day (a notice closing on day d is not counted that day)
            a = max((date.fromisoformat(t.published) - first).days, 0)
            b = min((dl - first).days, span)
            if a < b:
                diff[a] += 1
                diff[b] -= 1
        self.published.sort()
        self.open: list[int] = []
        run = 0
        for k in range(span):
            run += diff[k]
            self.open.append(run)

    def n12(self, day: date) -> int:
        lo = (day - timedelta(days=365)).isoformat()
        return bisect.bisect_right(self.published, day.isoformat()) - bisect.bisect_right(self.published, lo)

    def median_open(self, day: date, days: int = OPEN_WINDOW_DAYS) -> float:
        end = (day - self.first).days
        start = max(end - days + 1, 0)
        window = self.open[start:end + 1]
        return median(window) if window else 0


@dataclass
class IndexDecision:
    index: bool
    ever: bool  # passed the rule at least once since RULE_START
    n12: int
    median_open: float

    @property
    def robots(self) -> str | bool:
        return False if self.index else "noindex, follow"


def index_decision(items: Iterable[Notice], today: date, start: date = RULE_START) -> IndexDecision:
    items = list(items)
    start = min(start, today)
    first = start - timedelta(days=OPEN_WINDOW_DAYS)
    tl = Timeline(items, first, today)
    state = None
    ever = False
    d = start
    while d <= today:
        n, med = tl.n12(d), tl.median_open(d)
        passes = n >= INDEX_MIN_12M and med >= INDEX_MIN_OPEN
        fails = n < DEINDEX_MAX_12M or med < DEINDEX_MAX_OPEN
        if state is None:
            state = passes
        elif state and fails:
            state = False
        elif not state and passes:
            state = True
        ever = ever or state
        d += timedelta(days=1)
    return IndexDecision(bool(state), ever, tl.n12(today), tl.median_open(today))


# --- 12-month figures ------------------------------------------------------------------------------------

@dataclass
class Figures:
    since: str  # first day of the 12-month window (YYYY-MM-DD)
    n12: int
    months: list[tuple[str, int]]  # ("2025-11", count), oldest first, 12 full or partial months
    top_buyers: list[tuple[str, int]]
    median_days: int | None  # publication -> deadline, notices with a deadline
    with_lots: int
    framework: int  # accords-cadres (title or procedure mentions it)
    values: list[float] = field(default_factory=list)  # published estimated values

    @property
    def median_value(self) -> float | None:
        return median(self.values) if self.values else None


FRAMEWORK_RE = re.compile(r"\baccords?[- ]cadres?\b")


def buyer_key(name: str) -> str:
    """Group spelling variants of one buyer (« OPCO ATLAS » / « OPCO Atlas », « Cnfpt - … » / « CNFPT - … »)."""
    return re.sub(r"[\s-]+", " ", fold(name)).strip()


def last12(items: Iterable[Notice], today: date) -> list[Notice]:
    since = (today - timedelta(days=365)).isoformat()
    return [t for t in counted(items) if since < t.published <= today.isoformat()]


def figures(items: Iterable[Notice], today: date, top: int = 5) -> Figures:
    year = last12(items, today)
    since = (today - timedelta(days=364)).isoformat()
    months = []
    y, m = today.year, today.month
    keys = []
    for _ in range(12):
        keys.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    by_month = Counter(t.published[:7] for t in year)
    months = [(k, by_month.get(k, 0)) for k in reversed(keys)]
    names: dict[str, Counter] = {}
    counts: Counter = Counter()
    for t in year:
        k = buyer_key(t.buyer)
        counts[k] += 1
        names.setdefault(k, Counter())[t.buyer] += 1
    top_buyers = [(names[k].most_common(1)[0][0], n) for k, n in counts.most_common(top)]
    delays = sorted((deadline_day(t) - date.fromisoformat(t.published)).days for t in year
                    if t.deadline and deadline_day(t) > date.fromisoformat(t.published))
    framework = sum(1 for t in year if FRAMEWORK_RE.search(fold(t.title)))
    return Figures(
        since=since, n12=len(year), months=months, top_buyers=top_buyers,
        median_days=int(median(delays)) if delays else None,
        with_lots=sum(1 for t in year if t.lots), framework=framework,
        values=sorted(t.estimated_value for t in year if t.estimated_value),
    )


def closed_recently(items: Iterable[Notice], now: datetime, days: int = CLOSED_DAYS) -> list[Notice]:
    """Notices whose deadline passed in the last `days` days, latest deadline first (cancelled ones excluded)."""
    lo = now - timedelta(days=days)
    res = [t for t in counted(items) if (dl := t.deadline_dt()) and lo <= dl <= now]
    res.sort(key=lambda t: (t.deadline or "", t.id), reverse=True)
    return res
