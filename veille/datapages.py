"""Data pages built from the stored notices: regions (v2), themes, OPCO buyers.

Every number on these pages is computed at build time from data/notices.json (and data/of_qualiopi.json for the
supply side); nothing is generated text. Editorial blocks are short, written once, and cite their official source with
the date it was read (EDITORIAL_CHECKED). Each page follows the index rule of stats.index_decision: below the volume
threshold it stays reachable but carries « noindex, follow » and leaves the sitemap.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Iterable

from . import stats, supply, taxonomy
from .digest import NATIONAL_THRESHOLD, is_national
from .exporters import MONTHS, fmt_date, fmt_deadline
from .geo import OUTRE_MER, REGIONS, slugify
from .model import CPV_LABELS, PARIS, Notice, fold

ROOT = Path(__file__).resolve().parent.parent
QUALIOPI_FILE = ROOT / "data" / "of_qualiopi.json"
REPO_BLOB = "https://github.com/VREAD-corp/veille-ao-formation/blob/main/"
EVALUATION_URL = REPO_BLOB + "docs/evaluation-themes.md"

# Date the official sources quoted in the editorial blocks (themes, OPCO, guides) were last read. The build warns
# when it is more than EDITORIAL_REVIEW_DAYS old (tests/test_datapages.py checks it is set and not in the future).
EDITORIAL_CHECKED = "2026-10-17"
EDITORIAL_REVIEW_DAYS = 183

# Overseas territories of the « Outre-mer » page, by department code.
TERRITORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Guadeloupe", ("971",)), ("Martinique", ("972",)), ("Guyane", ("973",)), ("La Réunion", ("974",)),
    ("Mayotte", ("976",)), ("Saint-Martin et Saint-Barthélemy", ("977", "978")),
]
TERRITORY_IN = {"Guadeloupe": "en Guadeloupe", "Martinique": "en Martinique", "Guyane": "en Guyane",
                "La Réunion": "à La Réunion", "Mayotte": "à Mayotte",
                "Saint-Martin et Saint-Barthélemy": "à Saint-Martin et Saint-Barthélemy"}
TERRITORY_QUALIOPI = {"Guadeloupe": "Guadeloupe", "Martinique": "Martinique", "Guyane": "Guyane",
                      "La Réunion": "La Réunion", "Mayotte": "Mayotte"}


def e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def nb(n: float | int) -> str:
    """French thousands separator (narrow no-break space)."""
    return f"{n:,.0f}".replace(",", " ")


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)} %" if whole else "–"


def plural(n: int, one: str, many: str) -> str:
    return f"{nb(n)} {one if n <= 1 else many}"


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTHS[int(m) - 1]} {y[2:]}"


def load_qualiopi() -> dict:
    return supply.load(QUALIOPI_FILE)


# --- shared blocks ------------------------------------------------------------------------------------------

def notice_table(items: list[Notice], *, caption: str, region: bool = False) -> str:
    """Compact table: title (link to the official notice), buyer, deadline (and place)."""
    if not items:
        return '<p class="empty">Aucun avis sur cette période.</p>'
    rows = []
    for t in items:
        place = ""
        if region:
            place = f'<td>{e("National / plusieurs régions" if is_national(t) else ", ".join(t.regions))}</td>'
        rows.append(f'<tr><td><a href="{e(t.url)}" rel="noopener">{e(t.title)}</a></td><td>{e(t.buyer)}</td>{place}'
                    f'<td class="num">{e(fmt_date(t.deadline)) if t.deadline else "–"}</td></tr>')
    head_place = '<th scope="col">Lieu</th>' if region else ""
    return (f'<div class="table-wrap"><table class="data-table stack"><caption class="sr-only">{e(caption)}</caption>'
            f'<thead><tr><th scope="col">Objet (avis officiel)</th><th scope="col">Acheteur</th>{head_place}'
            f'<th scope="col" class="num">Date limite</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def months_table(fig: stats.Figures, caption: str) -> str:
    head = "".join(f'<th scope="col">{e(month_label(k))}</th>' for k, _ in fig.months)
    cells = "".join(f"<td>{n}</td>" for _, n in fig.months)
    return (f'<div class="table-wrap"><table class="data-table months"><caption class="sr-only">{e(caption)}</caption>'
            f'<thead><tr>{head}</tr></thead><tbody><tr>{cells}</tr></tbody></table></div>')


def buyers_list(fig: stats.Figures) -> str:
    if not fig.top_buyers:
        return '<p class="empty">Aucun avis en 12 mois.</p>'
    return '<ol class="rank">' + "".join(f"<li>{e(b)} <span class=\"muted\">({plural(n, 'avis', 'avis')})</span></li>"
                                         for b, n in fig.top_buyers) + "</ol>"


def structure_line(fig: stats.Figures) -> str:
    parts = [f"{pct(fig.with_lots, fig.n12)} des avis sont allotis",
             f"{pct(fig.framework, fig.n12)} annoncent un accord-cadre dans leur objet"]
    if len(fig.values) >= 5:
        parts.append(f"le montant estimé médian est de {nb(fig.median_value)} € HT, sur les {len(fig.values)} avis "
                     "qui publient un montant")
    return "Sur ces 12 mois, " + ", ".join(parts[:-1]) + (" et " if len(parts) > 1 else "") + parts[-1] + "."


def breadcrumb_ld(site, trail: list[tuple[str, str | None]]) -> dict:
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name,
                                 **({"item": site.url + path} if path is not None else {})}
                                for i, (name, path) in enumerate(trail)]}


def collection_ld(site, path: str, name: str, items: list[Notice], modified: str) -> dict:
    return {"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "url": site.url + path,
            "inLanguage": "fr-FR", "dateModified": modified, "isPartOf": {"@type": "WebSite", "url": site.url},
            "publisher": site.org_ref,
            "mainEntity": {"@type": "ItemList", "numberOfItems": len(items),
                           "itemListElement": [{"@type": "ListItem", "position": k + 1, "url": t.url, "name": t.title}
                                               for k, t in enumerate(items[:100])]}}


def crumbs(site, trail: list[tuple[str, str | None]]) -> str:
    parts = [f'<a href="{site.link(p)}">{e(n)}</a>' if p is not None else e(n) for n, p in trail]
    return f'<nav class="crumbs" aria-label="Fil d\'Ariane">{" › ".join(parts)}</nav>'


def alert_cta(site, title: str, text: str, href: str) -> str:
    if not site.sales.open:
        return ""
    from . import landing
    price = landing.price(site.sales.products["veille-ao-3m"])
    return f"""<aside class="inline-cta data-cta" aria-label="Alertes e-mail">
