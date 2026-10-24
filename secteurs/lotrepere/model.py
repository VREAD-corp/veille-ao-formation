"""Normalised notice model, relevance rules and shared helpers."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

# --- Relevance rules (deterministic, documented on the "Méthode" page) -------
#
# Three niches. A notice is kept for a niche when, for a SERVICES contract (works and supplies are left out):
#   - one of its CPV codes starts with a niche prefix, or
#   - the buyer ticked one of the niche's BOAMP descriptors (unless every CPV code of the notice sits in a
#     family that is never that niche, see `exclude_cpv`), or
#   - a niche keyword appears in the title or a lot name, ONLY when the notice has no CPV code at all.
# The rules come from the volume study of 2026-10-13 (analyses/V4-30, niches.py) and were tuned on real titles.


@dataclass(frozen=True)
class Niche:
    slug: str
    label: str  # "Nettoyage et propreté"
    short: str  # "nettoyage" (in running text: « appels d'offres nettoyage »)
    cpv: tuple[str, ...]  # CPV prefixes
    descriptors: frozenset[str]  # BOAMP descriptor codes (field dc)
    keywords: re.Pattern
    boamp_search: tuple[str, ...]  # BOAMP full-text pre-filter words (server side, broad on purpose)
    exclude_cpv: tuple[str, ...] = ()  # descriptor-only notices whose CPV codes all start with these are dropped
    # Generic descriptors (BOAMP 331 « Surveillance » also covers network or structure monitoring): they only
    # count next to a niche CPV code or keyword, never alone.
    weak_descriptors: frozenset[str] = frozenset()


NICHES: dict[str, Niche] = {
    "nettoyage": Niche(
        slug="nettoyage", label="Nettoyage et propreté", short="nettoyage",
        # 90910 cleaning services, 90911 buildings and windows, 90914 car parks, 90919 offices and schools; not
        # 90913 tank cleaning, 90915 chimney sweeping, 90916-90918 equipment or bin cleaning.
        cpv=("90910", "90911", "90914", "90919"), descriptors=frozenset({"239"}),
        keywords=re.compile(r"\b(nettoyage (?:des|de|du) (?:locaux|bureaux|batiments?|vitres|surfaces vitrees)|"
                            r"entretien (?:des|de) locaux|nettoyage des vitres|vitrerie|proprete des locaux|"
                            r"prestations? de (?:nettoyage|menage)|menage des locaux)\b"),
        boamp_search=("nettoyage", "propreté des locaux"),
        # Street cleaning, snow clearing, refuse, sewers, sanitation of networks: not cleaning of premises.
        exclude_cpv=("9050", "9051", "9052", "9053", "9061", "9062", "9063", "9064", "9067", "9040", "9041",
                     "9042", "9047", "9048", "9092", "4523", "4524", "4525", "4526", "4545", "4441", "5000",
                     "5070", "5071", "5072", "5076"),
    ),
    "espaces-verts": Niche(
        slug="espaces-verts", label="Espaces verts", short="espaces verts",
        # 77211400 tree felling and 77211500 tree maintenance, not the rest of 7721 (logging, forestry).
        cpv=("7731", "7734", "772114", "772115"), descriptors=frozenset({"116"}),
        keywords=re.compile(r"\b(espaces? verts?|elagage|tonte|debroussaillage|entretien paysager|"
                            r"amenagements? paysagers?|taille (?:des|de) haies)\b"),
        boamp_search=("espaces verts", "élagage", "débroussaillage", "tonte"),
        # Works, engineering studies and forestry: not the upkeep of green spaces.
        exclude_cpv=("4511", "4523", "4524", "4525", "4526", "4545", "0311", "0341", "0345", "1600", "1610",
                     "3400", "3410", "71", "7931", "77211100", "7723"),
    ),
    "securite": Niche(
        slug="securite", label="Sécurité privée", short="sécurité privée",
        cpv=("7971",), descriptors=frozenset({"138", "331"}), weak_descriptors=frozenset({"331"}),
        keywords=re.compile(r"\b(gardiennage|surveillance humaine|agents? de (?:securite|surete)|"
                            r"securite incendie ssiap|ssiap|maitres?[- ]chiens?|telesurveillance|rondes? de surveillance)\b"),
        boamp_search=("gardiennage", "surveillance humaine", "agents de sécurité", "SSIAP", "télésurveillance"),
        # Alarm, video or access-control equipment, IT security and works: supplies or works, not guarding.
        exclude_cpv=("3500", "3512", "3170", "3171", "3223", "3232", "3233", "3234", "4531", "4534", "4800",
                     "4873", "4876", "7200", "7225", "7226", "5061", "5070", "5071", "5072", "5130", "5131",
                     "5134", "5032", "4522", "9070", "9073", "9040", "9048", "7963", "71", "6371", "3492",
                     "3872", "92", "7232", "7941", "9811"),
    ),
}
NICHE_SLUGS: tuple[str, ...] = tuple(NICHES)
NICHE_LABELS: dict[str, str] = {k: n.label for k, n in NICHES.items()}


def is_niche_cpv(code: str, niche: Niche) -> bool:
    return (code or "")[:8].startswith(niche.cpv)


def descriptor_only_false_positive(match: list[str], cpv: list[str], niche: Niche | None = None) -> bool:
    """True when the only reason is the BOAMP descriptor and every CPV code is outside the niche."""
    if list(match) != ["descripteur"] or not cpv:
        return False
    niches = [niche] if niche else list(NICHES.values())
    return all(all(c.startswith(n.exclude_cpv) for c in cpv) for n in niches)

CPV_LABELS: dict[str, str] = {
    "90910000": "Services de nettoyage",
    "90911000": "Nettoyage de logements, de bâtiments et de vitres",
    "90911100": "Nettoyage de logements",
    "90911200": "Nettoyage de bâtiments",
    "90911300": "Nettoyage de vitres",
    "90914000": "Nettoyage de parkings",
    "90915000": "Ramonage",
    "90916000": "Nettoyage de matériel téléphonique",
    "90917000": "Nettoyage de matériel de transport",
    "90918000": "Nettoyage de poubelles",
    "90919000": "Nettoyage de bureaux, d'écoles et d'équipements de bureau",
    "90919100": "Nettoyage d'équipements de bureau",
    "90919200": "Nettoyage de bureaux",
    "90919300": "Nettoyage d'écoles",
    "77310000": "Réalisation et entretien d'espaces verts",
    "77311000": "Entretien de jardins d'ornement ou d'agrément",
    "77312000": "Désherbage",
    "77313000": "Entretien de parcs",
    "77314000": "Entretien de terrains",
    "77314100": "Engazonnement",
    "77315000": "Ensemencement",
    "77320000": "Entretien de terrains de sport",
    "77340000": "Élagage des arbres et taille des haies",
    "77341000": "Élagage des arbres",
    "77342000": "Taille des haies",
    "77210000": "Services de débardage",
    "77211000": "Services liés au débardage",
    "77211400": "Abattage d'arbres",
    "77211500": "Entretien des arbres",
    "79710000": "Services de sécurité",
    "79711000": "Services de surveillance d'alarmes",
    "79713000": "Services de gardiennage",
    "79714000": "Services de surveillance",
    "79714100": "Services de dépistage",
    "79714110": "Services de recherche de fugitifs",
    "79715000": "Services de patrouille",
    "79716000": "Services de délivrance de badges d'identification",
}

def fold(text: str | None) -> str:
    """Lowercase, accent-free text for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w-])(?:\+33\s?|0)[1-9](?:[ .]?\d{2}){4}(?![\w-])")


