"""Two written guides built on our data: France Travail as a buyer of training, and the CPV codes of training.

The prose is written once; every count in it is computed at build time from the stored notices, so the pages stay
true as the data moves. Sources were read on datapages.EDITORIAL_CHECKED; claims we could not check are not made.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from . import stats, taxonomy
from .datapages import (EDITORIAL_CHECKED, alert_cta, breadcrumb_ld, crumbs, e, nb, notice_table, pct, plural)
from .digest import is_national
from .exporters import fmt_date, fmt_deadline
from .model import PARIS, Notice, fold

ROOT = Path(__file__).resolve().parent.parent
CPV_FILE = ROOT / "data" / "cpv_fr.json"
GUIDE_PUBLISHED = "2026-10-17"

FT_ACHATS_URL = "https://www.francetravail.org/accueil/actualites/2025/organisation-et-segmentation-des-achats-de-france-travail.html?type=article"
FT_MISSIONS_URL = "https://www.francetravail.org/francetravail/mission/nos-missions.html?type=article"
FT_OF_URL = "https://www.francetravail.org/acteursemploi/organismes-de-formation.html"
PLACE_URL = "https://www.marches-publics.gouv.fr/entreprise"
BOAMP_CGU_URL = "https://www.boamp.fr/telechargements/cms/fichiers/CGU_site_boamp.fr_et_service_d_alerte_BOAMP_2024-10-21.pdf"
BOAMP_SEARCH_URL = "https://www.boamp.fr/pages/recherche/"
TED_SEARCH_DOC_URL = "https://docs.ted.europa.eu/api/latest/search.html"
CPV_REG_URL = "https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32008R0213"
CPV_FILE_URL = "https://ted.europa.eu/documents/d/ted/cpv_2008_xls"

# Generic training codes: they say « training », not which training.
GENERIC_CPV = ("80000000", "80500000", "80510000", "80511000", "80530000", "79632000", "79633000")


def load_cpv_labels() -> dict[str, str]:
    """{'80530000': 'Services de formation professionnelle', …} from the official CPV 2008 file (FR)."""
    if not CPV_FILE.exists():
        return {}
    raw = json.loads(CPV_FILE.read_text(encoding="utf-8"))["labels"]
    return {k[:8]: v for k, v in raw.items()}


def cpv_with_digit() -> dict[str, str]:
    if not CPV_FILE.exists():
        return {}
    return {k[:8]: k for k in json.loads(CPV_FILE.read_text(encoding="utf-8"))["labels"]}


def author_box(site, updated: str) -> str:
    return f"""<aside class="author-box" aria-label="À propos de ce guide">