<div><p class="ic-title">{e(title)}</p>
<p class="ic-text">{text} Dès {e(price)} pour 3 mois, payés une fois, sans renouvellement automatique. <a href="{site.link('rss.xml')}" data-mesure="free_download">Ou suivre le flux RSS gratuit</a> (toutes régions).</p></div>
<a class="btn" href="{e(href)}" data-cta>Recevoir les alertes →</a>
</aside>"""


def index_note(decision: stats.IndexDecision) -> str:
    """Visible explanation when the page is below the threshold (kept for visitors, out of the index)."""
    if decision.index:
        return ""
    return (f'<p class="small muted">Page peu fournie pour l\'instant ({plural(decision.n12, "avis publié", "avis publiés")} '
            f"en 12 mois, {nb(decision.median_open)} ouverts un jour type) : elle reste consultable mais n'est pas proposée "
            "aux moteurs de recherche tant que le volume reste sous nos seuils (40 avis par an et 4 avis ouverts un jour type).</p>")


# --- regions ------------------------------------------------------------------------------------------------

def region_scope(tenders: Iterable[Notice], region: str) -> list[Notice]:
    return [t for t in tenders if region in t.regions and not is_national(t)]


def territory_of(t: Notice) -> list[str]:
    deps = set(t.departments)
    return [name for name, codes in TERRITORIES if deps & set(codes)]


def nearest_regions(region: str, n12: dict[str, int], k: int = 4) -> list[str]:
    others = [r for r in REGIONS if r != region]
    return sorted(others, key=lambda r: (abs(n12[r] - n12[region]), r))[:k]


def region_page(site, region: str, open_items: list[Notice], national: list[Notice], n12_by: dict[str, int],
                theme_pages: dict[str, stats.IndexDecision]) -> None:
    from .site import region_in
    slug = slugify(region)
    path = f"region/{slug}/"
    where = region_in(region)
    today = site.now.astimezone(PARIS).date()
    scope = region_scope(site.tenders, region)
    local_open = [t for t in open_items if t in scope]
    closed = stats.closed_recently(scope, site.now)
    fig = stats.figures(scope, today)
    decision = stats.index_decision(scope, today)
    site.decisions[path] = decision
    q = (site.qualiopi.get("regions") or {}).get(region)
    today_label = fmt_date(today.isoformat())

    lead = (f"<strong>{plural(len(local_open), 'appel', 'appels')} d'offres de formation "
            f"{'ouvert' if len(local_open) <= 1 else 'ouverts'}</strong> {e(where)} au {today_label}, plus "
            f"{plural(len(national), 'annonce nationale ou multi-régions', 'annonces nationales ou multi-régions')}. "
            f"En 12 mois, {plural(fig.n12, 'avis a été publié', 'avis ont été publiés')} pour un lieu d'exécution "
            f"{e(where)}, soit environ {nb(round(fig.n12 / 12))} par mois")
    lead += (f". Délai médian pour répondre : {fig.median_days} jours entre la publication et la date limite."
             if fig.median_days else ".")

    themes = Counter(s for t in stats.last12(scope, today) for s in taxonomy.themes_of(t) if s in theme_pages)
    theme_items = "".join(
        f'<li><a href="{site.link(f"theme/{s}/")}">{e(taxonomy.THEMES[s].label_cap)}</a> '
        f'<span class="muted">({plural(n, "avis", "avis")})</span></li>' for s, n in themes.most_common(6))
    theme_html = (f'<h3>Thèmes les plus fréquents</h3><ul class="rank">{theme_items}</ul>'
                  f'<p class="small muted">Un avis peut relever de plusieurs thèmes (un même marché alloti peut couvrir '
                  f'la sécurité, la conduite et le management). Thèmes repérés dans l\'objet et les lots : '
                  f'<a href="{site.link("theme/")}">tous les thèmes</a>.</p>') if theme_items else ""

    qual_html = ""
    if q:
        ratio = f", soit environ {nb(round(q['qualiopi'] / fig.n12))} organismes pour un appel d'offres publié en 12 mois" if fig.n12 else ""
        qual_html = f"""<section class="section" aria-labelledby="qualiopi-h">
<h2 id="qualiopi-h">Organismes de formation certifiés Qualiopi {e(where)}</h2>
<p><strong>{nb(q['qualiopi'])}</strong> organismes de formation dont l'adresse déclarée est {e(where)} sont certifiés Qualiopi pour les « actions de formation »{ratio}. La liste en compte {nb(q['declared'])} au total, certifiés ou non.</p>
<p class="small muted">Source : <a href="{e(site.qualiopi.get('dataset', supply.DATASET_URL))}">liste publique des organismes de formation</a> (ministère du Travail, data.gouv.fr, Licence Ouverte), extraction du {e(fmt_date(site.qualiopi.get('extracted', today.isoformat())))}, comptée par nos soins selon l'adresse de l'organisme. Un organisme peut répondre hors de sa région : ce rapport donne un ordre de grandeur de la concurrence et ne dit pas combien d'organismes répondent.</p>
</section>"""

    territory_html = ""
    if region == OUTRE_MER:
        blocks = []
        y = stats.last12(scope, today)
        for name, _codes in TERRITORIES:
            t_open = [t for t in local_open if name in territory_of(t)]
            t12 = [t for t in y if name in territory_of(t)]
            tq = (site.qualiopi.get("territories") or {}).get(TERRITORY_QUALIOPI.get(name, ""), {})
            qline = (f" {nb(tq['qualiopi'])} organismes de formation certifiés Qualiopi y ont leur adresse." if tq else "")
            listing = "".join(f'<li><a href="{e(t.url)}" rel="noopener">{e(t.title)}</a> – {e(t.buyer)}, '
                              f'date limite {e(fmt_deadline(t.deadline))}</li>' for t in t_open)
            blocks.append(f"""<h3 id="{slugify(name)}">Appels d'offres formation {e(TERRITORY_IN[name])}</h3>
<p>{plural(len(t_open), "avis ouvert", "avis ouverts")} au {today_label}, {plural(len(t12), "avis publié", "avis publiés")} en 12 mois.{qline}</p>
{f'<ul class="links">{listing}</ul>' if listing else ''}""")
        territory_html = f"""<section class="section" aria-labelledby="territoires-h">
