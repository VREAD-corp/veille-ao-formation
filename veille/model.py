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

CPV_LABELS: dict[str, str] = {
    "80000000": "Services d'enseignement et de formation",
    "80400000": "Éducation des adultes et autres enseignements",
    "80410000": "Services scolaires divers",
    "80411000": "Services d'auto-école",
    "80420000": "Enseignement par voie électronique",
    "80430000": "Enseignement universitaire pour adultes",
    "80500000": "Services de formation",
    "80510000": "Formation spécialisée",
    "80511000": "Formation du personnel",
    "80520000": "Installations de formation",
    "80521000": "Programmes de formation",
    "80522000": "Séminaires de formation",
    "80530000": "Formation professionnelle",
    "80531000": "Formation industrielle et technique",
    "80532000": "Formation en gestion",
    "80533000": "Formation à l'informatique",
    "80540000": "Formation non professionnelle",
    "80550000": "Formation à la sécurité",
    "80560000": "Formation santé et secourisme",
    "80561000": "Formation dans le domaine de la santé",
    "80562000": "Formation en secourisme",
    "80570000": "Formation en développement personnel",
    "80580000": "Cours de langues",
    "80590000": "Services de tutorat",
    "80600000": "Formation en matériel de défense et de sécurité",
    "79632000": "Formation du personnel",
    "79633000": "Perfectionnement du personnel",
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