<p><strong>Jalonel</strong>, marque de READ FRANCE SAS (Paris). Publié le {fmt_date(GUIDE_PUBLISHED)}, chiffres mis à jour le {fmt_date(updated)} à partir des avis BOAMP et TED de nos données.</p>
<p class="small">Rédigé avec l'aide d'Octave, l'assistant IA de READ FRANCE, à partir des sources officielles citées, lues le {fmt_date(EDITORIAL_CHECKED)}. Directeur de la publication : Tanguy Warsmann. Ce guide n'est pas un conseil juridique : l'avis officiel et le dossier de consultation font foi.</p>
</aside>"""


def article_ld(site, path: str, headline: str, description: str, modified: str) -> dict:
    return {"@context": "https://schema.org", "@type": "Article", "headline": headline, "description": description,
            "inLanguage": "fr-FR", "url": site.url + path, "mainEntityOfPage": site.url + path,
            "datePublished": GUIDE_PUBLISHED, "dateModified": modified,
            "author": site.org_ref, "publisher": site.org_ref, "image": site.url + "static/og.png"}


# --- France Travail -----------------------------------------------------------------------------------------

FT_KINDS = [
    ("Mobilité Erasmus+ de demandeurs d'emploi", r"erasmus"),
    ("Prestations d'insertion professionnelle pour demandeurs d'emploi",
     r"insertion professionnelle|agil|destination emploi|activ'? ?frontalier|tremplin|redynamisation|defi sante"),
    ("Formation de ses propres agents", r"manag|leadership|formations? securite"),
    ("Formation de demandeurs d'emploi", r"formation"),
]


def ft_kind(t: Notice) -> str:
    text = fold(t.title.replace("’", "'"))
    return next(label for label, rx in FT_KINDS if re.search(rx, text) or label == FT_KINDS[-1][0])


def france_travail_guide(site, open_items: list[Notice]) -> None:
    path = "guide/appels-offres-france-travail-formation/"
    today = site.now.astimezone(PARIS).date()
    scope = sorted((t for t in site.tenders if taxonomy.is_france_travail(t)), key=lambda t: t.published, reverse=True)
    year = stats.last12(scope, today)
    t_open = [t for t in open_items if t in scope]
    this_year = [t for t in scope if t.published[:4] == str(today.year) and t.status != "annule"]
    overseas = [t for t in year if "Outre-mer" in t.regions]
    kinds = Counter(ft_kind(t) for t in year)
    kind_rows = "".join(
        f'<tr><th scope="row">{e(label)}</th><td>{kinds[label]}</td></tr>' for label, _ in FT_KINDS if kinds[label])
    buyers = Counter(t.buyer.strip() for t in year)
    buyer_rows = "".join(f"<li>{e(b)} <span class=\"muted\">({n})</span></li>" for b, n in buyers.most_common())
    values = sorted(t.estimated_value for t in year if t.estimated_value)
    today_label = fmt_date(today.isoformat())
    open_html = site.cards(t_open) if t_open else (
        "<p class=\"empty\">Aucun avis de France Travail n'est ouvert aujourd'hui au BOAMP ou dans TED.</p>")
    overseas_html = notice_table(overseas, caption="Avis de France Travail en outre-mer, 12 mois") if overseas else \
        "<p class=\"empty\">Aucun avis de France Travail en outre-mer sur les 12 derniers mois.</p>"
    cta = alert_cta(site, "Être prévenu des nouveaux avis de France Travail",
                    "Mettez « France Travail » en mot-clé : l'alerte cherche aussi dans le nom de l'acheteur, et vous recevez au passage les autres avis de formation de vos régions.",
                    site.link("alertes/"))
    modified = site.content_date(scope)
    desc = (f"France Travail publie ses avis sur PLACE ; {len(year)} avis de formation ou d'insertion au BOAMP et dans TED en "
            "12 mois. Lesquels, quelles directions régionales, ceux ouverts aujourd'hui.")
    body = f"""
{crumbs(site, [("Accueil", ""), ("Guides", None), ("France Travail", None)])}
<article class="guide">
<h1>Appels d'offres de formation de France Travail</h1>
<p class="lead">France Travail publie ses avis de marchés sur PLACE, la plateforme des achats de l'État, sous forme de marchés nationaux ou régionaux (<a href="{FT_ACHATS_URL}">francetravail.org, organisation des achats, 6 mars 2025</a>). Au BOAMP et dans TED, que nous relisons deux fois par jour, nous comptons <strong>{plural(len(year), "avis", "avis")} de France Travail</strong> dans le champ de la formation et de l'insertion sur les 12 derniers mois, dont {len(t_open)} {'ouvert' if len(t_open) <= 1 else 'ouverts'} au {today_label}.</p>
<p>C'est peu pour un acteur qui, selon ses propres termes, compte « aux côtés des Conseils régionaux » parmi les principaux financeurs de formations pour les demandeurs d'emploi (<a href="{FT_MISSIONS_URL}">francetravail.org, nos missions</a>). Nous ne savons pas quelle part de ses achats de formation passe par un avis au BOAMP ou au JOUE : un avis publié seulement sur PLACE n'apparaît pas dans nos données.</p>

<h2 id="ouverts">Ouverts aujourd'hui ({len(t_open)})</h2>
{open_html}

