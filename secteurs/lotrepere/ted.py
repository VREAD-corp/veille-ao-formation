"""TED (Tenders Electronic Daily) search API v3, no authentication needed.

Docs: https://docs.ted.europa.eu/api/latest/search.html
Reuse: notices published in the Supplement to the OJ EU can be freely reused
(Commission Decision 2011/833/EU); TED logos must not be used.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterable, Iterator

from .geo import department_from_postcode, region_for_department, region_for_nuts
from .http import HttpJson, http_json
from .model import NICHES, Notice, classify_niches, clean_text, redact

log = logging.getLogger(__name__)

API = "https://api.ted.europa.eu/v3/notices/search"
PAGE = 100
MAX_PAGES = 30

# Calls for competition only (no award notices).
NOTICE_TYPES = "cn-standard cn-social cn-desg pin-cfc-standard pin-cfc-social"
# classification-cpv matches the CPV hierarchy (90910000 covers 9091xxxx). Union of the niche prefixes.
CPV_QUERY = "classification-cpv IN (" + " ".join(
    dict.fromkeys((p + "0" * 8)[:8] for n in NICHES.values() for p in n.cpv)) + ")"

FIELDS = [
    "publication-number", "notice-type", "notice-title", "title-proc", "description-proc",
    "buyer-name", "organisation-post-code-buyer", "publication-date",
    "deadline-receipt-tender-date-lot", "deadline-receipt-tender-time-lot",
    "classification-cpv", "place-of-performance", "procedure-identifier",
    "contract-nature", "estimated-value-proc", "estimated-value-cur-proc", "title-lot",
    "procedure-type",
]


PROCEDURES = {
    "open": "Procédure ouverte",
    "restricted": "Procédure restreinte",
    "neg-w-call": "Procédure négociée avec mise en concurrence",
    "neg-wo-call": "Procédure négociée sans mise en concurrence",
    "comp-dial": "Dialogue compétitif",
    "innovation": "Partenariat d'innovation",
    "oth-single": "Autre procédure (une étape)",
    "oth-mult": "Autre procédure (plusieurs étapes)",
}


def query_open(today: date) -> str:
    return (f"buyer-country=FRA AND {CPV_QUERY} AND notice-type IN ({NOTICE_TYPES}) "
            f"AND deadline-receipt-tender-date-lot>={today:%Y%m%d}")


def query_published(start: date, end: date) -> str:
    """Notices published in [start, end)."""
    last = date.fromordinal(end.toordinal() - 1)
    return (f"buyer-country=FRA AND {CPV_QUERY} AND notice-type IN ({NOTICE_TYPES}) "
            f"AND publication-date>={start:%Y%m%d} AND publication-date<={last:%Y%m%d}")


def fetch(query: str, *, get: HttpJson = http_json) -> Iterator[dict]:
    for page in range(1, MAX_PAGES + 1):
        data = get(API, None, {"query": query, "fields": FIELDS, "limit": PAGE, "page": page,
                               "paginationMode": "PAGE_NUMBER", "scope": "ALL"})
        notices = data.get("notices", [])
        yield from notices
        if len(notices) < PAGE:
            return
    log.warning("TED: page cap reached for %s", query)


def _lang(value, prefer=("fra", "mul", "eng")) -> list[str]:
    """TED multilingual field -> list of strings in the preferred language."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        for lang in prefer:
            if lang in value:
                v = value[lang]
                return v if isinstance(v, list) else [v]
        for v in value.values():
            return v if isinstance(v, list) else [v]
    return []


def _first(value) -> str:
    vals = _lang(value)
    return str(vals[0]) if vals else ""


def _uniq(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        if i and i not in out:
            out.append(i)
    return out


def ted_deadlines(n: dict) -> list[str]:
    dates = n.get("deadline-receipt-tender-date-lot") or []
    times = n.get("deadline-receipt-tender-time-lot") or []
    out = []
    for i, d in enumerate(dates):
        if not re.match(r"\d{4}-\d{2}-\d{2}", str(d)):
            continue
        t = str(times[i]) if i < len(times) else (str(times[0]) if times else "")
        hhmm = t[:5] if re.match(r"\d{2}:\d{2}", t) else "23:59"
        out.append(f"{str(d)[:10]}T{hhmm}")
    return sorted(set(out))


def normalise(n: dict, *, today: str | None = None) -> Notice | None:
    from .boamp import pick_deadline  # shared rule

    today = today or date.today().isoformat()
    pub = str(n.get("publication-number"))
    cpv = _uniq(re.sub(r"\D", "", str(c))[:8] for c in (n.get("classification-cpv") or []))
    title = _first(n.get("title-proc")) or re.sub(r"^.*? – .*? – ", "", _first(n.get("notice-title")))
    lots = " ".join(_lang(n.get("title-lot")))
    types = _uniq(str(t) for t in (n.get("contract-nature") or []))
    found = classify_niches(title=title, extra_text=lots, cpv=cpv, contract_types=types)
    if not found:
        return None
    reasons = sorted({r for rs in found.values() for r in rs})

    regions = _uniq(r for r in (region_for_nuts(c) for c in (n.get("place-of-performance") or [])) if r)
    departments: list[str] = []
    if not regions:
        for pc in n.get("organisation-post-code-buyer") or []:
            dep = department_from_postcode(pc)
            if dep:
                departments.append(dep)
                regions.append(region_for_department(dep) or "")
                break
    value = None
    try:
        if float(n.get("estimated-value-proc") or 0) > 0 and (n.get("estimated-value-cur-proc") or "EUR") == "EUR":
            value = float(n["estimated-value-proc"])
    except (TypeError, ValueError):
        value = None
    buyers = _uniq(_lang(n.get("buyer-name")))
    lot_titles = _uniq(clean_text(x, 160) for x in _lang(n.get("title-lot")))[:12]
    return Notice(
        id=f"ted:{pub}",
        source="TED",
        source_id=pub,
        title=clean_text(title),
        buyer=clean_text(", ".join(buyers[:2])),
        published=str(n.get("publication-date"))[:10],
        deadline=pick_deadline(ted_deadlines(n), today),
        url=f"https://ted.europa.eu/fr/notice/-/detail/{pub}",
        procedure_id=n.get("procedure-identifier") or None,
        description=clean_text(redact(_first(n.get("description-proc"))), 420),
        departments=departments,
        regions=sorted(r for r in regions if r),
        cpv=cpv,
        procedure=PROCEDURES.get(str(n.get("procedure-type") or ""), ""),
        family="Marchés européens (TED)",
        contract_types=[t.capitalize() for t in types],
        estimated_value=value,
        status="initial",
        match=reasons,
        niches=list(found),
        lots=lot_titles if len(lot_titles) > 1 else [],
    )


def normalise_all(items: Iterable[dict], today: str | None = None) -> list[Notice]:
    out = []
    for n in items:
        try:
            x = normalise(n, today=today)
        except Exception as exc:
            log.warning("TED %s skipped: %s", n.get("publication-number"), exc)
            continue
        if x:
            out.append(x)
    return out