<h2 id="territoires-h">Par territoire</h2>
<p class="small muted">Classement selon le département indiqué dans l'avis ; un avis qui couvre plusieurs territoires compte dans chacun.</p>
{''.join(blocks)}
</section>"""

    near = nearest_regions(region, n12_by)
    near_links = ", ".join(f'<a href="{site.link(f"region/{slugify(o)}/")}">{e(o)}</a> ({nb(n12_by[o])} en 12 mois)' for o in near)
    others = ", ".join(f'<a href="{site.link(f"region/{slugify(o)}/")}">{e(o)}</a>' for o in REGIONS if o != region)

    cta = alert_cta(site, f"Recevoir chaque matin les nouveaux avis {where}",
                    f"Un e-mail vers 7&nbsp;h avec les avis publiés la veille {e(where)} (et les avis nationaux), filtrés sur vos mots-clés si vous le souhaitez.",
                    site.link(f"alertes/?region={slug}"))

    body = f"""
{crumbs(site, [("Accueil", ""), ("Régions", "region/"), (region, None)])}
<h1>Appels d'offres formation {e(where)}</h1>
<p class="lead">{lead}</p>
<p class="small muted">Sources : BOAMP et TED, mise à jour le {site.updated_label()}. <a href="{site.link('methode/')}">Comment ces chiffres sont calculés</a>.</p>
{index_note(decision)}
<section class="section" aria-labelledby="ouverts-h">
<h2 id="ouverts-h">Ouverts {e(where)} ({len(local_open)})</h2>
{site.cards(local_open)}
</section>
{cta}
<details class="national">
<summary><h2>Annonces nationales ou multi-régions ({len(national)})</h2></summary>
<p class="small muted">Avis sans lieu précis, ou dont le lieu couvre {NATIONAL_THRESHOLD} régions ou plus. Ils figurent sur les {len(REGIONS)} pages régionales.</p>
{site.cards(national)}
</details>
{territory_html}
<section class="section" aria-labelledby="clos-h">
<h2 id="clos-h">Clôturés ces 90 derniers jours ({len(closed)})</h2>
<p class="small muted">Pour repérer les acheteurs de la région et préparer leurs prochaines consultations : titre, acheteur et date limite, avec le lien vers l'avis officiel.</p>
{notice_table(closed, caption=f"Avis clôturés ces 90 derniers jours {where}")}
</section>
<section class="section" aria-labelledby="stats-h">
<h2 id="stats-h">Les marchés de formation {e(where)} sur 12 mois</h2>
<p>{plural(fig.n12, "avis publié", "avis publiés")} du {fmt_date(fig.since)} au {today_label}, un par marché (BOAMP et TED fusionnés, annulés exclus).</p>
{months_table(fig, f"Avis publiés par mois {where}")}
<h3>Acheteurs les plus actifs</h3>
{buyers_list(fig)}
{theme_html}
<p>{structure_line(fig)}</p>
</section>
{qual_html}
<p class="region-line"><span>Régions au volume proche :</span> {near_links}.</p>
<p class="region-line"><span>Voir aussi :</span> <a href="{site.link('theme/')}">les appels d'offres par thème</a>, <a href="{site.link('acheteur/opco/')}">les appels d'offres des OPCO</a>.</p>
<p class="region-line"><span>Autres régions :</span> {others}.</p>
"""
    n = len(local_open)
    modified = site.content_date(scope + national)
    jsonld = [collection_ld(site, path, f"Appels d'offres formation {where}", local_open, modified),
              breadcrumb_ld(site, [("Accueil", ""), ("Régions", "region/"), (region, path)])]
    site.write(path, site.page(
        path, f"Appels d'offres formation {region} – marchés publics ouverts",
        f"{n} appel{'s' if n > 1 else ''} d'offres de formation {'ouverts' if n > 1 else 'ouvert'} {where} aujourd'hui, "
        f"{fig.n12} publiés en 12 mois (BOAMP, TED) : acheteurs, dates limites, liens officiels. Mis à jour 2 fois par jour.",
        body, jsonld=jsonld, sticky=True, noindex=decision.robots),
        sitemap=decision.index, lastmod=modified)


def region_hub(site, open_items: list[Notice], national: list[Notice], n12_by: dict[str, int]) -> None:
    today = fmt_date(site.now.astimezone(PARIS).date().isoformat())
    rows = []
    for r in REGIONS:
        slug = slugify(r)
        local = [t for t in open_items if r in t.regions and not is_national(t)]
        q = (site.qualiopi.get("regions") or {}).get(r, {}).get("qualiopi")
        rows.append(f'<tr><th scope="row"><a href="{site.link(f"region/{slug}/")}">{e(r)}</a></th>'
                    f'<td>{len(local)}</td><td>{nb(n12_by[r])}</td><td>{nb(q) if q else "–"}</td></tr>')
    body = f"""
{crumbs(site, [("Accueil", ""), ("Régions", None)])}
<h1>Appels d'offres formation par région</h1>
<p class="lead">{len(open_items)} appels d'offres de formation ouverts au {today}, classés selon le lieu d'exécution indiqué dans l'avis (départements BOAMP ou codes NUTS de TED). Les {len(national)} annonces sans lieu précis, ou dont le lieu couvre {NATIONAL_THRESHOLD} régions ou plus, sont classées « nationales » et figurent sur chaque page régionale.</p>
<div class="table-wrap"><table class="region-table">
<thead><tr><th scope="col">Région</th><th scope="col">Ouverts aujourd'hui</th><th scope="col">Publiés en 12 mois</th><th scope="col">Organismes Qualiopi</th></tr></thead>
<tbody>{''.join(rows)}<tr><th scope="row">Nationales ou multi-régions</th><td>{len(national)}</td><td>{nb(n12_by['_national'])}</td><td>–</td></tr></tbody>
</table></div>
<p class="small muted">Un avis qui couvre deux régions compte dans chacune. « Publiés en 12 mois » : un avis par marché, annulés exclus. « Organismes Qualiopi » : organismes certifiés pour les actions de formation, selon l'adresse déclarée dans la <a href="{supply.DATASET_URL}">liste publique des organismes de formation</a> (extraction du {e(fmt_date(site.qualiopi.get('extracted', '2026-10-17')))}). <a href="{site.link('methode/')}">Méthode et sources</a>.</p>
<p class="region-line"><span>Par thème :</span> <a href="{site.link('theme/')}">sécurité, conduite, management, numérique, langues, santé, insertion</a>. <span>Par acheteur :</span> <a href="{site.link('acheteur/opco/')}">OPCO</a>.</p>
"""
    site.write("region/", site.page("region/", "Appels d'offres formation par région",
                                    f"{len(open_items)} appels d'offres publics de formation ouverts, région par région : avis ouverts, "
                                    "publiés en 12 mois et organismes certifiés Qualiopi. BOAMP et TED.", body,
                                    jsonld=breadcrumb_ld(site, [("Accueil", ""), ("Régions", "region/")])),
               lastmod=site.content_date(site.tenders))


# --- themes -------------------------------------------------------------------------------------------------

# Page title, meta lead, and what the notices of each theme are made of (facets, counted on title + lots).
THEME_SEO: dict[str, dict] = {
    "securite-sst-incendie": {
        "title": "Appels d'offres formation SST, incendie, sécurité – marchés publics ouverts",
        "facets": [("SST, secourisme, premiers secours", r"\bsst\b|secouris|premiers? secours|\bpsc ?1?\b|\bpse ?[12]\b|gestes qui sauvent"),
                   ("Incendie, EPI, évacuation, SSIAP", r"incendie|extincteur|evacuation|equipiers? de premiere intervention|ssiap"),
                   ("Habilitation électrique", r"habilitations? electrique|\bb0\b|\bh0"),
                   ("Travail en hauteur, échafaudage, harnais", r"travail en hauteur|echafaud|harnais"),
                   ("AIPR (travaux à proximité des réseaux)", r"\baipr\b|proximite des reseaux"),
                   ("Amiante", r"amiante|sous[- ]section 4|\bss4\b"),
                   ("Espaces confinés (CATEC)", r"catec|espaces? confines?"),
                   ("Gestes et postures, PRAP", r"\bprap\b|gestes et postures|activite physique"),
                   ("Risques psychosociaux", r"psychosociaux|\brps\b")],
    },
    "conduite-caces-permis": {
        "title": "Appels d'offres formation CACES et permis – marchés publics ouverts",
        "facets": [("CACES et recommandations R482 à R490", r"caces|\br ?4(?:8[2-9]|90)\b"),
                   ("Autorisation de conduite, conduite d'engins", r"autorisations? de conduite|conduite (?:en securite )?d'engins|engins de chantier"),
                   ("Permis poids lourd, super lourd, remorque (C, CE, BE)", r"poids lourds?|super ?lourds?|permis (?:c|ce|be|c1)\b|remorque"),
                   ("FIMO, FCO (transport routier)", r"\bfimo\b|\bfco\b"),
                   ("Permis B, code de la route, auto-école", r"permis b\b|code de la route|auto[- ]?ecole|permis de conduire"),
                   ("Conduite préventive, éco-conduite, risque routier", r"eco[- ]?conduite|conduite (?:preventive|defensive|sur route)|risques? routiers?|securite routiere"),
                   ("Permis bateau et autres permis", r"permis (?:bateau|cotier|fluvial|am|a\b|moto)")],
    },
    "management-rh": {
        "title": "Appels d'offres formation management et RH – marchés publics ouverts",
        "facets": [("Formations au management, managers, encadrants", r"management|manageri|managers?|encadr"),
                   ("Coaching individuel ou collectif", r"coaching"),
                   ("Leadership, cadres dirigeants", r"leadership|dirigeants?"),
                   ("Codéveloppement, analyse de pratiques managériales", r"co-?developpement"),
                   ("Gestion et conduite de projet", r"(?:gestion|conduite|management) de projets?|mode projet"),
                   ("Prise de poste de manager", r"prise de (?:poste|fonctions?)"),
                   ("RH, GPEC, entretiens professionnels, QVCT", r"ressources humaines|\bgpec\b|\bgepp\b|entretiens? (?:professionnels?|annuels?)|\bqvc?t\b|qualite de vie")],
    },
    "numerique-bureautique-ia": {
        "title": "Appels d'offres formation bureautique, numérique et IA – marchés ouverts",
        "facets": [("Bureautique, Microsoft 365, Excel", r"bureautique|excel|word|powerpoint|outlook|office|365|libre ?office|tableur"),
                   ("Intelligence artificielle", r"intelligence artificielle|\bia\b|chatgpt|copilot"),
                   ("Cybersécurité, sécurité des SI", r"cyber|securite (?:informatique|des systemes)"),
                   ("Informatique, réseaux, cloud, développement", r"informatique|reseaux|cloud|devops|developpement (?:web|logiciel|informatique)|python|linux"),
                   ("Données, SIG, CAO, BIM", r"donnees|data|\bsig\b|qgis|arcgis|\bcao\b|\bdao\b|autocad|revit|\bbim\b|power ?bi"),
                   ("Culture et inclusion numérique", r"numerique")],
    },
    "langues": {
        "title": "Appels d'offres formation langues (anglais, FLE) – marchés publics ouverts",
        "facets": [("Anglais", r"anglais|english|anglaise"),
                   ("Français langue étrangère, ateliers sociolinguistiques", r"\bfle\b|francais langue|sociolinguistique|apprentissage du francais|alphabetisation"),
                   ("Tests et certifications (TOEIC, Linguaskill…)", r"toeic|toefl|ielts|linguaskill|certification"),
                   ("Autres langues étrangères", r"allemand|espagnol|italien|portugais|chinois|mandarin|arabe|russe|neerlandais|japonais"),
                   ("Langue des signes", r"\blsf\b|langue des signes"),
                   ("Langues régionales", r"basque|occitan|catalan|breton|alsacien|creole|corse")],
    },
    "sante-medico-social": {
        "title": "Appels d'offres formation santé et médico-social – marchés ouverts",
        "facets": [("Assistants maternels et familiaux, accueillants familiaux", r"assistant(?:e)?s? (?:maternel|familia)|accueillants? familiaux"),
                   ("Petite enfance, protection de l'enfance", r"petite enfance|puericult|creche|protection de l'enfance|\bpmi\b|enfan"),
                   ("Santé mentale, psychiatrie, PSSM", r"sante mentale|psychiatri|\bpssm\b|psych"),
                   ("Personnes âgées, handicap, aide à domicile", r"personnes? agees?|ehpad|handicap|autis|domicile|geront|geriatr"),
                   ("Diplômes et métiers du soin (AS, AP, IDE, DEAES…)", r"aides?[- ]soignant|infirmi|auxiliaire|ambulanc|\bdeaes\b|accompagnants? educatifs|diplome d'etat"),
                   ("Pratiques soignantes, qualité des soins, DPC", r"soins?|soignant|\bdpc\b|developpement professionnel continu|patients?"),
                   ("Travail social, analyse des pratiques", r"travail social|travailleurs sociaux|analyse des pratiques|supervision")],
    },
    "insertion-emploi-poec": {
        "title": "Appels d'offres formation insertion, POEC, PRF – marchés publics ouverts",
        "facets": [("POEC et préparation opérationnelle à l'emploi", r"\bpoec?\b|\bpoei\b|preparations? operationnelles?"),
                   ("Programmes régionaux de formation (PRF)", r"programmes? regiona(?:l|ux)|\bprfp?\b"),
                   ("Allocataires du RSA, PLIE, insertion par l'activité", r"\brsa\b|\bplie\b|insertion par l'activite|\biae\b"),
                   ("Mobilité Erasmus+ des demandeurs d'emploi", r"erasmus"),
                   ("Personnes placées sous main de justice", r"detenu|justice|carceral|penitentiaire|ppsmj|ecrouees?"),
                   ("EPIDE, service militaire adapté ou volontaire", r"epide|service militaire|\brsma\b|\bsmv\b|\bsma\b"),
                   ("Compétences de base, CléA, illettrisme", r"competences? (?:de base|cles|premieres)|savoirs? de base|\bclea\b|illettrisme")],
    },
}


def theme_editorial(site, slug: str) -> str:
    """Short block per theme: official rules a bidder must know, with sources. Kept short on purpose; the data does
    the rest. Sources read on EDITORIAL_CHECKED."""
    checked = fmt_date(EDITORIAL_CHECKED)
    blocks = {
        "securite-sst-incendie": f"""<p>Deux règles comptent pour répondre à ces marchés. Le sauvetage secourisme du travail (SST) et la prévention des risques liés à l'activité physique (PRAP) sont des dispositifs de l'INRS et de l'Assurance maladie – Risques professionnels : pour les dispenser, un organisme doit être habilité (dossier instruit par la Carsat, la Cramif ou la CGSS, avis de la Commission nationale d'habilitation, numéro attribué pour 5 ans) et ses formateurs certifiés (<a href="https://www.inrs.fr/formation/devenir-organisme-habilite/habilitation-organismes-formation.html">INRS, habilitation des organismes de formation</a>).</p>