<h2 id="quoi">Ce que France Travail achète par appel d'offres</h2>
<p>Sa direction des achats classe ses achats en six domaines : accompagnement des demandeurs d'emploi, accompagnement des entreprises, actions de formation collectives, prestations intellectuelles et services, travaux, systèmes d'information (<a href="{FT_ACHATS_URL}">même source</a>). Les avis de nos 12 derniers mois se répartissent ainsi :</p>
<div class="table-wrap"><table class="region-table"><caption class="sr-only">Avis de France Travail par type</caption>
<thead><tr><th scope="col">Type d'achat (d'après l'objet de l'avis)</th><th scope="col">Avis en 12 mois</th></tr></thead>
<tbody>{kind_rows}</tbody></table></div>
<p>{f"Quand l'avis publie un montant ({len(values)} sur {len(year)}), il va de {nb(values[0])} à {nb(values[-1])} € HT. " if values else ""}Les marchés Erasmus+ préparent et accompagnent la mobilité de demandeurs d'emploi dans un autre pays européen ; les prestations d'insertion (« Agil'Cadres », « Destination Emploi »…) sont des accompagnements vers l'emploi plutôt que des formations.</p>

<h2 id="qui">Qui publie : la direction nationale et les directions régionales</h2>
<p>Noms d'acheteurs tels qu'ils figurent dans les avis des 12 derniers mois :</p>
<ul class="rank">{buyer_rows}</ul>

<h2 id="outre-mer">France Travail en outre-mer : La Réunion, Guadeloupe, Martinique</h2>
<p>{plural(len(overseas), "avis de France Travail concerne", "avis de France Travail concernent")} l'outre-mer sur les 12 derniers mois. Ils figurent aussi sur notre <a href="{site.link('region/outre-mer/')}">page Outre-mer</a>, territoire par territoire.</p>
{overseas_html}

<h2 id="suivre">Suivre les avis de France Travail</h2>
<ul>
<li><strong>Sur PLACE</strong>, où France Travail publie : l'espace entreprises propose une recherche et la gestion d'« alertes et recherches sauvegardées » (<a href="{PLACE_URL}">marches-publics.gouv.fr</a>, consulté le {fmt_date(EDITORIAL_CHECKED)}).</li>
<li><strong>Au BOAMP</strong>, gratuitement, avec un compte (jusqu'à 10 alertes, <a href="{BOAMP_CGU_URL}">conditions d'utilisation de la DILA</a>) : cherchez « France Travail » dans le texte de l'annonce ou en acheteur.</li>
<li><strong>Ici</strong>, les avis BOAMP et TED de France Travail apparaissent dans la <a href="{site.link()}">liste gratuite</a>, sur les pages régionales et sur la page <a href="{site.link('theme/insertion-emploi-poec/')}">insertion et emploi</a>.</li>
</ul>
<p>Pour la relation avec les organismes de formation hors marchés publics (l'outil Kairos, les actualités de son offre de formation), France Travail renvoie vers son site ActuFormation (<a href="{FT_OF_URL}">francetravail.org, organismes de formation</a>).</p>
{cta}

<h2 id="questions">Questions fréquentes</h2>
<details class="faq-item" id="faq-2026"><summary>Y a-t-il des appels d'offres de formation France Travail en {today.year} ?</summary>
<p>Oui : {plural(len(this_year), "avis publié", "avis publiés")} depuis le 1er janvier {today.year} au BOAMP et dans TED, dont {len(t_open)} encore {'ouvert' if len(t_open) <= 1 else 'ouverts'}. Ils sont listés plus haut ; la liste se met à jour deux fois par jour.</p></details>
<details class="faq-item" id="faq-dom"><summary>Où trouver les appels d'offres France Travail à La Réunion (974), en Guadeloupe ou en Martinique ?</summary>
<p>Sur PLACE pour l'ensemble, et ici pour ceux qui passent au BOAMP ou dans TED : section « outre-mer » ci-dessus et <a href="{site.link('region/outre-mer/')}">page Outre-mer</a>.</p></details>
<details class="faq-item" id="faq-hors"><summary>Pourquoi certains marchés de France Travail n'apparaissent-ils pas ici ?</summary>
<p>Nous ne lisons que le BOAMP et TED. Un marché publié seulement sur PLACE, ou un marché à procédure adaptée annoncé ailleurs qu'au BOAMP, n'y figure pas ; c'est la principale limite de ce guide.</p></details>

<h2 id="historique">Tous les avis de France Travail de nos données ({len(scope)})</h2>
{notice_table(scope, caption="Avis de France Travail", region=True)}
<p class="small muted">Avis dont l'acheteur est France Travail (ou Pôle emploi), sélectionnés comme tous nos avis : codes CPV de formation, descripteur BOAMP « Formation » ou mot-clé de formation. <a href="{site.link('methode/')}">Méthode</a>.</p>
</article>
{author_box(site, modified)}
<p class="region-line"><span>Voir aussi :</span> <a href="{site.link('acheteur/opco/')}">les appels d'offres des OPCO</a>, <a href="{site.link('guide/codes-cpv-formation/')}">les codes CPV de la formation</a>, <a href="{site.link('region/')}">les régions</a>.</p>
"""
    site.write(path, site.page(
        path, "Appels d'offres France Travail formation : où et quand ils sont publiés", desc, body,
        jsonld=[article_ld(site, path, "Appels d'offres de formation de France Travail", desc, modified),
                breadcrumb_ld(site, [("Accueil", ""), ("France Travail", path)])], sticky=True),
        lastmod=modified)


# --- CPV ------------------------------------------------------------------------------------------------------

def cpv_guide(site) -> None:
    path = "guide/codes-cpv-formation/"
    today = site.now.astimezone(PARIS).date()
    labels = load_cpv_labels()
    digits = cpv_with_digit()
    year = stats.last12(site.tenders, today)
    n = len(year)
    codes = Counter(c for t in year for c in dict.fromkeys(x[:8] for x in t.cpv))
    no_cpv = [t for t in year if not t.cpv]
    training = lambda c: c.startswith(("804", "805", "806")) or c in ("80000000", "79632000", "79633000")  # noqa: E731
    with_training = [t for t in year if any(training(c[:8]) for c in t.cpv)]
    generic_only = [t for t in with_training if all(c[:8] in GENERIC_CPV for c in t.cpv if training(c[:8]))]
    by_descriptor = sum(1 for t in year if "descripteur" in t.match)
    keyword_only = sum(1 for t in year if t.match == ["mot-cle"])
    top4 = [c for c, _ in codes.most_common() if training(c)][:4]
    with_top4 = sum(1 for t in year if any(c[:8] in top4 for c in t.cpv))
    mapa = [t for t in year if t.family == "Marchés <90 k€ (MAPA)"]
    mapa_no_cpv = sum(1 for t in mapa if not t.cpv)

    in_list = lambda c: c.startswith("80") or c.startswith("7963")  # noqa: E731  (division 80 + group 7963)

    def code_cell(c: str) -> str:
        return e(digits.get(c, c))

    used_rows = "".join(
        f'<tr><th scope="row">{code_cell(c)}</th><td class="txt">{e(labels.get(c, ""))}</td><td>{k}</td><td>{pct(k, n)}</td></tr>'
        for c, k in [(c, k) for c, k in codes.most_common() if in_list(c)][:20])
    other_rows = "".join(
        f'<tr><th scope="row">{code_cell(c)}</th><td class="txt">{e(labels.get(c, ""))}</td><td>{k}</td></tr>'
        for c, k in [(c, k) for c, k in codes.most_common() if not in_list(c)][:8])
    official = sorted(c for c in labels if c.startswith("80") or c.startswith("7963"))
    official_rows = "".join(
        f'<tr><th scope="row">{code_cell(c)}</th><td class="txt">{e(labels[c])}</td><td>{codes.get(c, 0)}</td>'
        f'<td class="txt">{"oui" if training(c) else "non"}</td></tr>' for c in official)
    first = top4[0] if top4 else ""
    modified = site.content_date(site.tenders)
    desc = (f"Les codes CPV des marchés de formation, avec le nombre d'avis qui les utilisent vraiment : {n} appels d'offres "
            "BOAMP et TED des 12 derniers mois. Et comment régler une veille qui ne rate rien.")
    cta = alert_cta(site, "Ou laisser le tri à Veille AO Formation",
                    "Codes CPV, descripteur « Formation » et mots-clés sont déjà combinés : chaque matin, les nouveaux avis de vos régions, sans réglage de codes.",
                    site.link("alertes/"))
    body = f"""
{crumbs(site, [("Accueil", ""), ("Guides", None), ("Codes CPV formation", None)])}
<article class="guide">
<h1>Codes CPV des marchés de formation</h1>
<p class="lead">Sur les {nb(n)} appels d'offres de formation publiés au BOAMP et dans TED en 12 mois, le code le plus utilisé est le <strong>{code_cell(first)}</strong> « {e(labels.get(first, ''))} » ({codes.get(first, 0)} avis, {pct(codes.get(first, 0), n)}). Quatre codes génériques ({", ".join(code_cell(c) for c in top4)}) figurent sur {pct(with_top4, n)} des avis, alors que les codes spécialisés (langues, informatique, sécurité, santé) restent rares. Une veille réglée sur les seuls codes de votre spécialité rate donc la plupart des avis.</p>
<p>Le CPV (vocabulaire commun pour les marchés publics) est la nomenclature européenne des objets de marché, fixée par le <a href="{CPV_REG_URL}">règlement (CE) n° 213/2008</a>. Chaque code a 8 chiffres et un chiffre de contrôle (80530000-8). Les libellés ci-dessous sont ceux du <a href="{CPV_FILE_URL}">fichier officiel publié par l'Office des publications de l'UE</a>.</p>

<h2 id="utilises">Les codes réellement utilisés</h2>
<p>Nombre d'avis des 12 derniers mois qui portent chaque code (un avis peut citer plusieurs codes), un avis par marché.</p>
<div class="table-wrap"><table class="region-table cpv"><caption class="sr-only">Codes CPV de formation utilisés</caption>
<thead><tr><th scope="col">Code</th><th scope="col" class="txt">Libellé officiel</th><th scope="col">Avis</th><th scope="col">Part</th></tr></thead>
<tbody>{used_rows}</tbody></table></div>
<p>Les avis de formation citent aussi des codes d'autres divisions, choisis pour le contexte du marché (accompagnement, conseil, animation, services sociaux) :</p>
<div class="table-wrap"><table class="region-table cpv"><caption class="sr-only">Autres codes CPV présents</caption>
<thead><tr><th scope="col">Code</th><th scope="col" class="txt">Libellé officiel</th><th scope="col">Avis</th></tr></thead>
<tbody>{other_rows}</tbody></table></div>

<h2 id="generiques">Pourquoi une alerte sur les codes spécialisés rate des avis</h2>
<ul>
<li>{pct(len(generic_only), len(with_training))} des avis qui ont un code de formation n'utilisent que des codes génériques ({", ".join(GENERIC_CPV)}) : rien n'y dit s'il s'agit d'anglais, de SST ou de management.</li>
<li>{pct(len(no_cpv), n)} des avis n'ont aucun code CPV ({nb(len(no_cpv))} sur {nb(n)}). Chez les marchés de moins de 90 000 € publiés au BOAMP, c'est {pct(mapa_no_cpv, len(mapa))}.</li>
<li>{pct(by_descriptor, n)} des avis portent le descripteur BOAMP « Formation », et {nb(keyword_only)} ne sont repérables que par un mot de leur objet (formation, formateur, CACES…).</li>
</ul>
<p>Conséquence pratique : pour un organisme spécialisé, le thème se lit dans l'objet et les lots, pas dans le code. C'est ce que font nos <a href="{site.link('theme/')}">pages thématiques</a>.</p>

<h2 id="veille">Régler une veille gratuite au BOAMP et dans TED</h2>
<ol>
<li><strong>BOAMP</strong> : le compte d'alerte est gratuit (création par e-mail ou FranceConnect) et permet jusqu'à 10 alertes (<a href="{BOAMP_CGU_URL}">CGU du site boamp.fr et du service d'alerte, DILA</a>).</li>
<li>Le formulaire de recherche du BOAMP n'a pas de champ CPV (vérifié le {fmt_date(EDITORIAL_CHECKED)}). Utilisez la liste « Mots clés » (375 descripteurs, dont « Formation »), le type de marché « Services » et vos départements (<a href="{BOAMP_SEARCH_URL}">recherche BOAMP</a>).</li>
<li><strong>TED</strong> ne contient que les avis publiés au Journal officiel de l'Union européenne, mais sa recherche accepte les codes CPV, organisés en arborescence (<a href="{TED_SEARCH_DOC_URL}">documentation de l'API de recherche TED</a>). Dans l'API, que nous utilisons, le code 80000000 ramène toute la division 80.</li>
<li>Ajoutez les mots de votre spécialité en texte libre, sur l'objet : c'est là que se trouve l'information que le code ne donne pas.</li>
</ol>
{cta}

<h2 id="liste">La liste officielle des codes de la formation (division 80 et codes 7963)</h2>
<p>Tous les codes de la division 80 « Services d'enseignement et de formation » et du groupe 7963 (gestion du personnel), avec le nombre d'avis qui les citent en 12 mois. La dernière colonne indique si le code suffit à faire retenir un avis par Veille AO Formation (codes 804, 805, 806, 80000000, 79632000 et 79633000) ; les autres codes de la division 80 visent surtout l'enseignement scolaire et supérieur.</p>
<div class="table-wrap"><table class="region-table cpv"><caption class="sr-only">Codes CPV de la formation</caption>
<thead><tr><th scope="col">Code</th><th scope="col" class="txt">Libellé officiel</th><th scope="col">Avis en 12 mois</th><th scope="col" class="txt">Retenu seul</th></tr></thead>
<tbody>{official_rows}</tbody></table></div>
<p class="small muted">Comptage : avis publiés du {fmt_date(stats.figures(site.tenders, today).since)} au {fmt_date(today.isoformat())} au BOAMP ou dans TED, un par marché (copies BOAMP et TED fusionnées, annulés exclus), tous les codes cités dans l'avis. <a href="{site.link('methode/')}">Méthode</a>.</p>
</article>
{author_box(site, modified)}
<p class="region-line"><span>Voir aussi :</span> <a href="{site.link('theme/')}">les appels d'offres par thème</a>, <a href="{site.link('guide/appels-offres-france-travail-formation/')}">France Travail</a>, <a href="{site.link()}">tous les avis ouverts</a>.</p>
"""
    site.write(path, site.page(
        path, "Codes CPV formation : la liste et ceux que les acheteurs utilisent vraiment", desc, body,
        jsonld=[article_ld(site, path, "Codes CPV des marchés de formation", desc, modified),
                breadcrumb_ld(site, [("Accueil", ""), ("Codes CPV formation", path)])], sticky=True),
        lastmod=modified)
