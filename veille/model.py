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

# CPV prefixes counted as "training": 804 (adult education, e-learning, driving
# schools), 805 (training services), 806 (defence training), plus the two
# staff-training codes of division 79.
TRAINING_CPV_PREFIXES: tuple[str, ...] = ("804", "805", "806")
TRAINING_CPV_EXACT: frozenset[str] = frozenset({"80000000", "79632000", "79633000"})

# BOAMP's own subject descriptor "Formation".
BOAMP_TRAINING_DESCRIPTORS: frozenset[str] = frozenset({"410"})

# CPV divisions that are never a training service: 66 financial and insurance, 90 sewage, refuse, cleaning
# and environmental, 92 recreational, cultural and sporting services. A notice kept ONLY because the buyer
# ticked the "Formation" descriptor, whose CPV codes all sit in these divisions, is a false positive
# (e.g. « Gestion de l'animation enfance et jeunesse », CPV 92000000; « Service de courtage en assurances »).
UNRELATED_CPV_DIVISIONS: tuple[str, ...] = ("66", "90", "92")


def descriptor_only_false_positive(match: list[str], cpv: list[str]) -> bool:
    """True when the only reason is the BOAMP descriptor and every CPV code is outside training."""
    return list(match) == ["descripteur"] and bool(cpv) and all(c[:2] in UNRELATED_CPV_DIVISIONS for c in cpv)

# Keywords, matched on accent-free lowercase text, whole words only. A keyword
# alone only counts for service contracts (so "construction d'un centre de
# formation", a works contract, is left out).
KEYWORD_RE = re.compile(
    r"\b("
    r"formations?|formateurs?|e-?learning|elearning|"
    r"bilans? de competences|cours de (?:langues?|francais|anglais)|"
    r"habilitations? electriques?|caces|sauveteurs? secouristes? du travail|"
    r"accompagnement a la vae|validation des acquis"
    r")\b"
)

# Official French labels of the CPV 2008 (règlement (CE) n° 213/2008, file published by the EU Publications Office,
# downloaded 2026-10-17; the full list is data/cpv_fr.json), for the training codes.
CPV_LABELS: dict[str, str] = {
    "79632000": "Services de formation de personnel",
    "79633000": "Services de développement du personnel",
    "80000000": "Services d'enseignement et de formation",
    "80100000": "Services d'enseignement primaire",
    "80110000": "Services d'enseignement préscolaire",
    "80200000": "Services d'enseignement secondaire",
    "80210000": "Services d'enseignement secondaire technique et professionnel",
    "80211000": "Services d'enseignement secondaire technique",
    "80212000": "Services d'enseignement secondaire professionnel",
    "80300000": "Services d'enseignement supérieur",
    "80310000": "Services de formation pour la jeunesse",
    "80320000": "Services de formation médicale",
    "80330000": "Services de formation en matière de sécurité",
    "80340000": "Services d'enseignement spécialisé",
    "80400000": "Services d'éducation des adultes et autres services d'enseignement",
    "80410000": "Services scolaires divers",
    "80411000": "Services des auto-écoles",
    "80411100": "Services d'épreuves de conduite automobile",
    "80411200": "Cours de conduite",
    "80412000": "Services des écoles d'aviation",
    "80413000": "Services des écoles de voile",
    "80414000": "Services des écoles de plongée",
    "80415000": "Services des écoles de ski",
    "80420000": "Services d'enseignement par voie électronique",
    "80430000": "Services d'enseignement de niveau universitaire pour adultes",
    "80490000": "Exploitation d'un centre d'enseignement",
    "80500000": "Services de formation",
    "80510000": "Services de formation spécialisée",
    "80511000": "Services de formation du personnel",
    "80512000": "Services de dressage de chiens",
    "80513000": "Services d'écoles d'équitation",
    "80520000": "Installations de formation",
    "80521000": "Services relatifs aux programmes de formation",
    "80522000": "Séminaires de formation",
    "80530000": "Services de formation professionnelle",
    "80531000": "Services de formation industrielle et technique",
    "80531100": "Services de formation industrielle",
    "80531200": "Services de formation technique",
    "80532000": "Services de formation en gestion",
    "80533000": "Services de formation et de familiarisation dans le domaine de l'informatique",
    "80533100": "Services de formation informatique",
    "80533200": "Cours d'informatique",
    "80540000": "Services de formation dans le domaine de l'environnement",
    "80550000": "Services de formation dans le domaine de la sécurité",
    "80560000": "Services de formation dans le domaine de la santé et des premiers secours",
    "80561000": "Services de formation dans le domaine de la santé",
    "80562000": "Services de formation dans le domaine des premiers secours",
    "80570000": "Services de formation pour l'épanouissement de la personnalité",
    "80580000": "Fourniture de cours de langues",
    "80590000": "Services d'aide pédagogique",
    "80600000": "Services de formation dans le domaine des équipements militaires et de sécurité",
    "80610000": "Formation et simulation dans le domaine des équipements de sécurité",
    "80620000": "Formation et simulation dans le domaine des armes à feu et des munitions",
    "80630000": "Formation et simulation dans le domaine des véhicules militaires",
    "80640000": "Formation et simulation dans le domaine des navires de guerre",
    "80650000": "Formation et simulation dans le domaines des aéronefs, des missiles et des spationefs militaires",
    "80660000": "Formation et simulation dans le domaine des systèmes électroniques militaires",
}


def fold(text: str | None) -> str:
    """Lowercase, accent-free text for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def is_training_cpv(code: str) -> bool:
    code = (code or "")[:8]
    return code in TRAINING_CPV_EXACT or code.startswith(TRAINING_CPV_PREFIXES)


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
            if code[:8] in CPV_LABELS:
                return CPV_LABELS[code[:8]]
        return ""

    def iso_week(self) -> str:
        y, w, _ = date.fromisoformat(self.published).isocalendar()
        return f"{y}-W{w:02d}"


def classify(
    *,
    title: str,
    extra_text: str = "",
    cpv: list[str],
    descriptors: list[str] | None = None,
    contract_types: list[str] | None = None,
) -> list[str]:
    """Return why a notice is relevant ([] = not relevant)."""
    reasons: list[str] = []
    if any(is_training_cpv(c) for c in cpv):
        reasons.append("cpv")
    if descriptors and BOAMP_TRAINING_DESCRIPTORS.intersection(descriptors):
        reasons.append("descripteur")
    if KEYWORD_RE.search(fold(f"{title} {extra_text}")):
        reasons.append("mot-cle")
    if descriptor_only_false_positive(reasons, cpv):
        return []
    if reasons == ["mot-cle"] and cpv:
        # The buyer classified the contract under other CPV codes: a mention of
        # "formation" is then incidental (cleaning of a training centre, etc.).
        return []
    types = {fold(t) for t in (contract_types or [])}
    if reasons and types and "services" not in types:
        # Works or supplies contract (e.g. a machine sold with operator
        # training): not a training-services opportunity.
        return []
    return reasons


def iso_week_bounds(week: str) -> tuple[date, date]:
    """'2026-W41' -> (monday, next monday)."""
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return monday, monday + timedelta(days=7)