def redact(text: str) -> str:
    """Remove e-mail addresses and French phone numbers (data minimisation)."""
    return PHONE_RE.sub("[…]", EMAIL_RE.sub("[…]", text or ""))


ENTITY_RE = re.compile(r"&(?:[a-zA-Z]{2,8}|#\d{1,6}|#x[0-9a-fA-F]{1,6});")


def unescape_entities(text: str) -> str:
    """Decode HTML entities, however many times the source escaped them. BOAMP sends some titles as
    « &amp;quot;module&amp;quot; » (escaped twice); the site escapes once when it renders, so the stored text
    must be plain. Stops after 4 rounds or as soon as nothing changes."""
    for _ in range(4):
        if not ENTITY_RE.search(text):
            break
        text = html.unescape(text)
    return text


def clean_text(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", unescape_entities(str(text))).strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:.-") + "…"
    return text


TEXT_FIELDS = ("title", "buyer", "description", "procedure", "family", "summary")


# --- Model -----------------------------------------------------------------


@dataclass
class Notice:
    """One call for tenders, normalised across sources."""

    id: str  # "boamp:26-97886" or "ted:755-2025"
    source: str  # "BOAMP" or "TED"
    source_id: str
    title: str
    buyer: str
    published: str  # YYYY-MM-DD
    deadline: str | None  # "YYYY-MM-DDTHH:MM" wall-clock as written in the notice (France)
    url: str
    procedure_id: str | None = None
    description: str = ""
    departments: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    cpv: list[str] = field(default_factory=list)
    procedure: str = ""
    family: str = ""
    contract_types: list[str] = field(default_factory=list)
    estimated_value: float | None = None
    status: str = "initial"  # initial | rectificatif | annule
    match: list[str] = field(default_factory=list)  # cpv | descripteur | mot-cle
    alt_urls: dict[str, str] = field(default_factory=dict)
    supersedes: list[str] = field(default_factory=list)  # source ids replaced by this notice
    summary: str = ""  # optional, filled by a summariser plug-in (off by default)
    lots: list[str] = field(default_factory=list)  # lot titles (max 12)
    updated: str = ""  # YYYY-MM-DD of the latest notice of the same procedure (set on merge)
    niches: list[str] = field(default_factory=list)  # niche slugs (NICHES) this notice was kept for
    descriptors: list[str] = field(default_factory=list)  # BOAMP descriptor codes (kept to re-apply the rules)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Notice":
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in data.items() if k in known}
        # Notices stored before clean_text() decoded entities still hold « &amp;quot; »: decode on load too.
        for k in TEXT_FIELDS:
            if isinstance(kw.get(k), str):
                kw[k] = unescape_entities(kw[k])
        if isinstance(kw.get("lots"), list):
            kw["lots"] = [unescape_entities(x) if isinstance(x, str) else x for x in kw["lots"]]
        return cls(**kw)

    # -- derived values ----------------------------------------------------

    @property
    def group_key(self) -> str:
        return f"proc:{self.procedure_id}" if self.procedure_id else self.id

    def deadline_dt(self) -> datetime | None:
        if not self.deadline:
            return None
        try:
            return datetime.fromisoformat(self.deadline).replace(tzinfo=PARIS)
        except ValueError:
            return None

    def is_open(self, now: datetime) -> bool:
        if self.status == "annule":
            return False
        dl = self.deadline_dt()
        return dl is not None and dl > now

    def days_left(self, now: datetime) -> int | None:
        dl = self.deadline_dt()
        if dl is None:
            return None
        return (dl.date() - now.astimezone(PARIS).date()).days

    def cpv_main_label(self) -> str:
        for code in self.cpv:
            c = code[:8]
            # exact code, else its nearest listed parent (90919210 -> 90919200)
            for k in (c, c[:6] + "00", c[:5] + "000", c[:4] + "0000"):
                if k in CPV_LABELS:
                    return CPV_LABELS[k]
        return ""

    def iso_week(self) -> str:
        y, w, _ = date.fromisoformat(self.published).isocalendar()
        return f"{y}-W{w:02d}"