<p>L'habilitation électrique est délivrée par l'employeur, qui reconnaît la capacité de son salarié après une formation et un avis d'aptitude du médecin du travail. L'INRS indique que les habilitations doivent être revues chaque année et conseille un recyclage tous les 3 ans (<a href="https://www.inrs.fr/risques/electriques/habilitation-electrique.html">INRS, habilitation électrique</a>). D'où les lots « initial » et « recyclage » ou « MAC » que l'on voit dans les avis.</p>""",
        "conduite-caces-permis": """<p>Les avis de ce thème mélangent deux types de formation : la conduite d'engins (CACES, autorisations de conduite, parfois découpés par recommandation R482, R486, R489, R490) et les permis routiers (C, CE, BE, FIMO et FCO). Les acheteurs sont surtout des communes et intercommunalités, des départements et des SDIS, pour leurs agents. Certains acheteurs publient les deux dans le même marché alloti, avec un lot par catégorie : regardez le détail des lots avant d'écarter un avis.</p>""",
        "management-rh": """<p>Ce thème regroupe les formations au management (prise de poste, management de proximité, leadership), le coaching individuel ou collectif et quelques formations RH (GPEC, entretiens professionnels, QVCT). Les acheteurs vont des communes et départements aux ministères, en passant par le CNFPT, les universités et les OPCO. Lisez l'objet de près : certains avis achètent un parcours conçu sur mesure, d'autres des sessions d'un catalogue existant.</p>""",
        "numerique-bureautique-ia": """<p>Trois familles d'achats se côtoient ici : la bureautique (parfois par niveaux, avec un lot Excel), l'intelligence artificielle, que les OPCO commandent pour leurs entreprises adhérentes et que des chambres consulaires, des universités ou des collectivités achètent pour leur public ou leurs agents, et des formations plus techniques (cybersécurité, réseaux, SIG, CAO). Nous écartons de ce thème les marchés de plateforme ou de logiciel de gestion de la formation.</p>""",
        "langues": """<p>Ce thème réunit des marchés de nature différente : des cours (anglais professionnel, français langue étrangère, langue des signes, langues régionales), des achats de tests et de certifications (TOEIC, Linguaskill), qui intéressent surtout les centres agréés, et des ateliers linguistiques pour des publics en insertion. Dans nos données, les achats de tests et de certifications viennent surtout d'universités et d'écoles.</p>""",
        "sante-medico-social": f"""<p>Les départements sont les premiers acheteurs de ce thème (formation obligatoire des assistants maternels et familiaux, protection de l'enfance, analyse des pratiques), suivis des communes et de leurs CCAS, des établissements de santé et de l'ANFH, qui achète des formations « FPTLV » destinées aux agents de la fonction publique hospitalière (<a href="https://ted.europa.eu/fr/notice/-/detail/39935-2026">exemple d'avis de l'ANFH</a>). Pour les professionnels de santé, le développement professionnel continu (DPC) est une obligation triennale : au moins deux actions de deux types différents par période de 3 ans, choisies parmi les actions publiées par l'Agence nationale du DPC (<a href="https://www.agencedpc.fr/le-dpc-en-pratique">ANDPC, le DPC en pratique</a>). Les lots « pouvant s'intégrer dans un parcours DPC » de l'ANFH en tiennent compte.</p>""",
        "insertion-emploi-poec": f"""<p>Les acheteurs sont ici les régions (programmes régionaux de formation pour les demandeurs d'emploi), les départements (allocataires du RSA), les intercommunalités qui portent un PLIE, les OPCO et France Travail. La préparation opérationnelle à l'emploi collective (POEC) en est un bon exemple : elle est montée quand une branche professionnelle, ou à défaut son OPCO, a identifié des besoins de recrutement, et dure au plus 400 heures avec une période en entreprise (<a href="https://travail-emploi.gouv.fr/la-preparation-operationnelle-lemploi-poe">ministère du Travail, la POE</a>). Des lots demandent aussi de préparer le certificat CléA, créé par Certif'Pro pour les salariés et demandeurs d'emploi sans diplôme (<a href="https://www.certificat-clea.fr/">certificat-clea.fr</a>).</p>""",
    }
    has_source = slug in ("securite-sst-incendie", "sante-medico-social", "insertion-emploi-poec")
    note = (f'<p class="small muted">Sources officielles lues le {checked}. Ce bloc ne remplace pas le règlement de la consultation de chaque marché.</p>'
            if has_source else
            f'<p class="small muted">Constat tiré des avis eux-mêmes (relus le {checked}), pas d\'une règle officielle.</p>')
    return blocks[slug] + note


