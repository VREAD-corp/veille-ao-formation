"""Themes and buyer groups of the programmatic pages (/theme/<slug>/, /acheteur/opco/, the France Travail guide).

Themes are matched on the title and the lot names only (never the buyer or the free description: « santé et
sécurité au travail » in a description, or a CHU buyer, are what made a first regex 60 % precise). Rules are
deterministic regexes on accent-free lowercase text (model.fold). Their measured precision is in
docs/evaluation-themes.md; a theme page is published only if its precision passed the gate (THEMES[slug].published).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Notice, fold


@dataclass(frozen=True)
class Theme:
    slug: str
    label: str  # « sécurité, SST et incendie »
    h1: str  # page H1 suffix: « Appels d'offres de formation {h1} »
    include: str  # regex on fold(title + lots)
    exclude: str = ""  # regex removing matches (applied to the same text, per segment)
    exclude_buyer: str = ""  # regex on fold(buyer): the theme never applies to these buyers
    drop: str = ""  # regex: a segment (title or lot) that matches it is ignored for this theme
    published: bool = True  # False when the precision gate failed: no page, no link
    keyword: str = ""  # suggested alert keyword
    _inc: re.Pattern = field(init=False, repr=False, compare=False)
    _exc: re.Pattern | None = field(init=False, repr=False, compare=False)
    _exb: re.Pattern | None = field(init=False, repr=False, compare=False)
    _drop: re.Pattern | None = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_inc", re.compile(self.include))
        object.__setattr__(self, "_exc", re.compile(self.exclude) if self.exclude else None)
        object.__setattr__(self, "_exb", re.compile(self.exclude_buyer) if self.exclude_buyer else None)
        object.__setattr__(self, "_drop", re.compile(self.drop) if self.drop else None)

    def matches_text(self, text: str) -> bool:
        """True if one segment (the title or one lot name) matches the theme."""
        for seg in segments(text):
            if self._drop is not None and self._drop.search(seg):
                continue
            if self._exc is not None:
                seg = self._exc.sub(" ", seg)
            if self._inc.search(seg):
                return True
        return False

    @property
    def label_cap(self) -> str:
        return self.label[:1].upper() + self.label[1:]

    def matches(self, t: Notice) -> bool:
        if self._exb is not None and self._exb.search(fold(t.buyer)):
            return False
        return self.matches_text(theme_text(t))


SEP = "\n"


def theme_text(t: Notice) -> str:
    return SEP.join([t.title, *t.lots])


TAG_RE = re.compile(r"<[^>]{0,40}>")  # BOAMP lot names carry « <br/> », which folds to the word « br »
APOS_RE = re.compile(r"[\u2019\u2018\u02bc\u00b4`]")  # fold() would drop « ’ »: « l’intelligence » -> « lintelligence »
# Segments that describe something other than a training (works design, consulting, software development): never a theme.
NOT_TRAINING_RE = re.compile(r"\b(?:maitrise d'oeuvre|assistance a (?:la )?maitrise d'ouvrage|developpement d'un outil|"
                             r"verification et (?:l')?entretien)\b")


def segments(text: str) -> list[str]:
    out = []
    for s in text.split(SEP):
        s = fold(APOS_RE.sub("'", TAG_RE.sub(" ", s)))
        if s and not NOT_TRAINING_RE.search(s):
            out.append(f" {s} ")
    return out


# Word boundary helpers (text is folded: lowercase, no accents).
def w(*alts: str) -> str:
    return r"\b(?:" + "|".join(alts) + r")\b"


THEMES: dict[str, Theme] = {t.slug: t for t in [
    Theme(
        "securite-sst-incendie", "sécurité au travail, SST et incendie", "sécurité, SST et incendie",
        include=w(
            r"sst", r"sauveteurs?[ -]secouristes?", r"secourisme", r"premiers? secours", r"psc ?1", r"pse ?[12]",
            r"gestes qui sauvent", r"incendies?", r"extincteurs?", r"evacuation", r"equipiers? de premiere intervention",
            r"ssiap", r"habilitations? electriques?", r"habilitations? (?:b0|h0|bs|br|be|b1v?|b2v?|bc|hc)\w*",
            r"b0[- ]?h0v?", r"psc", r"prevention et secours civiques",
            r"prap", r"gestes et postures", r"travail en hauteur", r"port du harnais", r"echafaudages?", r"echafauder",
            r"amiante", r"sous[- ]section 4", r"ss4", r"aipr", r"atex", r"risques? chimiques?",
            r"prevention des risques(?: professionnels)?", r"sante,? (?:et )?securite au travail", r"securite au travail",
            r"hygiene,? (?:sante,? )?(?:et )?securite", r"document unique", r"permis feu", r"defibrillateurs?",
            r"risques? routiers?", r"espaces? confines?", r"pre-?catec", r"catec", r"risques? psychosociaux", r"rps",
            r"radioprotection des travailleurs", r"securite (?:et|en) prevention", r"prevention et securite",
            r"formations? (?:obligatoires|reglementaires) (?:de|en) securite", r"formations? securite",
            r"sante,? securite et conditions de travail", r"f3sct", r"chsct", r"assistants? de prevention",
            r"conseillers? de prevention",
        ),
        exclude=w(r"cyber ?securite", r"securite (?:informatique|des systemes|numerique|alimentaire|sanitaire|des donnees)",
                  r"premiers secours en sante mentale", r"prevention des risques (?:cyber\w*|numeriques?|informatiques?)",
                  # the buyer's own name in the title (« … du service départemental d'incendie et de secours »)
                  r"services? departementa(?:l|ux) d'incendie et de secours", r"d'incendie et de secours"),
        keyword="SST",
    ),
    Theme(
        "conduite-caces-permis", "conduite : CACES, engins et permis", "conduite : CACES et permis",
        include=w(
            r"caces", r"r ?4(?:8[2-9]|90)", r"chariots? (?:automoteurs?|elevateurs?)", r"nacelles?", r"pemp",
            r"engins? de chantier", r"grues?", r"ponts? roulants?", r"autorisations? de conduite",
            r"permis", r"auto[- ]?ecoles?", r"ecoles? de conduite", r"code de la route", r"fimo", r"fco",
            r"conduite (?:de vehicules?|des vehicules?|d'engins|des engins|poids lourds?|en securite|preventive|"
            r"defensive|sur route|d'un vehicule|de bus|d'autocars?|automobile|accompagnee|de tracteurs?|"
            r"de bateaux?|d'embarcations?)",
            r"eco[- ]?conduite", r"conducteurs? (?:de bus|d'autocars?|routiers?|poids lourds?|d'engins|de vl|de pl)",
            r"poids lourds?", r"super ?lourds?", r"securite routiere", r"formations? (?:a la )?conduite",
        ),
        exclude=w(r"permis (?:de construire|d'amenager|de chasser|de visite|d'exploitation|de feu|feu|de demolir|"
                  r"d'echafauder|de travail|de fouille|de peche)", r"conduite (?:de projets?|du changement|de reunions?|"
                  r"d'entretiens?|de travaux|d'operations?|de la politique)"),
        keyword="CACES",
    ),
    Theme(
        "management-rh", "management et ressources humaines", "management et RH",
        include=w(
            r"management", r"manageri\w+", r"managers?", r"encadrement (?:intermediaire|superieur|de proximite|d'equipes?)",
            r"formations? des encadrants", r"leadership", r"cadres? dirigeants?", r"pilotage d'equipes?",
            r"animer une equipe", r"animation d'equipes?", r"conduite du changement",
            r"gestion des conflits", r"codeveloppement", r"co-developpement", r"coaching", r"ressources humaines",
            r"gpec", r"gepp", r"entretiens? (?:professionnels?|annuels?|d'evaluation)", r"qvt", r"qvct",
            r"qualite de vie (?:et des conditions de travail|au travail)", r"gestion de projets?", r"conduite de projets?",
            r"mode projet", r"chefs? de projets?", r"prise de (?:poste|fonctions?) (?:de |d')?(?:manager|encadr\w+|cadres?)",
            r"techniques de recrutement", r"recruter",
        ),
        exclude=w(r"management (?:environnemental|de l'energie|de la qualite|qualite|des risques|du risque|de la securite|"
                  r"de l'information|des donnees)", r"systemes? (?:de )?management", r"learning management",
                  r"co-?developpement de l'enfant", r"coaching (?:interculturels?|sportifs?|scolaires?|des etudiants)",
                  r"(?:techniques|outils) de coaching dans (?:ses|leurs|les) pratiques",
                  r"(?:techniques?|outils?|methodes?)(?: d'accompagnement)? (?:issue?s? du|de|du) coaching"),
        keyword="management",
    ),
    Theme(
        "numerique-bureautique-ia", "numérique, bureautique et IA", "numérique, bureautique et IA",
        include=w(
            r"bureautiques?", r"formations? (?:a l'|en )?informatiques?", r"outils informatiques", r"informatique",
            r"excel", r"word", r"powerpoint", r"outlook", r"office ?365", r"microsoft ?(?:office|365)", r"m365",
            r"pack office", r"libre ?office", r"tableurs?", r"traitement de texte",
            r"(?:outils|competences|usages?|culture|inclusion|transformation|transition|acculturation|pratiques|"
            r"environnement|sobriete|accessibilite|citoyennete|mediation|mediateurs?) numeriques?",
            r"numerique responsable", r"au numerique", r"du numerique", r"le numerique",
            r"intelligence artificielle", r"ia(?: generative)?", r"chatgpt", r"copilot\w*",
            r"cyber ?securite", r"securite (?:informatique|des systemes d'information|numerique)",
            r"sig", r"systemes? d'information geographique", r"qgis", r"arcgis", r"programmation (?:informatique|web)",
            r"langages? de programmation", r"python", r"developpement (?:web|informatique|logiciel)", r"devops",
            r"cloud", r"virtualisation", r"reseaux informatiques", r"linux", r"windows", r"cao", r"dao", r"autocad",
            r"revit", r"bim", r"pao", r"adobe", r"photoshop", r"indesign", r"illustrator", r"outils collaboratifs",
            r"teams", r"sharepoint", r"power ?bi", r"analyse des donnees", r"science des donnees", r"data ?science",
            r"competences digitales", r"outils digitaux", r"marketing digital", r"transformation digitale",
        ),
        exclude=w(r"plateformes? (?:de formation )?(?:numerique|digitale|e-learning)",
                  r"(?:en|au format|format|support|supports|contenus?|ressources?|parcours|modules?) (?:numeriques?|digitale?s?)",
                  r"(?:outils?|ressources?) pedagogiques? numeriques?", r"archivage numerique",
                  r"abonnements? a des outils numeriques", r"learning management system", r"lms",
                  r"programmation (?:et techniques )?du spectacle", r"programmation (?:des |de la )?formation",
                  r"logiciel de gestion de la formation"),
        keyword="informatique",
    ),
    Theme(
        "langues", "langues", "langues",
        include=w(
            r"langues?(?: vivantes?| etrangeres?)?", r"linguistiques?", r"anglais", r"allemand", r"espagnol", r"italien",
            r"portugais", r"chinois", r"mandarin", r"arabe", r"russe", r"neerlandais", r"japonais", r"creole",
            r"fle", r"francais langue (?:etrangere|seconde|d'integration)", r"fls", r"fli", r"cours de francais",
            r"apprentissage du francais", r"toeic", r"toefl", r"ielts", r"linguaskill", r"cecrl",
            r"lsf", r"langue des signes", r"english", r"sociolinguistiques?",
        ),
        exclude=w(r"langages? (?:informatiques?|de programmation)", r"elements? de langage", r"sejours? linguistiques?"),
        keyword="anglais",
    ),
    Theme(
        "sante-medico-social", "santé et médico-social", "santé et médico-social",
        include=w(
            r"soins?", r"soignants?", r"aides?[- ]soignant\w*", r"infirmi\w+", r"ifsi", r"medica(?:l|le|les|ux)",
            r"medico-?\w+", r"medicaments?", r"medecins?",
            r"medecine", r"paramedic\w+", r"sante mentale", r"psychiatri\w+", r"geriatri\w+", r"gerontologi\w+",
            r"ehpad", r"personnes? agees?", r"handicaps?", r"autis\w+", r"tsa", r"polyhandicap\w*", r"petite enfance",
            r"puericult\w+", r"assistant(?:e)?s? (?:maternel|familia)\w*", r"accueillants? familiaux", r"creches?",
            r"protection de l'enfance", r"travail social", r"travailleurs? sociaux", r"medico-sociale?s?",
            r"sanitaires? et sociale?s?", r"addict\w+", r"bientraitance", r"maltraitance", r"fin de vie",
            r"soins palliatifs", r"echographi\w+", r"pharmac\w+", r"hygiene hospitaliere", r"infections? nosocomiales?",
            r"dpc", r"developpement professionnel continu", r"aides? a domicile", r"auxiliaires? de vie",
            r"accompagnants? educatifs? et sociaux", r"agents? de service hospitalier", r"sages?-femmes?",
            r"kinesitherap\w+", r"dentaires?", r"bucco-dentaire", r"nutrition", r"dietetique", r"vaccination",
            r"prevention (?:sante|du suicide)", r"suicide", r"sante publique", r"premiers secours en sante mentale",
            r"pssm", r"education therapeutique", r"radioprotection des patients", r"biologie medicale",
            r"gestes et soins d'urgence", r"afgsu", r"secretaires? medica\w+", r"(?:domaine|secteur|metiers|formations?) "
            r"(?:de la |du )?(?:sante|sanitaire|social|medico-social)",
            r"sante (?:mentale|publique|sexuelle|environnementale|des enfants|de l'enfant)", r"pmi", r"aidants?",
            r"perinatalite", r"soutien a la parentalite", r"violences (?:faites aux femmes|conjugales|intrafamiliales)",
        ),
        exclude=w(r"sante,? (?:et )?securite", r"securite et sante", r"sante au travail", r"premiers? (?:secours|soins)",
                  r"sauveteurs?[- ]secouristes?", r"secourisme", r"handicap (?:au|et|en) (?:travail|emploi|entreprise)",
                  r"etablissements? (?:de sante|sanitaires?|medico-sociaux|hospitaliers?)", r"centres? hospitaliers?",
                  r"cadres? de sante", r"directeurs? des soins", r"medico-?legal\w*", r"medicaments? veterinaires?",
                  r"(?:professionnels?|personnels?) (?:non[- ])?medica\w*(?: et (?:non[- ])?medica\w*)?",
                  r"(?:personnels?|agents?|salaries?|professionnels?|equipes?) (?:des? |du |de la |de l')?(?:services?|secteurs?|"
                  r"structures?|etablissements?) (?:d'aides? a domicile|medico-sociaux|de soins|sanitaires?|d'aide)",
                  r"gaz medicaux"),
        keyword="santé",
    ),
    Theme(
        "insertion-emploi-poec", "insertion et emploi (POEC, PRF, compétences clés)", "insertion et emploi",
        include=w(
            r"poec?", r"poei", r"preparations? operationnelles? a l'emploi", r"programmes? regiona(?:l|ux) de formations?",
            r"prf", r"prfp", r"actions? de formation conventionnees?", r"afc", r"demandeurs? d'emploi",
            r"personnes? (?:a|en) (?:la )?recherche d'(?:un )?emploi", r"competences? (?:cles|de base|premieres)",
            r"savoirs? de base", r"clea", r"illettrisme", r"alphabetisation", r"insertion (?:sociale et )?professionnelle",
            r"(?:parcours|structures?|chantiers?|services?|prestations?|actions?|dispositifs?|ateliers?) d'insertion",
            r"(?:publics?|personnes?|beneficiaires?|participants?) (?:en|de l') ?(?:parcours d')?insertion",
            r"insertion (?:dans l'emploi|par l'activite economique|socio-?professionnelle)",
            r"plans? locaux? pour l'insertion", r"remobilisation", r"redynamisation", r"pre-?qualifi\w+", r"parcours (?:vers|d'acces a) l'emploi",
            r"retour a l'emploi", r"acces a l'emploi", r"accompagnement vers l'emploi", r"jeunes? (?:de 16 a 2[56] ans|neet)",
            r"ecoles? de la (?:deuxieme|2e) chance", r"rsa", r"plie", r"chantiers? d'insertion", r"iae",
            r"plan d'investissement dans les competences", r"cap emploi", r"decouverte des metiers",
            r"service militaire adapte", r"rsma", r"smv", r"service militaire volontaire", r"epide",
            r"beneficiaires? de la protection internationale",
        ),
        exclude=w(r"professionnel\.?(?:le)?s? (?:de l'|des? )?(?:insertion|missions? locales?)", r"acteurs? de l'insertion",
                  r"conseillers? (?:en )?insertion", r"salaries? (?:de|des) (?:la )?missions? locales?",
                  r"plans? regiona(?:l|ux) de formations? (?:ml|des missions locales)",
                  r"insertion professionnelle (?:des|pour les|aux) (?:eleves|etudiants|ingenieurs|diplomes|doctorants|apprentis)",
                  r"referent\.?(?:e|es)?s? (?:rsa|insertion|de parcours)", r"formations? des referent\w*",
                  # basic skills bought by an employer for its own staff, executive coaching, school pupils
                  r"(?:competences?|savoirs?) (?:cles|de base|premieres|fondamentaux)[^.;]{0,40}(?:agents?|salaries?|personnels?|collaborateurs?)",
                  r"(?:agents?|salaries?|personnels?|collaborateurs?)[^.;]{0,40}(?:competences?|savoirs?) (?:cles|de base|premieres|fondamentaux)",
                  ),
        drop=w(r"codir|comites? de direction|equipes? de direction", r"(?:publics?|milieux?) scolaires?", r"eleves",
               r"collegiens", r"lyceens", r"enseignants"),
        # Regional associations of the Missions Locales buy training for their own staff (« programme régional de
        # formation » there means a staff-training plan, not training for jobseekers).
        # The CNFPT trains territorial civil servants only (« lutte contre l'illettrisme » there is for agents).
        exclude_buyer=r"\barml\b|\bamilaura\b|missions? locales?|union regionale des missions|\bcnfpt\b|\binet\b",
        keyword="POEC",
    ),
]}


def themes_of(t: Notice) -> list[str]:
    return [slug for slug, th in THEMES.items() if th.matches(t)]


# --- buyers ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Opco:
    slug: str
    name: str  # official short name
    sector: str  # as listed by the Ministry of Labour
    site: str  # official website, as linked by the Ministry of Labour
    pattern: str  # regex on fold(buyer)


# The 11 OPCOs approved on 1 April 2019, with the sector and website given on
# https://travail-emploi.gouv.fr/les-operateurs-de-competences-opco (page updated 30/09/2026, read 2026-10-17).
OPCOS: list[Opco] = [
    Opco("afdas", "Afdas", "culture, médias, loisirs, sports", "https://www.afdas.com/", r"\bafdas\b"),
    Opco("atlas", "Opco Atlas", "services financiers et conseil", "https://opco-atlas.fr/", r"\bopco[ -]?atlas\b|^atlas$"),
    Opco("cohesion-sociale", "Uniformation (Opco Cohésion sociale)", "cohésion sociale", "https://www.uniformation.fr/",
         r"\buniformation\b|\bopco (?:de la )?cohesion sociale\b"),
    Opco("akto", "Akto", "entreprises à forte intensité de main-d'œuvre", "https://www.akto.fr/", r"\bakto\b"),
    Opco("ocapiat", "Ocapiat", "agriculture, pêche, agroalimentaire", "https://www.ocapiat.fr/", r"\bocapiat\b"),
    Opco("2i", "Opco 2i", "interindustriel", "https://www.opco2i.fr/", r"\bopco[ -]?2i\b"),
    Opco("constructys", "Constructys", "construction", "https://www.constructys.fr/", r"\bconstructys\b"),
    Opco("mobilites", "Opco Mobilités", "transports, voyages, distribution", "https://www.opcomobilites.fr/",
         r"\bopco[ -]?mobilites?\b"),
    Opco("ep", "Opco EP", "artisanat, professions libérales, services de proximité", "https://www.opcoep.fr/",
         r"\bopco[ -]?ep\b|\bopco des entreprises de proximite\b"),
    Opco("sante", "Opco Santé", "santé", "https://opco-sante.fr/", r"\bopco[ -]?sante\b"),
    Opco("opcommerce", "L'Opcommerce", "commerce", "https://www.lopcommerce.com/", r"\bl?'?opcommerce\b|\bopco (?:du )?commerce\b"),
]


def opco_of(t: Notice) -> Opco | None:
    b = fold(t.buyer)
    return next((o for o in OPCOS if re.search(o.pattern, b)), None)


FRANCE_TRAVAIL_RE = re.compile(r"\bfrance travail\b|\bpole emploi\b")


def is_france_travail(t: Notice) -> bool:
    return bool(FRANCE_TRAVAIL_RE.search(fold(t.buyer)))


# Precision of the final rules on the last hand-checked sample each theme was NOT tuned on (correct, classified).
# Details, rounds and labels: docs/evaluation-themes.md and tests/fixtures/theme_labels.json. A theme is published
# only if this is >= 90 % (PRECISION_GATE); otherwise set Theme.published = False.
PRECISION_GATE = 0.90
PRECISION: dict[str, tuple[int, int]] = {
    "securite-sst-incendie": (24, 24),
    "conduite-caces-permis": (23, 23),
    "management-rh": (33, 35),
    "numerique-bureautique-ia": (16, 16),
    "langues": (17, 18),
    "sante-medico-social": (31, 34),
    "insertion-emploi-poec": (35, 37),
}