def classify_niches(
    *,
    title: str,
    extra_text: str = "",
    cpv: list[str],
    descriptors: list[str] | None = None,
    contract_types: list[str] | None = None,
) -> dict[str, list[str]]:
    """Return {niche slug: reasons} for every niche the notice belongs to ({} = not relevant)."""
    types = {fold(t) for t in (contract_types or [])}
    if types and "services" not in types:
        # Works or supplies contract (a cleaning machine, CCTV cameras, landscaping works): not a service.
        return {}
    text = fold(f"{title} {extra_text}")
    out: dict[str, list[str]] = {}
    for slug, n in NICHES.items():
        reasons: list[str] = []
        if any(is_niche_cpv(c, n) for c in cpv):
            reasons.append("cpv")
        hit = n.descriptors.intersection(descriptors or ())
        if n.keywords.search(text):
            reasons.append("mot-cle")
        if hit and (hit - n.weak_descriptors or reasons):
            reasons.insert(len(reasons) - ("mot-cle" in reasons), "descripteur")
        if reasons == ["mot-cle"] and cpv:
            # The buyer classified the contract under other CPV codes: the keyword is incidental.
            continue
        if descriptor_only_false_positive(reasons, cpv, n):
            continue
        if reasons:
            out[slug] = reasons
    return out


def classify(**kw) -> list[str]:
    """Union of the reasons over every niche ([] = not relevant)."""
    found = classify_niches(**kw)
    return sorted({r for reasons in found.values() for r in reasons})


def iso_week_bounds(week: str) -> tuple[date, date]:
    """'2026-W41' -> (monday, next monday)."""
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return monday, monday + timedelta(days=7)
