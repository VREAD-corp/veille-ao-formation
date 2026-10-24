"""BOAMP (DILA) open data via the Opendatasoft Explore API v2.1.

Dataset: https://boamp-datadila.opendatasoft.com/explore/dataset/boamp/
Licence: Licence Ouverte / Open Licence (Etalab), as declared on
https://www.data.gouv.fr/datasets/boamp
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Iterable, Iterator

from .geo import normalise_department, region_for_department
from .http import HttpJson, http_json
from .model import NICHES, Notice, classify_niches, clean_text, redact

log = logging.getLogger(__name__)

API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
PAGE = 100
MAX_OFFSET = 9900  # Opendatasoft caps offset + limit at 10,000

# Broad server-side pre-filter (the three niches OR-ed); the exact rules are applied locally by
# classify_niches(). Opendatasoft full-text search stems words, which is why the local filter is needed.
def _candidates() -> str:
    parts: list[str] = []
    for n in NICHES.values():
        parts += [f'dc="{d}"' for d in sorted(n.descriptors)]
        parts += [f'search(objet,"{k}")' for k in n.boamp_search]
        parts += [f'donnees like "{p}*"' for p in n.cpv]
    return "(" + " or ".join(dict.fromkeys(parts)) + ")"


CANDIDATES = _candidates()
NOT_AWARD = 'nature!="ATTRIBUTION"'


def where_open() -> str:
    return f"datelimitereponse>now() and {NOT_AWARD} and {CANDIDATES}"


def where_published(start: date, end: date) -> str:
    """Notices published in [start, end)."""
    return (f"dateparution>=date'{start.isoformat()}' and dateparution<date'{end.isoformat()}' "
            f"and {NOT_AWARD} and {CANDIDATES}")


def fetch(where: str, *, get: HttpJson = http_json) -> Iterator[dict]:
    offset = 0
    while True:
        data = get(API, {"where": where, "limit": PAGE, "offset": offset,
                         "order_by": "dateparution desc,idweb"}, None)
        results = data.get("results", [])
        yield from results
        if len(results) < PAGE:
            return
        offset += PAGE
        if offset > MAX_OFFSET:
            log.warning("BOAMP: offset cap reached for %s", where)
            return


# --- donnees (notice body, JSON converted from XML) helpers -----------------


def _walk(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, path + (k,))
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, path)
    else:
        yield path, obj


def _text(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("#text", ""))
    if isinstance(v, list):
        return " ".join(_text(x) for x in v)
    return "" if v is None else str(v)


def _get(obj: Any, *keys: str) -> Any:
    for k in keys:
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def extract_cpv(donnees: dict) -> list[str]:
    codes: list[str] = []
    for path, value in _walk(donnees):
        if not path:
            continue
        key = path[-1]
        if key == "classPrincipale" or (len(path) >= 2 and path[-2] == "cbc:ItemClassificationCode"
                                         and key == "#text"):
            m = re.match(r"(\d{8})", str(value).strip())
            if m and m.group(1) not in codes:
                codes.append(m.group(1))
    return codes


def _eforms_deadlines(body: dict) -> list[str]:
    out: list[str] = []

    def visit(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "cac:TenderSubmissionDeadlinePeriod":
                    for p in v if isinstance(v, list) else [v]:
                        d = _text(p.get("cbc:EndDate")) if isinstance(p, dict) else ""
                        t = _text(p.get("cbc:EndTime")) if isinstance(p, dict) else ""
                        if re.match(r"\d{4}-\d{2}-\d{2}", d):
                            hhmm = t[:5] if re.match(r"\d{2}:\d{2}", t) else "23:59"
                            out.append(f"{d[:10]}T{hhmm}")
                else:
                    visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(body)
    return out


def extract_deadlines(donnees: dict) -> list[str]:
    """Deadlines as wall-clock 'YYYY-MM-DDTHH:MM', as written in the notice."""
    found = _eforms_deadlines(donnees)
    for path, value in _walk(donnees):
        if path and path[-1] in ("dateReceptionOffres", "receptionOffres", "dateLimiteReponse"):
            m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", str(value))
            if m:
                found.append(f"{m.group(1)}T{m.group(2) or '23:59'}")
    return sorted(set(found))


def pick_deadline(deadlines: list[str], today: str) -> str | None:
    """Earliest deadline not yet past (lot deadlines can differ), else the latest."""
    if not deadlines:
        return None
    upcoming = [d for d in deadlines if d[:10] >= today]
    return min(upcoming) if upcoming else max(deadlines)


def extract_description(donnees: dict) -> str:
    candidates = [
        ("EFORMS", "ContractNotice", "cac:ProcurementProject", "cbc:Description"),
        ("EFORMS", "PriorInformationNotice", "cac:ProcurementProject", "cbc:Description"),
        ("FNSimple", "initial", "natureMarche", "description"),
        ("MAPA", "initial", "description", "objet"),
        ("DSP", "initial", "descriptionMarche", "objet"),
        ("DIVERS", "initial", "description", "objet"),
    ]
    for keys in candidates:
        v = _get(donnees, *keys)
        if v:
            return _text(v)
    return ""


def _is_lot_name(path: tuple[str, ...]) -> bool:
    """eForms lot title: ProcurementProjectLot/ProcurementProject/Name."""
    p = path[:-1] if path and path[-1] == "#text" else path
    return p[-3:] == ("cac:ProcurementProjectLot", "cac:ProcurementProject", "cbc:Name")


def extract_lots(donnees: dict) -> list[str]:
    """Lot titles (eForms lot names, older-format lot descriptions)."""
    lots: list[str] = []
    for path, value in _walk(donnees):
        if _is_lot_name(path):
            lots.append(clean_text(value, 160))
        elif len(path) >= 2 and path[-2:] == ("lot", "description"):
            lots.append(clean_text(value, 160))
    lots = list(dict.fromkeys(x for x in lots if x))
    return lots if len(lots) > 1 else []


def extract_lot_text(donnees: dict) -> str:
    parts: list[str] = []
    for path, value in _walk(donnees):
        if _is_lot_name(path):
            parts.append(str(value))
        elif len(path) >= 2 and path[-2:] == ("lot", "description"):
            parts.append(str(value))
        elif len(path) >= 2 and path[-1] == "intitule":
            parts.append(str(value))
    return " ".join(parts)


def extract_value(donnees: dict) -> float | None:
    for keys in [
        ("EFORMS", "ContractNotice", "cac:ProcurementProject", "cac:RequestedTenderTotal",
         "cbc:EstimatedOverallContractAmount"),
        ("FNSimple", "initial", "natureMarche", "valeurEstimee", "valeur"),
    ]:
        v = _text(_get(donnees, *keys))
        try:
            if v and float(v) > 0:
                return float(v)
        except ValueError:
            pass
    return None


STATUS = {"INITIAL": "initial", "RECTIFICATIF": "rectificatif", "ANNULATION": "annule"}


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def normalise(rec: dict, *, today: str | None = None) -> Notice | None:
    """BOAMP record -> Notice, or None if it belongs to none of the niches."""
    today = today or date.today().isoformat()
    try:
        donnees = json.loads(rec.get("donnees") or "{}")
    except json.JSONDecodeError:
        donnees = {}
    cpv = extract_cpv(donnees)
    title = clean_text(rec.get("objet"))
    description = extract_description(donnees)
    contract_types = _as_list(rec.get("type_marche"))
    descriptors = _as_list(rec.get("dc") or rec.get("descripteur_code"))
    found = classify_niches(
        title=title,
        extra_text=extract_lot_text(donnees),
        cpv=cpv,
        descriptors=descriptors,
        contract_types=contract_types,
    )
    if not found:
        return None
    reasons = sorted({r for rs in found.values() for r in rs})
    deadline = pick_deadline(extract_deadlines(donnees), today)
    if not deadline and rec.get("datelimitereponse"):
        deadline = str(rec["datelimitereponse"])[:16]
    deps = [d for d in (normalise_department(x) for x in _as_list(rec.get("code_departement"))) if d]
    regions = sorted({r for r in (region_for_department(x) for x in _as_list(rec.get("code_departement"))) if r})
    idweb = str(rec.get("idweb"))
    return Notice(
        id=f"boamp:{idweb}",
        source="BOAMP",
        source_id=idweb,
        title=title,
        buyer=clean_text(rec.get("nomacheteur")),
        published=str(rec.get("dateparution"))[:10],
        deadline=deadline,
        url=rec.get("url_avis") or f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}",
        procedure_id=rec.get("contractfolderid") or None,
        description=clean_text(redact(description), 420) if fold_differs(description, title) else "",
        departments=sorted(set(deps)),
        regions=regions,
        cpv=cpv,
        procedure=clean_text(rec.get("procedure_libelle")),
        family=clean_text(rec.get("famille_libelle")),
        contract_types=[t.capitalize() for t in contract_types],
        estimated_value=extract_value(donnees),
        status=STATUS.get(str(rec.get("etat")), "initial"),
        match=reasons,
        niches=list(found),
        descriptors=descriptors,
        supersedes=[f"boamp:{x}" for x in _as_list(rec.get("annonce_lie"))],
        lots=extract_lots(donnees)[:12],
    )


def fold_differs(a: str, b: str) -> bool:
    return bool(a) and re.sub(r"\W+", "", a.lower()) != re.sub(r"\W+", "", b.lower())


def normalise_all(records: Iterable[dict], today: str | None = None) -> list[Notice]:
    out = []
    for rec in records:
        try:
            n = normalise(rec, today=today)
        except Exception as exc:  # one malformed notice must not break the run
            log.warning("BOAMP %s skipped: %s", rec.get("idweb"), exc)
            continue
        if n:
            out.append(n)
    return out