def facet_table(slug: str, year: list[Notice]) -> str:
    rows = []
    for label, rx in THEME_SEO[slug]["facets"]:
        pat = re.compile(rx)
        n = sum(1 for t in year if any(pat.search(s) for s in taxonomy.segments(taxonomy.theme_text(t))))
        rows.append(f'<tr><th scope="row">{e(label)}</th><td>{n}</td><td>{pct(n, len(year))}</td></tr>')
    return (f'<div class="table-wrap"><table class="region-table facets"><caption class="sr-only">Contenu des avis</caption>'
            f'<thead><tr><th scope="col">Ce que l\'objet ou un lot mentionne</th><th scope="col">Avis</th><th scope="col">Part</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="small muted">Un même avis peut cocher plusieurs lignes.</p>')


def cpv_table(year: list[Notice], cpv_labels: dict[str, str], top: int = 8) -> str:
    c = Counter(code for t in year for code in dict.fromkeys(x[:8] for x in t.cpv))
    none = sum(1 for t in year if not t.cpv)
    rows = "".join(f'<tr><th scope="row">{code}</th><td class="txt">{e(cpv_labels.get(code, ""))}</td><td>{n}</td></tr>'
                   for code, n in c.most_common(top))
    return (f'<div class="table-wrap"><table class="region-table"><caption class="sr-only">Codes CPV</caption>'
            f'<thead><tr><th scope="col">Code CPV</th><th scope="col" class="txt">Libellé officiel</th><th scope="col">Avis</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><p class="small muted">{plural(none, "avis n\'indique", "avis n\'indiquent")} aucun code CPV.</p>')


