"""Supply side: Qualiopi-certified training providers per region (data/of_qualiopi.json).

Source: « Liste publique des organismes de formation (L.6351-7-1 du Code du travail) », data.gouv.fr dataset
582c8978c751df788ec0bb7e, Licence Ouverte (Etalab). The CSV (about 36 MB, 150 000 rows) is streamed and only the
counts per region are kept, so the repo never stores it. `python -m veille qualiopi` refreshes the file when it is
older than --max-age-days (the update workflow runs it twice a day, so it really runs about once a month).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
import urllib.request
from datetime import date
from pathlib import Path

from .geo import OUTRE_MER, region_for_department, department_from_postcode

log = logging.getLogger("veille.supply")

DATASET_URL = "https://www.data.gouv.fr/datasets/582c8978c751df788ec0bb7e/"
CSV_URL = "https://www.monactiviteformation.emploi.gouv.fr/mon-activite-formation/public/listePubliqueOF?format=csv"
MIN_ROWS = 100_000  # sanity check: a truncated download (the server resets connections) must not replace the file

# INSEE region codes (COG 2016) used by the list's « codeRegion » column.
INSEE_REGIONS: dict[str, str] = {
    "01": OUTRE_MER, "02": OUTRE_MER, "03": OUTRE_MER, "04": OUTRE_MER, "06": OUTRE_MER,
    "905": OUTRE_MER,  # Saint-Pierre-et-Miquelon (postcode 97500)
    "11": "Île-de-France", "24": "Centre-Val de Loire", "27": "Bourgogne-Franche-Comté", "28": "Normandie",
    "32": "Hauts-de-France", "44": "Grand Est", "52": "Pays de la Loire", "53": "Bretagne",
    "75": "Nouvelle-Aquitaine", "76": "Occitanie", "84": "Auvergne-Rhône-Alpes",
    "93": "Provence-Alpes-Côte d'Azur", "94": "Corse",
}

# Overseas territories, by INSEE region code, for the sections of the « Outre-mer » page.
INSEE_TERRITORIES: dict[str, str] = {"01": "Guadeloupe", "02": "Martinique", "03": "Guyane", "04": "La Réunion",
                                     "06": "Mayotte", "905": "Saint-Pierre-et-Miquelon"}

COL_REGION = "adressePhysiqueOrganismeFormation.codeRegion"
COL_POSTCODE = "adressePhysiqueOrganismeFormation.codePostal"
COL_QUALIOPI = "certifications.actionsDeFormation"


def aggregate(lines, extracted: str) -> dict:
    """Count declared providers and providers certified « actions de formation », per region."""
    reader = csv.DictReader(lines, delimiter=";")
    regions: dict[str, dict[str, int]] = {}
    territories: dict[str, dict[str, int]] = {}
    rows = certified = unplaced = 0
    for row in reader:
        rows += 1
        region = INSEE_REGIONS.get((row.get(COL_REGION) or "").strip())
        if not region:
            region = region_for_department(department_from_postcode(row.get(COL_POSTCODE)))
        is_cert = (row.get(COL_QUALIOPI) or "").strip().lower() == "true"
        certified += is_cert
        if not region:
            unplaced += 1
            continue
        r = regions.setdefault(region, {"declared": 0, "qualiopi": 0})
        r["declared"] += 1
        r["qualiopi"] += is_cert
        territory = INSEE_TERRITORIES.get((row.get(COL_REGION) or "").strip())
        if territory:
            tr = territories.setdefault(territory, {"declared": 0, "qualiopi": 0})
            tr["declared"] += 1
            tr["qualiopi"] += is_cert
    return {
        "source": "Liste publique des organismes de formation (L.6351-7-1 du Code du travail)",
        "dataset": DATASET_URL,
        "licence": "Licence Ouverte / Open Licence (Etalab)",
        "extracted": extracted,
        "rows": rows,
        "qualiopi_total": certified,
        "unplaced": unplaced,
        "regions": dict(sorted(regions.items())),
        "territories": dict(sorted(territories.items())),
    }


def download(url: str = CSV_URL, attempts: int = 5, timeout: int = 120) -> str:
    """The server often resets the connection mid-file: retry and accept only a complete-looking file."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "veille-ao-formation (+https://ao-formation.jalonel.com/methode/)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                expected = resp.headers.get("Content-Length")
            if expected and len(data) != int(expected):
                raise IOError(f"truncated: {len(data)} of {expected} bytes")
            text = data.decode("utf-8-sig")
            if text.count("\n") < MIN_ROWS:
                raise IOError(f"only {text.count(chr(10))} lines")
            return text
        except Exception as exc:  # network errors, truncation
            last = exc
            log.warning("OF list download %d/%d failed: %s", i + 1, attempts, exc)
            time.sleep(5 * (i + 1))
    raise IOError(f"OF list download failed: {last}")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def refresh(path: Path, today: date, max_age_days: int = 25, fetch=download) -> bool:
    """Re-aggregate the list if the stored counts are older than max_age_days. Returns True if rewritten."""
    old = load(path)
    if old.get("extracted") and (today - date.fromisoformat(old["extracted"])).days < max_age_days:
        return False
    agg = aggregate(io.StringIO(fetch()), today.isoformat())
    if agg["rows"] < MIN_ROWS:
        raise IOError(f"OF list has only {agg['rows']} rows")
    path.write_text(json.dumps(agg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True
