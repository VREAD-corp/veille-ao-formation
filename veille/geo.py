"""Departments, regions and NUTS codes (France, 2016 regions)."""

from __future__ import annotations

import re
import unicodedata

OUTRE_MER = "Outre-mer"

REGION_DEPARTMENTS: dict[str, tuple[str, ...]] = {
    "Auvergne-Rhône-Alpes": ("01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"),
    "Bourgogne-Franche-Comté": ("21", "25", "39", "58", "70", "71", "89", "90"),
    "Bretagne": ("22", "29", "35", "56"),
    "Centre-Val de Loire": ("18", "28", "36", "37", "41", "45"),
    "Corse": ("2A", "2B"),
    "Grand Est": ("08", "10", "51", "52", "54", "55", "57", "67", "68", "88"),
    "Hauts-de-France": ("02", "59", "60", "62", "80"),
    "Île-de-France": ("75", "77", "78", "91", "92", "93", "94", "95"),
    "Normandie": ("14", "27", "50", "61", "76"),
    "Nouvelle-Aquitaine": ("16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"),
    "Occitanie": ("09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"),
    "Pays de la Loire": ("44", "49", "53", "72", "85"),
    "Provence-Alpes-Côte d'Azur": ("04", "05", "06", "13", "83", "84"),
    OUTRE_MER: ("971", "972", "973", "974", "975", "976", "977", "978", "986", "987", "988"),
}

REGIONS: tuple[str, ...] = tuple(REGION_DEPARTMENTS)

DEPARTMENT_REGION: dict[str, str] = {
    dep: region for region, deps in REGION_DEPARTMENTS.items() for dep in deps
}

# NUTS 2021 level-1 codes for France map one-to-one onto regions,
# except FRC (Bourgogne + Franche-Comté, a single region) and FRY (overseas).
NUTS1_REGION: dict[str, str] = {
    "FR1": "Île-de-France",
    "FRB": "Centre-Val de Loire",
    "FRC": "Bourgogne-Franche-Comté",
    "FRD": "Normandie",
    "FRE": "Hauts-de-France",
    "FRF": "Grand Est",
    "FRG": "Pays de la Loire",
    "FRH": "Bretagne",
    "FRI": "Nouvelle-Aquitaine",
    "FRJ": "Occitanie",
    "FRK": "Auvergne-Rhône-Alpes",
    "FRL": "Provence-Alpes-Côte d'Azur",
    "FRM": "Corse",
    "FRY": OUTRE_MER,
}


def normalise_department(code: str | None) -> str | None:
    """Return a canonical department code ('01', '2A', '971') or None."""
    if not code:
        return None
    code = str(code).strip().upper()
    if code in DEPARTMENT_REGION:
        return code
    if code.isdigit() and len(code) == 1:
        code = "0" + code
        return code if code in DEPARTMENT_REGION else None
    if code == "20":
        return None  # Corse without A/B: region known, department not
    return None


def region_for_department(code: str | None) -> str | None:
    if code and str(code).strip() == "20":
        return "Corse"
    dep = normalise_department(code)
    return DEPARTMENT_REGION.get(dep) if dep else None


def department_from_postcode(postcode: str | None) -> str | None:
    """'75019' -> '75', '20200' -> '2B', '97400' -> '974'."""
    if not postcode:
        return None
    pc = re.sub(r"\s", "", str(postcode))
    if not re.fullmatch(r"\d{5}", pc):
        return None
    if pc.startswith("97") or pc.startswith("98"):
        return normalise_department(pc[:3])
    if pc.startswith("20"):
        return "2A" if int(pc) < 20200 else "2B"
    return normalise_department(pc[:2])


def region_for_nuts(code: str | None) -> str | None:
    """'FRK26' -> 'Auvergne-Rhône-Alpes'; country-level codes ('FRA', 'FR') -> None."""
    if not code:
        return None
    code = str(code).strip().upper()
    if not code.startswith("FR") or len(code) < 3 or code == "FRA":
        return None
    return NUTS1_REGION.get(code[:3])


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


REGION_SLUGS: dict[str, str] = {slugify(r): r for r in REGIONS}