# CPV codes that name the subject of each theme (official CPV 2008); a theme without one has no line.
SPECIALISED_CPV: dict[str, tuple[str, ...]] = {
    "securite-sst-incendie": ("80550000", "80330000", "80562000"),
    "conduite-caces-permis": ("80411000", "80411100", "80411200"),
    "management-rh": ("80532000",),
    "numerique-bureautique-ia": ("80533000", "80533100", "80533200"),
    "langues": ("80580000",),
    "sante-medico-social": ("80560000", "80561000", "80320000"),
}


def specialised_line(slug: str, year: list[Notice]) -> str:
    codes = SPECIALISED_CPV.get(slug)
    if not codes or not year:
        return ""
    k = sum(1 for t in year if any(c[:8] in codes for c in t.cpv))
    return (f"{pct(k, len(year))} de ces avis seulement portent un code spécialisé du thème ({', '.join(codes)}) : "
            "une alerte sur ces seuls codes laisse passer les autres. ")


def theme_pages(site, open_items: list[Notice], cpv_labels: dict[str, str]) -> dict[str, stats.IndexDecision]:
    """Build the published theme pages that pass (or once passed) the index rule. Returns {slug: decision}."""
    today = site.now.astimezone(PARIS).date()
    built: dict[str, stats.IndexDecision] = {}
    rows = []
    for slug, th in taxonomy.THEMES.items():
        if not th.published:
            continue
        scope = [t for t in site.tenders if th.matches(t)]
        decision = stats.index_decision(scope, today)
        if not decision.ever:
            continue  # never built a page that never passed the rule
        built[slug] = decision
        rows.append((slug, th, scope, decision))
    for slug, th, scope, decision in rows:
        path = f"theme/{slug}/"
        site.decisions[path] = decision
        t_open = [t for t in open_items if t in scope]
        closed = stats.closed_recently(scope, site.now)
        fig = stats.figures(scope, today, top=10)
        year = stats.last12(scope, today)
        reg_rows = []
        for r in REGIONS:
            ro = sum(1 for t in t_open if r in t.regions and not is_national(t))
            ry = sum(1 for t in year if r in t.regions and not is_national(t))
            reg_rows.append(f'<tr><th scope="row"><a href="{site.link(f"region/{slugify(r)}/")}">{e(r)}</a></th><td>{ro}</td><td>{ry}</td></tr>')
        reg_rows.append(f'<tr><th scope="row">Nationales ou multi-régions</th><td>{sum(1 for t in t_open if is_national(t))}</td>'
                        f'<td>{sum(1 for t in year if is_national(t))}</td></tr>')
        prec = taxonomy.PRECISION.get(slug)
        prec_txt = (f"Sur un échantillon relu à la main, {prec[0]} des {prec[1]} avis classés dans ce thème en relevaient vraiment "
                    f"({pct(prec[0], prec[1])}) : <a href=\"{EVALUATION_URL}\">évaluation détaillée</a>." if prec else "")
        today_label = fmt_date(today.isoformat())
        cta = alert_cta(site, f"Recevoir les nouveaux avis « {th.keyword} » chaque matin",
                        f"Indiquez « {e(th.keyword)} » (ou vos propres mots-clés) et vos régions dans le formulaire : l'e-mail ne contient que les avis qui les mentionnent, lots compris.",
                        site.link("alertes/"))
        other_themes = ", ".join(f'<a href="{site.link(f"theme/{s}/")}">{e(taxonomy.THEMES[s].label)}</a>' for s in built if s != slug)
        body = f"""
{crumbs(site, [("Accueil", ""), ("Thèmes", "theme/"), (th.label_cap, None)])}
<h1>Appels d'offres de formation {e(th.h1)}</h1>
<p class="lead"><strong>{plural(len(t_open), "appel", "appels")} d'offres {'ouvert' if len(t_open) <= 1 else 'ouverts'}</strong> au {today_label} pour des formations {e(th.label)}, et {plural(fig.n12, "avis publié", "avis publiés")} en 12 mois au BOAMP et dans TED{f", avec un délai médian de {fig.median_days} jours pour répondre" if fig.median_days else ""}.</p>
<p class="small muted">Un avis est classé ici si son objet ou l'un de ses lots parle de ce thème (règles publiques, sans IA). {prec_txt}</p>
{index_note(decision)}
<section class="section" aria-labelledby="ouverts-h">
<h2 id="ouverts-h">Ouverts aujourd'hui ({len(t_open)})</h2>
{site.cards(t_open)}
</section>
{cta}
<section class="section" aria-labelledby="contenu-h">
<h2 id="contenu-h">Ce que les acheteurs commandent ({fig.n12} avis en 12 mois)</h2>
{facet_table(slug, year)}
{theme_editorial(site, slug)}
</section>
<section class="section" aria-labelledby="acheteurs-h">
<h2 id="acheteurs-h">Qui achète ces formations</h2>
{buyers_list(fig)}
<p>{structure_line(fig)}</p>
{months_table(fig, "Avis publiés par mois")}
</section>
<section class="section" aria-labelledby="regions-h">
<h2 id="regions-h">Par région</h2>
<div class="table-wrap"><table class="region-table"><caption class="sr-only">Avis par région</caption>
<thead><tr><th scope="col">Région</th><th scope="col">Ouverts</th><th scope="col">12 mois</th></tr></thead>
<tbody>{''.join(reg_rows)}</tbody></table></div>
</section>
<section class="section" aria-labelledby="cpv-h">
<h2 id="cpv-h">Codes CPV utilisés par les acheteurs</h2>
{cpv_table(year, cpv_labels)}
<p class="small">{specialised_line(slug, year)}Détail dans notre <a href="{site.link('guide/codes-cpv-formation/')}">guide des codes CPV de la formation</a>.</p>
</section>
<section class="section" aria-labelledby="clos-h">
<h2 id="clos-h">Clôturés ces 90 derniers jours ({len(closed)})</h2>
{notice_table(closed, caption="Avis clôturés ces 90 derniers jours", region=True)}
</section>
<p class="region-line"><span>Autres thèmes :</span> {other_themes}.</p>
"""
        modified = site.content_date(scope)
        jsonld = [collection_ld(site, path, f"Appels d'offres de formation {th.h1}", t_open, modified),
                  breadcrumb_ld(site, [("Accueil", ""), ("Thèmes", "theme/"), (th.label_cap, path)])]
        n = len(t_open)
        site.write(path, site.page(
            path, THEME_SEO[slug]["title"],
            f"{n} appel{'s' if n > 1 else ''} d'offres de formation {th.label} {'ouverts' if n > 1 else 'ouvert'}, "
            f"{fig.n12} en 12 mois (BOAMP, TED) : acheteurs, lots, dates limites, liens officiels.",
            body, jsonld=jsonld, sticky=True, noindex=decision.robots), sitemap=decision.index, lastmod=modified)

    # hub
    hub_rows = []
    for slug, th, scope, decision in rows:
        o = sum(1 for t in open_items if t in scope)
        hub_rows.append(f'<tr><th scope="row"><a href="{site.link(f"theme/{slug}/")}">{e(th.label_cap)}</a></th>'
                        f'<td>{o}</td><td>{decision.n12}</td></tr>')
    body = f"""
{crumbs(site, [("Accueil", ""), ("Thèmes", None)])}
<h1>Appels d'offres de formation par thème</h1>
<p class="lead">Les appels d'offres de formation ouverts et ceux des 12 derniers mois, classés en {len(rows)} thèmes à partir de l'objet et des lots de chaque avis. Un marché alloti peut figurer dans plusieurs thèmes.</p>
<div class="table-wrap"><table class="region-table">
<thead><tr><th scope="col">Thème</th><th scope="col">Ouverts aujourd'hui</th><th scope="col">Publiés en 12 mois</th></tr></thead>
<tbody>{''.join(hub_rows)}</tbody></table></div>
<p class="small muted">Le classement repose sur des règles publiques (mots et expressions de l'objet et des lots, jamais le nom de l'acheteur), sans IA. Avant de publier un thème, nous avons relu à la main des échantillons d'avis : chaque thème publié dépasse 90 % de précision. <a href="{EVALUATION_URL}">Évaluation détaillée</a> · <a href="{site.link('methode/')}">méthode</a>.</p>
<p class="region-line"><span>Par région :</span> <a href="{site.link('region/')}">les 14 pages régionales</a>. <span>Par acheteur :</span> <a href="{site.link('acheteur/opco/')}">OPCO</a>.</p>
"""
    site.write("theme/", site.page("theme/", "Appels d'offres formation par thème",
                                   "Appels d'offres de formation par thème : sécurité et SST, CACES et permis, management, "
                                   "numérique et IA, langues, santé, insertion. Ouverts et 12 derniers mois.", body,
                                   jsonld=breadcrumb_ld(site, [("Accueil", ""), ("Thèmes", "theme/")])),
               lastmod=site.content_date(t for _, _, scope, _ in rows for t in scope))
    return built


# --- OPCO ---------------------------------------------------------------------------------------------------

def opco_page(site, open_items: list[Notice]) -> None:
    path = "acheteur/opco/"
    today = site.now.astimezone(PARIS).date()
    scope = [t for t in site.tenders if taxonomy.opco_of(t)]
    decision = stats.index_decision(scope, today)
    site.decisions[path] = decision
    year = stats.last12(scope, today)
    t_open = [t for t in open_items if t in scope]
    today_label = fmt_date(today.isoformat())
    def mentions(rx: str) -> int:
        pat = re.compile(rx)
        return sum(1 for t in year if any(pat.search(s) for s in taxonomy.segments(taxonomy.theme_text(t))))
    poec = mentions(r"\bpoec?\b|preparations? operationnelles?")
    collective = mentions(r"actions? collectives?|\bacm\b|\bpar\b|catalogue|campus ?atlas|click ?& ?form|"
                          r"offres? de formations? collectives?|actions? mutualisees?|plans? d'actions mutualise")
    rows, sections = [], []
    for o in taxonomy.OPCOS:
        oy = sorted((t for t in year if taxonomy.opco_of(t) is o), key=lambda t: t.published, reverse=True)
        oo = [t for t in t_open if taxonomy.opco_of(t) is o]
        last = fmt_date(oy[0].published) if oy else "–"
        rows.append(f'<tr><th scope="row"><a href="#{o.slug}">{e(o.name)}</a></th><td class="txt sector">{e(o.sector)}</td>'
                    f'<td>{len(oy)}</td><td>{len(oo)}</td><td class="txt">{e(last)}</td></tr>')
        listing = ("".join(f'<li>{e(fmt_date(t.published))} · <a href="{e(t.url)}" rel="noopener">{e(t.title)}</a> <span class="muted">'
                           f'{"– ouvert jusqu’au " + e(fmt_deadline(t.deadline)) if t.is_open(site.now) else ""}</span></li>'
                           for t in oy)
                   or "<li>Aucun avis de formation au BOAMP ou dans TED sur les 12 derniers mois.</li>")
        sections.append(f"""<section class="section opco" id="{o.slug}" aria-labelledby="{o.slug}-h">
<h2 id="{o.slug}-h">{e(o.name)}</h2>
<p>{e(o.name)} est l'opérateur de compétences du secteur « {e(o.sector)} ». En 12 mois : {plural(len(oy), "avis publié", "avis publiés")}, dont {plural(len(oo), "ouvert", "ouverts")} aujourd'hui. Site officiel : <a href="{e(o.site)}">{e(o.site.split('//')[1].rstrip('/'))}</a>.</p>
<ul class="links">{listing}</ul>
</section>""")
    cta = alert_cta(site, "Recevoir chaque matin les nouveaux avis des OPCO",
                    "Mettez « OPCO » ou le nom d'un opérateur en mot-clé : l'alerte cherche aussi dans le nom de l'acheteur.",
                    site.link("alertes/"))
    body = f"""
{crumbs(site, [("Accueil", ""), ("Acheteurs", None), ("OPCO", None)])}
<h1>Appels d'offres de formation des OPCO</h1>
<p class="lead">Les 11 opérateurs de compétences ont publié <strong>{plural(len(year), "appel", "appels")} d'offres de formation</strong> au BOAMP et dans TED en 12 mois, dont {len(t_open)} {'ouvert' if len(t_open) <= 1 else 'ouverts'} au {today_label}. Tableau par OPCO, puis le détail de chacun.</p>
{index_note(decision)}
<section class="section" aria-labelledby="ouverts-h">
<h2 id="ouverts-h">Ouverts aujourd'hui ({len(t_open)})</h2>
{site.cards(t_open)}
</section>
<div class="table-wrap"><table class="region-table opco-table"><caption class="sr-only">Avis par OPCO</caption>
<thead><tr><th scope="col">OPCO</th><th scope="col" class="txt sector">Secteur</th><th scope="col">12 mois</th><th scope="col">Ouverts</th><th scope="col" class="txt">Dernier avis</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{cta}
<section class="section" aria-labelledby="quoi-h">
<h2 id="quoi-h">Pourquoi un OPCO passe des marchés de formation</h2>
<p>Onze opérateurs de compétences ont été agréés le 1er avril 2019 à la place des anciens OPCA. Ils financent l'apprentissage, aident les branches professionnelles à construire leurs certifications et appuient les TPE et PME dans la définition de leurs besoins de formation (<a href="https://travail-emploi.gouv.fr/les-operateurs-de-competences-opco">ministère du Travail, les OPCO</a>, page mise à jour le 30 septembre 2026). Les secteurs et sites officiels du tableau viennent de cette page.</p>
<p>Sur leurs {len(year)} avis des 12 derniers mois, {collective} achètent des actions collectives ou des catalogues de formation pour les entreprises adhérentes, et {poec} une préparation opérationnelle à l'emploi collective (POEC). La POEC est montée quand une branche ou son OPCO a identifié des besoins de recrutement, et dure au plus 400 heures (<a href="https://travail-emploi.gouv.fr/la-preparation-operationnelle-lemploi-poe">ministère du Travail, la POE</a>). Quelques avis achètent la formation des salariés de l'OPCO lui-même.</p>
<p class="small muted">Sources officielles lues le {fmt_date(EDITORIAL_CHECKED)}. Cette page ne montre que les avis publiés au BOAMP et dans TED ; un OPCO peut aussi consulter des prestataires sur son propre profil d'acheteur.</p>
</section>
{''.join(sections)}
<p class="region-line"><span>Voir aussi :</span> <a href="{site.link('theme/insertion-emploi-poec/')}">insertion et POEC</a>, <a href="{site.link('guide/appels-offres-france-travail-formation/')}">France Travail</a>, <a href="{site.link('region/')}">les régions</a>.</p>
"""
    modified = site.content_date(scope)
    jsonld = [collection_ld(site, path, "Appels d'offres de formation des OPCO", t_open, modified),
              breadcrumb_ld(site, [("Accueil", ""), ("OPCO", path)])]
    site.write(path, site.page(
        path, "Appels d'offres OPCO formation : les 11 opérateurs de compétences",
        f"Appels d'offres de formation des 11 OPCO (Atlas, Santé, EP, 2i, Akto, Constructys…) : {len(year)} avis en 12 mois, "
        f"{len(t_open)} ouverts, avec le détail par OPCO. BOAMP et TED.",
        body, jsonld=jsonld, sticky=True, noindex=decision.robots), sitemap=decision.index, lastmod=modified)


def theme_line(site, built: dict[str, stats.IndexDecision], open_items: list[Notice]) -> str:
    """Home page: one link per theme page with its count of open notices."""
    if not built:
        return ""
    links = []
    for slug in built:
        th = taxonomy.THEMES[slug]
        k = sum(1 for t in open_items if th.matches(t))
        links.append(f'<a href="{site.link(f"theme/{slug}/")}">{e(th.label)}</a>&nbsp;({k})')
    return f'<p class="region-line"><span>Par thème :</span> {", ".join(links)}. <span>Par acheteur :</span> <a href="{site.link("acheteur/opco/")}">OPCO</a>.</p>'


def editorial_age_warning(today: date) -> str | None:
    age = (today - date.fromisoformat(EDITORIAL_CHECKED)).days
    if age > EDITORIAL_REVIEW_DAYS:
        return (f"editorial blocks were last checked on {EDITORIAL_CHECKED} ({age} days ago): re-read their sources "
                "and update EDITORIAL_CHECKED in veille/datapages.py")
    return None
