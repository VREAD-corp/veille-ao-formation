"""Static site generator (no dependencies)."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .digest import NATIONAL_THRESHOLD, is_national, render
from .exporters import ATTRIBUTION, fmt_date, fmt_deadline, to_atom, to_json, to_rss
from . import sales as sales_mod
from . import landing, shop
from .geo import REGIONS, slugify
from .model import PARIS, Notice, fold, iso_week_bounds

REPO = "VREAD-corp/veille-ao-formation"
REPO_URL = f"https://github.com/{REPO}"
RELEASE_CSV = f"{REPO_URL}/releases/latest/download/ao-formation-ouverts.csv"
RELEASE_ICS = f"{REPO_URL}/releases/latest/download/ao-formation-echeances.ics"
INTEREST_ISSUE = f"{REPO_URL}/issues/1"
BRAND = "Veille AO Formation"
STATIC = Path(__file__).resolve().parent.parent / "static"
# Anonymous, cookieless audience measurement (verbatim copy of octave-checkout gas/web/mesure.js).
MESURE = Path(__file__).resolve().parent / "mesure.js"
# Aggregate behaviour counters and A/B framework (verbatim copy of octave-checkout gas/web/cro.js), same backend.
CRO = Path(__file__).resolve().parent / "cro.js"
CRO_SITE = "veille-ao-formation"
INDEXNOW_KEY = "5be19f56d46ca3d2553bee28ebfa8edd"
CRO_API = "https://script.google.com/macros/s/AKfycbzD1IRL8QALluKGZxgGiORFP6-DkC_qFJYR9aOxZiimxa06nbCqpzUI-9iLblp6V2VY/exec"
CRO_HOSTS = "ao-formation.jalonel.com outils.vread.fr"
# Time to first CTA = first click on a paid-alerts call to action (links to /alertes/ or #commander-h, pay button).
CRO_CTA = "[data-cta]"

PUBLISHER = {
    "name": "READ FRANCE SAS",
    "siren": "988 741 773",
    "address": "11 rue Rouvet, 75019 Paris",
    "president": "Tanguy Warsmann",
}

# schema.org publisher/brand: Jalonel (hub https://jalonel.com/), a brand of READ FRANCE SAS (the legal operator).
ORG_ID = "https://jalonel.com/#org"
ORG_REF = {"@type": "Organization", "@id": ORG_ID, "name": "Jalonel", "url": "https://jalonel.com/",
           "parentOrganization": {"@type": "Organization", "name": "READ FRANCE SAS", "identifier": "SIREN 988741773"}}
JALONEL_URL = "https://jalonel.com/"

# « Autres outils Jalonel »: the same footer block on every Jalonel site (hub + the paid offers).
NETWORK_LINKS = [
    ("Tous les outils", "https://jalonel.com/"),
    ("Kit Qualiopi V10", "https://qualiopi.jalonel.com/"),
    ("Pack documentation GPSR", "https://gpsr.jalonel.com/pack.html"),
    ("Alertes appels d’offres formation", "https://ao-formation.jalonel.com/alertes/"),
    ("Audit d’accessibilité", "https://accessibilite.jalonel.com/audit/"),
]
NETWORK_HTML = ('<nav class="rf-network" aria-label="Autres outils Jalonel">\n'
                '<p class="rf-network-title">Autres outils Jalonel</p>\n<ul>\n'
                + "\n".join(f'<li><a href="{href}">{label}</a></li>' for label, href in NETWORK_LINKS)
                + "\n</ul>\n</nav>")

# Date of the last change to the page templates (copy, layout). Pages without notice data use it as their
# sitemap lastmod; update it by hand when a template changes.
TEMPLATE_DATE = "2026-10-13"

# "en Normandie", but "dans les Hauts-de-France".
REGION_IN = {"Grand Est": "dans le Grand Est", "Hauts-de-France": "dans les Hauts-de-France",
             "Pays de la Loire": "dans les Pays de la Loire", "Outre-mer": "en outre-mer"}


def region_in(region: str) -> str:
    return REGION_IN.get(region, f"en {region}")


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


FEED_LIMIT = 200
SCRIPT_RE = re.compile(r"(<script\b.*?</script>)", re.S)


def e(text: object) -> str:
    return escape("" if text is None else str(text), quote=True)


def text(value: object) -> str:
    """Escape for <title> and double-quoted attributes, keeping apostrophes literal (d'offres, not d&#x27;offres)."""
    return escape("" if value is None else str(value), quote=False).replace('"', "&quot;")


def fmt_eur(v: float | None) -> str:
    if not v:
        return ""
    return f"{v:,.0f}".replace(",", " ") + " € HT (estimation)"


class Site:
    def __init__(self, out: Path, site_url: str, now: datetime, sales: "sales_mod.Sales | None" = None):
        self.sales = sales or sales_mod.load()
        self.out = out
        self.url = site_url if site_url.endswith("/") else site_url + "/"
        # path prefix for internal links, e.g. "/veille-ao-formation/"
        self.base = urlparse(self.url).path or "/"
        self.now = now
        self.pages: list[tuple[str, str]] = []  # (path, lastmod)
        self.meta: dict = {}

    # -- helpers -------------------------------------------------------------

    def link(self, path: str = "") -> str:
        return self.base + path

    def cro_tag(self) -> str:
        """cro.js (classic deferred script; no A/B experiment on this site, so no OCTAVE_XP), then the goals."""
        return (f'<script src="{self.link("cro.js")}" data-site="{CRO_SITE}" data-api="{CRO_API}" '
                f'data-hosts="{CRO_HOSTS}" data-cta="{CRO_CTA}" defer></script>\n'
                f'<script src="{self.link("static/cro-goals.js")}" defer></script>')

    def write(self, path: str, html: str, *, sitemap: bool = True, lastmod: str | None = None) -> None:
        target = self.out / path
        if path == "" or path.endswith("/"):
            target = target / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        # French typography: keep guillemets attached to their words. Not inside <script> (JSON-LD would
        # carry a literal "&nbsp;"): there, a raw U+00A0 says the same thing.
        html = "".join(
            part.replace("« ", "«\u00a0").replace(" »", "\u00a0»") if part.startswith("<script")
            else part.replace("« ", "«&nbsp;").replace(" »", "&nbsp;»")
            for part in SCRIPT_RE.split(html))
        target.write_text(html, encoding="utf-8")
        if sitemap:
            self.pages.append((path, lastmod or TEMPLATE_DATE))

    def page(self, path: str, title: str, description: str, body: str, *,
             jsonld: dict | list | None = None, scripts: bool = False, noindex: bool | str = False,
             csp: str = "", body_scripts: str = "", sticky: bool = False) -> str:
        canonical = self.url + path
        # "<\/" keeps a notice title containing "</script>" from closing the JSON-LD block.
        ld_json = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
        ld = f'<script type="application/ld+json">{ld_json}</script>' if jsonld else ""
        robots = "noindex" if noindex is True else noindex
        nav = [("", "Annonces"), ("region/", "Régions"), ("semaine/", "Archives"), ("methode/", "Méthode")]
        nav_html = "".join(
            f'<a href="{self.link(p)}"{" aria-current=\"page\"" if p == path else ""}>{label}</a>'
            for p, label in nav)
        alerts_current = ' aria-current="page"' if path == "alertes/" else ""
        sticky_html = ""
        if sticky and self.sales.open:
            sticky_html = landing.sticky_bar(self, "#commander-h" if path == "alertes/" else self.link("alertes/"))
        return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
{f'<meta http-equiv="Content-Security-Policy" content="{e(csp)}">' if csp else ''}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(title)}</title>
<meta name="description" content="{text(description)}">
<link rel="canonical" href="{e(canonical)}">
{f'<meta name="robots" content="{robots}">' if robots else ''}
<meta property="og:type" content="website">
<meta property="og:locale" content="fr_FR">
<meta property="og:site_name" content="{BRAND}">
<meta property="og:title" content="{text(title)}">
<meta property="og:description" content="{text(description)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:image" content="{e(self.url)}static/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b5c63">
<link rel="icon" href="{self.link('static/favicon.svg')}" type="image/svg+xml">
<link rel="alternate" type="application/atom+xml" title="Appels d'offres formation (Atom)" href="{self.link('feed.xml')}">
<link rel="alternate" type="application/rss+xml" title="Appels d'offres formation (RSS)" href="{self.link('rss.xml')}">
<link rel="stylesheet" href="{self.link('static/style.css')}">
<script src="{self.link('mesure.js')}" defer></script>
{self.cro_tag()}
{ld}
</head>
<body{' class="has-sticky"' if sticky_html else ''}>
<a class="skip" href="#contenu">Aller au contenu</a>
<header class="top">
<div class="wrap top-in">
<a class="brand" href="{self.link()}"><span class="brand-mark" aria-hidden="true">AO</span>{BRAND}</a>
<nav class="nav-main" aria-label="Navigation principale">{nav_html}</nav>
<a class="btn btn-sm nav-cta" href="{self.link('alertes/')}"{alerts_current} data-cta>Alertes e-mail</a>
<details class="nav-menu"><summary>Menu</summary><nav aria-label="Navigation principale (mobile)">{nav_html}<a href="{self.link('alertes/')}"{alerts_current}>Alertes e-mail</a></nav></details>
</div>
</header>
<main id="contenu" class="wrap">
{body}
</main>
<footer class="foot">
<div class="wrap">
{NETWORK_HTML}
<p class="attrib">{e(ATTRIBUTION)} Les avis officiels font foi : vérifiez toujours l'avis et le dossier de consultation avant de répondre.</p>
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a></p>
<nav aria-label="Liens de bas de page">
<a href="{self.link('methode/')}">Méthode et sources</a>
<a href="{self.link('mentions-legales/')}">Mentions légales</a>
<a href="{self.link('confidentialite/')}">Confidentialité</a>
{f'<a href="{self.link("cgv-pro/")}">CGV</a>' if self.sales.open else f'<a href="{self.link("cgv/")}">CGV (projet)</a>'}
<a href="{REPO_URL}">Code source</a>
</nav>
<p class="jalonel"><a href="{JALONEL_URL}">Une marque Jalonel</a></p>
</div>
</footer>
{sticky_html}
{f'<script src="{self.link("static/app.js")}" defer></script>' if scripts else ''}
{body_scripts}</body>
</html>
"""

    def card(self, t: Notice, *, heading: str = "h3") -> str:
        days = t.days_left(self.now)
        open_ = t.is_open(self.now)
        if t.status == "annule":
            badge = '<span class="badge closed">Annulé</span>'
        elif not open_:
            badge = '<span class="badge closed">Clôturé</span>'
        elif days is not None and days <= 7:
            badge = f'<span class="badge urgent">J-{days}</span>'
        else:
            badge = f'<span class="badge">J-{days}</span>' if days is not None else ""
        national = is_national(t)
        regions = ("National / plusieurs régions" if t.regions and len(t.regions) >= NATIONAL_THRESHOLD
                   else ", ".join(t.regions) or "Lieu non précisé")
        deps = f" ({', '.join(t.departments[:6])}{'…' if len(t.departments) > 6 else ''})" if t.departments and not national else ""
        meta = []
        if t.cpv_main_label():
            meta.append(f"<span>{e(t.cpv_main_label())}</span>")
        if t.procedure:
            meta.append(f"<span>{e(t.procedure)}</span>")
        if t.estimated_value:
            meta.append(f"<span>{e(fmt_eur(t.estimated_value))}</span>")
        alt = "".join(f' · <a href="{e(u)}" rel="noopener">{e(k.split()[0])}</a>' for k, u in t.alt_urls.items()
                      if not k.startswith(t.source))
        upd = (f" · mis à jour le {fmt_date(t.updated)}" if t.updated and t.updated != t.published else "")
        rect = ' <span class="badge soft">Rectificatif</span>' if t.status == "rectificatif" else ""
        desc = f'<p class="desc">{e(t.description)}</p>' if t.description else ""
        if t.lots:
            shown = " ; ".join(t.lots[:5]) + (f" ; et {len(t.lots) - 5} autre(s)" if len(t.lots) > 5 else "")
            desc += f'<p class="lots"><span>{len(t.lots)} lots :</span> {e(shown)}</p>'
        summary = (f'<p class="desc"><em>Résumé automatique :</em> {e(t.summary)}</p>' if t.summary else "")
        search_text = fold(f"{t.title} {t.buyer} {t.description} {' '.join(t.lots)} {' '.join(t.cpv)} {t.cpv_main_label()} "
                           f"{' '.join(t.regions)} {' '.join(t.departments)}")
        return f"""<article class="card" data-regions="{e('|'.join(t.regions))}" data-national="{int(national)}" data-deadline="{e(t.deadline or '')}" data-search="{e(search_text)}" data-source="{e(t.source)}">
<div class="card-head"><{heading} class="card-title"><a href="{e(t.url)}" rel="noopener">{e(t.title)}</a></{heading}>{badge}</div>
<p class="buyer">{e(t.buyer)}</p>
<dl class="facts">
<div><dt>Date limite</dt><dd>{e(fmt_deadline(t.deadline)) if t.deadline else 'Non précisée'}</dd></div>
<div><dt>Lieu</dt><dd>{e(regions)}{e(deps)}</dd></div>
</dl>
{desc}{summary}
<p class="meta">{' '.join(meta)}</p>
<p class="src">Publié le {fmt_date(t.published)}{upd} · <a href="{e(t.url)}" rel="noopener">Avis officiel {e(t.source)}</a>{alt}{rect}</p>
</article>"""

    def cards(self, items: list[Notice]) -> str:
        if not items:
            return '<p class="empty">Aucun appel d\'offres à afficher.</p>'
        return '<div class="list">' + "\n".join(self.card(t) for t in items) + "</div>"

    # -- pages ---------------------------------------------------------------

    def build(self, tenders: list[Notice], meta: dict) -> None:
        self.meta = meta
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(STATIC, self.out / "static")
        shutil.copyfile(MESURE, self.out / "mesure.js")
        shutil.copyfile(CRO, self.out / "cro.js")
        # IndexNow key (served at the site root) and the custom domain (ignored by Pages workflow builds, kept for clarity).
        (self.out / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY, encoding="utf-8")
        (self.out / "CNAME").write_text(f"{urlparse(self.url).hostname}\n", encoding="utf-8")

        self.tenders = tenders
        open_items = sorted((t for t in tenders if t.is_open(self.now)), key=lambda t: (t.deadline or "", t.id))
        self.vol = landing.volume(tenders, self.now)
        self.sample = landing.sample(open_items, tenders, self.now)
        by_week: dict[str, list[Notice]] = defaultdict(list)
        for t in tenders:
            by_week[t.iso_week()].append(t)

        self.index(open_items, meta)
        self.weeks(by_week)
        self.regions(open_items)
        self.alerts(open_items)
        self.method(meta)
        self.legal()
        if self.sales.open:
            shop.copy_assets(self)
        feed_items = sorted(open_items, key=lambda t: (t.published, t.id), reverse=True)[:FEED_LIMIT]
        (self.out / "feed.xml").write_text(to_atom(feed_items, self.now, self.url), encoding="utf-8")
        (self.out / "rss.xml").write_text(to_rss(feed_items, self.now, self.url), encoding="utf-8")
        (self.out / "data").mkdir(exist_ok=True)
        (self.out / "data" / "ouverts.json").write_text(to_json(open_items, self.now, self.url), encoding="utf-8")
        self.not_found()
        self.sitemap()

    def updated_label(self) -> str:
        stamp = self.meta.get("updated_at")
        local = (datetime.fromisoformat(stamp) if stamp else self.now).astimezone(PARIS)
        return f"{fmt_date(local.date().isoformat())} à {local:%Hh%M}"

    def content_date(self, scope: Iterable[Notice]) -> str:
        """Sitemap lastmod of a list page: the last day its list changed, i.e. the newest publication or update
        of a notice still open, or the day a notice of the same scope closed or was cancelled. Never the build
        date (the site is rebuilt twice a day), never before TEMPLATE_DATE."""
        today = self.now.astimezone(PARIS).date().isoformat()
        best = ""
        for t in scope:
            if t.is_open(self.now):
                d = max(t.published, t.updated or "")
            elif t.status == "annule":
                d = t.updated or t.published
            else:
                dl = t.deadline_dt()
                d = dl.date().isoformat() if dl else ""
            best = max(best, d[:10])
        return min(max(best, TEMPLATE_DATE), today)

    def index(self, items: list[Notice], meta: dict) -> None:
        n = len(items)
        soon = sum(1 for t in items if (t.days_left(self.now) or 99) <= 7)
        new7 = sum(1 for t in items if t.published >= (self.now.date() - timedelta(days=7)).isoformat())
        region_opts = "".join(f'<option value="{e(r)}">{e(r)}</option>' for r in REGIONS)
        sources_note = ""
        failed = [s for s, ok in (meta.get("last_run_ok") or {}).items() if not ok]
        if failed:
            sources_note = (f'<p class="warn" role="status">La dernière mise à jour de {", ".join(failed)} a échoué ; '
                            f'les annonces de cette source peuvent dater de la mise à jour précédente.</p>')
        vol_attr = e(json.dumps({r: [self.vol.for_region(r), slugify(r)] for r in REGIONS},
                                ensure_ascii=False, separators=(",", ":")))
        body = f"""
{landing.home_hero(self, n, self.sample, RELEASE_CSV, RELEASE_ICS, sources_note)}
<ul class="stats" aria-label="Chiffres clés">
<li><strong>{n}</strong><span>appels d'offres ouverts</span></li>
<li><strong>{new7}</strong><span>publiés ces 7 derniers jours</span></li>
<li><strong>{soon}</strong><span>clôturent sous 7 jours</span></li>
</ul>
{self.region_line(items)}

<section aria-labelledby="filtres-titre" class="filters" id="filtres" hidden>
<h2 id="filtres-titre" class="sr-only">Filtrer les annonces</h2>
<div class="field"><label for="q">Mot-clé</label><input id="q" type="search" placeholder="ex. secourisme, anglais, CACES, management" autocomplete="off"></div>
<div class="field"><label for="region">Région</label><select id="region"><option value="">Toutes les régions</option>{region_opts}</select></div>
<div class="field"><label for="deadline">Date limite</label><select id="deadline">
<option value="">Toutes</option>
<option value="lt7">Dans moins de 7 jours</option>
<option value="lt14">Dans moins de 14 jours</option>
<option value="lt30">Dans moins de 30 jours</option>
<option value="gt7">Plus de 7 jours pour répondre</option>
<option value="gt14">Plus de 14 jours pour répondre</option>
</select></div>
<div class="field check"><input id="national" type="checkbox" checked><label for="national">Inclure les annonces nationales ou multi-régions</label></div>
<p id="count" class="count" aria-live="polite"></p>
</section>

<section aria-labelledby="liste-titre" id="liste" data-volume="{vol_attr}" data-days="{landing.VOLUME_DAYS}"{' data-sales="1"' if self.sales.open else ''}>
<h2 id="liste-titre">Annonces en cours, par date limite</h2>
{self.cards(items)}
{landing.inline_cta_template(self) if self.sales.open else ''}
</section>

{landing.home_bottom(self) if self.sales.open else self.soon_panel() + landing.about(self)}
"""
        jsonld = [
            {"@context": "https://schema.org", "@type": "WebSite", "name": BRAND, "url": self.url,
             "inLanguage": "fr-FR", "publisher": ORG_REF},
            {"@context": "https://schema.org", "@type": "Dataset",
             "name": "Appels d'offres publics ouverts pour des prestations de formation (France)",
             "description": "Sélection quotidienne des avis de marchés publics français ouverts dont l'objet est la "
                            "formation (CPV 805xxxxx et apparentés, descripteur BOAMP « Formation »), issus du BOAMP et de TED.",
             "url": self.url, "inLanguage": "fr-FR",
             "isAccessibleForFree": True,
             "license": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
             "isBasedOn": ["https://www.data.gouv.fr/datasets/boamp", "https://ted.europa.eu/"],
             "creator": ORG_REF,
             "dateModified": self.now.date().isoformat(),
             "distribution": [
                 {"@type": "DataDownload", "encodingFormat": "application/json",
                  "contentUrl": self.url + "data/ouverts.json"},
                 {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": RELEASE_CSV},
             ]},
        ]
        self.write("", self.page(
            "", "Appels d'offres formation ouverts – liste gratuite BOAMP + TED",
            f"{n} appels d'offres publics de formation en cours : acheteur, date limite, région, lien vers l'avis "
            "officiel. Mis à jour 2 fois par jour, gratuit.",
            body, jsonld=jsonld, scripts=True, sticky=True), lastmod=self.content_date(self.tenders))

    def region_line(self, items: list[Notice]) -> str:
        """Home page: one link per region page, with its count of open local notices."""
        links = []
        for r in REGIONS:
            k = sum(1 for t in items if r in t.regions and not is_national(t))
            links.append(f'<a href="{self.link(f"region/{slugify(r)}/")}">{e(r)}</a>&nbsp;({k})')
        return f'<p class="region-line"><span>Par région :</span> {", ".join(links)}.</p>'


    def weeks(self, by_week: dict[str, list[Notice]]) -> None:
        weeks = sorted(by_week, reverse=True)
        rows = []
        for i, w in enumerate(weeks):
            items = sorted(by_week[w], key=lambda t: (t.published, t.id), reverse=True)
            start, end = iso_week_bounds(w)
            last = end - timedelta(days=1)
            label = f"du {fmt_date(start.isoformat())} au {fmt_date(last.isoformat())}"
            still_open = sum(1 for t in items if t.is_open(self.now))
            rows.append(f'<li><a href="{self.link(f"semaine/{w}/")}">Semaine {w[-2:]} ({label})</a> — '
                        f'{len(items)} annonce(s), dont {still_open} encore ouverte(s)</li>')
            nav = []
            if i + 1 < len(weeks):
                nav.append(f'<a rel="prev" href="{self.link(f"semaine/{weeks[i + 1]}/")}">← Semaine précédente</a>')
            if i > 0:
                nav.append(f'<a rel="next" href="{self.link(f"semaine/{weeks[i - 1]}/")}">Semaine suivante →</a>')
            body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › <a href="{self.link('semaine/')}">Archives</a> › Semaine {w[-2:]}</nav>
<h1>Appels d'offres formation publiés {label}</h1>
<p class="lead">{len(items)} avis de marchés publics français pour des prestations de formation ont été publiés au BOAMP ou dans TED cette semaine-là (semaine {w[-2:]} de {w[:4]}). {still_open} sont encore ouverts au {fmt_date(self.now.date().isoformat())}.</p>
{self.cards(items)}
<p class="pager">{' '.join(nav)}</p>
"""
            jsonld = {"@context": "https://schema.org", "@type": "CollectionPage",
                      "name": f"Appels d'offres formation – semaine {w}", "url": self.url + f"semaine/{w}/",
                      "mainEntity": {"@type": "ItemList", "numberOfItems": len(items),
                                     "itemListElement": [{"@type": "ListItem", "position": k + 1, "url": t.url,
                                                          "name": t.title} for k, t in enumerate(items[:100])]}}
            self.write(f"semaine/{w}/", self.page(
                f"semaine/{w}/", f"Appels d'offres formation – semaine {w[-2:]} ({label})",
                f"Les {len(items)} appels d'offres publics de formation publiés {label} : acheteurs, dates limites, "
                "régions et liens vers les avis officiels BOAMP et TED.",
                # Thin (2025-W01 holds 2 notices) and duplicated by the home and region pages: kept for
                # visitors, out of the index and of the sitemap; "follow" lets crawlers reach the notices.
                body, jsonld=jsonld, noindex="noindex, follow"), sitemap=False)
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Archives</nav>
<h1>Archives hebdomadaires des appels d'offres formation</h1>
<p class="lead">Chaque semaine, les avis publiés au BOAMP et dans TED pour des prestations de formation, avec leur statut (ouvert, clôturé, annulé).</p>
<ul class="weeks">{''.join(rows)}</ul>
"""
        self.write("semaine/", self.page("semaine/", "Archives hebdomadaires – appels d'offres formation",
                                         "Archives semaine par semaine des appels d'offres publics de formation "
                                         "publiés au BOAMP et dans TED.", body),
                   lastmod=self.content_date(t for w in weeks for t in by_week[w]))

    def regions(self, open_items: list[Notice]) -> None:
        rows = []
        national = [t for t in open_items if is_national(t)]
        today = fmt_date(self.now.astimezone(PARIS).date().isoformat())
        local_by = {r: [t for t in open_items if r in t.regions and not is_national(t)] for r in REGIONS}
        for r in REGIONS:
            slug = slugify(r)
            local = local_by[r]
            where = region_in(r)
            rows.append(f'<tr><th scope="row"><a href="{self.link(f"region/{slug}/")}">{e(r)}</a></th>'
                        f'<td>{len(local)}</td><td>{self.vol.local.get(r, 0)}</td></tr>')
            count = (f"Aucun appel d'offres de formation ouvert {e(where)}" if not local else
                     f"<strong>{plural(len(local), 'appel', 'appels')}</strong> d'offres de formation "
                     f"{'ouvert' if len(local) == 1 else 'ouverts'} {e(where)}")
            cta = ""
            if self.sales.open:
                cta = (f' <a href="{self.link(f"alertes/?region={slug}")}">Recevoir les nouveaux avis {e(where)} par e-mail</a>, '
                       f"chaque matin vers 7&nbsp;h, dès {e(landing.price(self.sales.products['veille-ao-3m']))} pour 3 mois.")
            others = ", ".join(f'<a href="{self.link(f"region/{slugify(o)}/")}">{e(o)}</a>&nbsp;({len(local_by[o])})'
                               for o in REGIONS if o != r)
            body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › <a href="{self.link('region/')}">Régions</a> › {e(r)}</nav>
<h1>Appels d'offres formation {e(where)}</h1>
<p class="lead">{count} au {today}, plus {plural(len(national), "annonce nationale ou multi-régions", "annonces nationales ou multi-régions")}. Source : BOAMP et TED, mise à jour le {self.updated_label()}.</p>
<p>Ces {landing.VOLUME_DAYS} derniers jours, {plural(self.vol.local.get(r, 0), "avis de formation a été publié", "avis de formation ont été publiés")} avec un lieu d'exécution {e(where)}, sans compter les avis nationaux.{cta}</p>
<h2>Ouverts {e(where)} ({len(local)})</h2>
{self.cards(local)}
<h2>Annonces nationales ou multi-régions ({len(national)})</h2>
<p class="small muted">Avis sans lieu précis, ou dont le lieu couvre {NATIONAL_THRESHOLD} régions ou plus. Ils figurent sur les {len(REGIONS)} pages régionales.</p>
{self.cards(national)}
<p class="small muted">Le lieu vient des départements indiqués au BOAMP ou des codes NUTS de TED. Les marchés publiés seulement sur le profil d'acheteur n'y figurent pas. <a href="{self.link('methode/')}">Méthode et sources</a>.</p>
<p class="region-line"><span>Autres régions :</span> {others}.</p>
"""
            scope = [t for t in self.tenders if is_national(t) or (r in t.regions)]
            n = len(local)
            self.write(f"region/{slug}/", self.page(
                f"region/{slug}/", f"Appels d'offres formation {r} – marchés publics ouverts",
                f"{plural(n, 'appel', 'appels')} d'offres de formation {'ouvert' if n == 1 else 'ouverts'} {where} "
                "(BOAMP, TED) : acheteur, date limite, lien vers l'avis officiel. Mis à jour deux fois par jour.",
                body, sticky=True), lastmod=self.content_date(scope))
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Régions</nav>
<h1>Appels d'offres formation par région</h1>
<p class="lead">{len(open_items)} appels d'offres de formation ouverts au {today}, classés selon le lieu d'exécution indiqué dans l'avis (départements BOAMP ou codes NUTS de TED). Les {len(national)} annonces sans lieu précis, ou dont le lieu couvre {NATIONAL_THRESHOLD} régions ou plus, sont classées « nationales » et figurent sur chaque page régionale.</p>
<div class="table-wrap"><table class="region-table">
<thead><tr><th scope="col">Région</th><th scope="col">Ouverts aujourd'hui</th><th scope="col">Publiés en {landing.VOLUME_DAYS} jours</th></tr></thead>
<tbody>{''.join(rows)}<tr><th scope="row">Nationales ou multi-régions</th><td>{len(national)}</td><td>{self.vol.national}</td></tr></tbody>
</table></div>
<p class="small muted">Un avis qui couvre deux régions compte dans chacune. « Publiés en {landing.VOLUME_DAYS} jours » : avis publiés depuis le {fmt_date(self.vol.since)}, un par marché, annulés exclus. <a href="{self.link('methode/')}">Méthode et sources</a>.</p>
"""
        self.write("region/", self.page("region/", "Appels d'offres formation par région",
                                        f"{len(open_items)} appels d'offres publics de formation ouverts, région par région : "
                                        f"nombre d'avis ouverts et publiés en {landing.VOLUME_DAYS} jours, BOAMP et TED.", body),
                   lastmod=self.content_date(self.tenders))

    def soon_panel(self) -> str:
        return f"""<section class="panel soon" id="offre" aria-labelledby="alertes-titre">
<h2 id="alertes-titre">Bientôt : alertes e-mail personnalisées</h2>
<p>Un e-mail par jour ou par semaine avec uniquement les appels d'offres de vos régions et de vos thèmes, à <strong>19&nbsp;€ net par mois</strong> (TVA non applicable, art. 293 B du CGI). <strong>Pas encore disponible</strong> : aucune inscription ni aucun paiement n'est ouvert aujourd'hui. <a href="{self.link('alertes/')}">Voir ce qui est prévu et un exemple d'e-mail</a>.</p>
</section>"""

    def alerts(self, open_items: list[Notice]) -> None:
        profile, since, picked = self.sample.profile, self.sample.since, self.sample.items
        subject, _text, html = render(picked, profile, self.now, self.url, since=since)
        (self.out / "alertes").mkdir(parents=True, exist_ok=True)
        html = html.replace("</body>", f'<script src="{self.link("mesure.js")}" defer></script>\n{self.cro_tag()}</body>', 1)
        # Sample e-mail: linked from /alertes/ for buyers, but not a page to index.
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="robots" content="noindex">', 1)
        (self.out / "alertes" / "exemple-email.html").write_text(html, encoding="utf-8")
        if self.sales.open:
            p = self.sales.products
            self.write("alertes/", self.page(
                "alertes/", "Alerte appels d'offres formation par e-mail – Veille AO Formation",
                f"Alerte e-mail des nouveaux appels d'offres formation de vos régions et thèmes : "
                f"{sales_mod.eur_short(p['veille-ao-3m'])} net/3 mois, {sales_mod.eur_short(p['veille-ao-12m'])} net/12 mois. "
                f"{shop.VAT_NOTE}.",
                landing.wrap_order(shop.order_body(self, subject, f"profil d'exemple « {profile.name} »"),
                                   landing.alerts_top(self, self.sample, self.vol), landing.alerts_bottom(self, self.vol)),
                jsonld=shop.product_jsonld(self, ORG_REF),
                csp=self.sales.checkout_csp(),
                body_scripts=shop.order_scripts(self) + f'<script src="{self.link("static/landing.js")}" defer></script>\n',
                sticky=True), lastmod=max([TEMPLATE_DATE] + [t.published for t in picked]))
            self.write("alertes/commande.html", self.page(
                "alertes/commande.html", "Votre abonnement – Veille AO Formation", "Confirmation d'abonnement.",
                shop.return_body(self), noindex=True, csp=self.sales.order_csp(), body_scripts=shop.return_scripts(self)),
                sitemap=False)
            return
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Alertes e-mail</nav>
<h1>Alertes e-mail personnalisées — bientôt</h1>
<p class="lead">Recevoir chaque jour ou chaque semaine uniquement les appels d'offres formation qui vous concernent, sans les chercher.</p>
<div class="panel soon">
<h2>Statut : pas encore disponible</h2>
<p>Nous n'acceptons <strong>aucune inscription ni aucun paiement</strong> pour l'instant : l'envoi d'e-mails et le paiement en ligne ne sont pas encore en place. Rien n'est vendu sur ce site aujourd'hui.</p>
</div>
<h2>Ce qui est prévu</h2>
<ul>
<li>Prix envisagé : <strong>19&nbsp;€ net par mois</strong> (TVA non applicable, art. 293 B du CGI), sans engagement.</li>
<li>Vos critères : régions, mots-clés (ex. « secourisme », « anglais », « management »), codes CPV, délai minimum pour répondre.</li>
<li>Un e-mail quotidien ou hebdomadaire avec les nouvelles annonces correspondantes, et le lien vers chaque avis officiel.</li>
<li>Rappel des dates limites des annonces suivies.</li>
</ul>
<h2>Exemple réel, généré à partir des annonces du jour</h2>
<p>Profil d'exemple : « {e(profile.name)} », annonces publiées depuis le {fmt_date(since)} et encore ouvertes. Résultat : <strong>{e(subject)}</strong>.</p>
<p><a class="btn ghost" href="{self.link('alertes/exemple-email.html')}" data-mesure="free_download">Voir l'e-mail d'exemple</a></p>
<h2>Gratuit dès maintenant</h2>
<ul>
<li><a href="{self.link('feed.xml')}" data-mesure="free_download">Flux RSS / Atom</a> : toutes les nouvelles annonces, à filtrer dans votre lecteur de flux.</li>
<li><a href="{RELEASE_CSV}" data-mesure="free_download">Export CSV hebdomadaire</a> et <a href="{RELEASE_ICS}" data-mesure="free_download">calendrier des dates limites</a>.</li>
<li>Les <a href="{self.link('region/')}">pages régionales</a> et les <a href="{self.link('#filtres')}">filtres</a> de la page d'accueil.</li>
</ul>
<h2>Être prévenu du lancement</h2>
<p>Pas de formulaire ici, donc aucune donnée personnelle collectée. Si vous avez un compte GitHub, vous pouvez signaler votre intérêt (un 👍 suffit) sur <a href="{INTEREST_ISSUE}">ce ticket public</a> ; nous y annoncerons l'ouverture. Sinon, le lancement sera annoncé dans le flux RSS.</p>
"""
        self.write("alertes/", self.page("alertes/", "Alertes e-mail appels d'offres formation (bientôt)",
                                         "Projet d'alertes e-mail personnalisées sur les appels d'offres formation "
                                         "(19 € net/mois, TVA non applicable, art. 293 B du CGI) : pas encore disponible, exemple d'e-mail et alternatives "
                                         "gratuites.", body))

    def method(self, meta: dict) -> None:
        last = meta.get("last_success") or {}
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Méthode</nav>
<h1>Méthode, sources et limites</h1>
<h2>Sources</h2>
<ul>
<li><strong>BOAMP</strong> (Bulletin officiel des annonces des marchés publics, DILA) via l'<a href="https://boamp-datadila.opendatasoft.com/explore/dataset/boamp/api/">API open data BOAMP</a>. Jeu de données publié sous <a href="https://www.data.gouv.fr/datasets/boamp">Licence Ouverte / Open Licence (Etalab)</a>. Dernière récupération réussie : {e(last.get('BOAMP', 'inconnue'))}.</li>
<li><strong>TED</strong> (Tenders Electronic Daily, Office des publications de l'Union européenne) via l'<a href="https://docs.ted.europa.eu/api/latest/search.html">API de recherche TED</a>, pour les avis d'acheteurs français. Les avis publiés au Supplément au JOUE sont librement réutilisables (<a href="https://ted.europa.eu/fr/legal-notice">avis juridique TED</a>, décision 2011/833/UE). Ce site n'est ni affilié ni approuvé par l'Office des publications ou la DILA. Dernière récupération réussie : {e(last.get('TED', 'inconnue'))}.</li>
</ul>
<h2>Ce qui est sélectionné</h2>
<p>Un avis est retenu s'il s'agit d'un appel à la concurrence (pas d'un avis d'attribution) et s'il remplit au moins une condition :</p>
<ul>
<li>un code CPV de formation : 805xxxxx (services de formation), 804xxxxx (éducation des adultes, e-learning, auto-école), 806xxxxx, 80000000, 79632000 ou 79633000 (formation et perfectionnement du personnel) ;</li>
<li>le descripteur BOAMP « Formation » ;</li>
<li>un mot-clé de formation dans l'objet ou l'intitulé d'un lot (ex. « formation », « formateur », « e-learning », « CACES ») <em>lorsque l'avis n'a aucun code CPV</em>.</li>
</ul>
<p>Les marchés de travaux ou de fournitures sont écartés (ex. construction d'un centre de formation, machine livrée avec formation des utilisateurs).</p>
<h2>Traitement</h2>
<ul>
<li>Mise à jour automatique deux fois par jour (GitHub Actions). Le BOAMP publie ses données deux fois par jour, sept jours sur sept.</li>
<li>Doublons : un même marché publié au BOAMP et dans TED, ou modifié par un rectificatif, n'apparaît qu'une fois (identifiant de procédure eForms), avec les liens vers chaque avis.</li>
<li>Date limite : date et heure telles qu'écrites dans l'avis (heure de Paris en métropole). Pour un marché alloti, la plus proche date limite encore à venir.</li>
<li>Région : départements indiqués par le BOAMP ; pour TED, lieu d'exécution (codes NUTS) ou, à défaut, code postal de l'acheteur.</li>
<li>Aucune intelligence artificielle : les textes affichés sont des extraits des avis officiels. Les noms et coordonnées de personnes figurant dans les avis ne sont pas repris.</li>
</ul>
<h2>Limites</h2>
<ul>
<li>Ce n'est pas une liste exhaustive : beaucoup de marchés à procédure adaptée (MAPA) ne sont publiés que sur le profil d'acheteur ou dans la presse locale, et n'apparaissent ni au BOAMP ni dans TED.</li>
<li>Le classement dépend des codes et descripteurs choisis par les acheteurs ; des annonces pertinentes peuvent manquer et quelques annonces peu pertinentes passer.</li>
<li>Les informations sont fournies à titre indicatif. Seuls l'avis officiel et le dossier de consultation font foi.</li>
</ul>
<h2>Code et données</h2>
<p>Le code est public sur <a href="{REPO_URL}">GitHub</a>. Les exports CSV et ICS de chaque semaine sont publiés dans les <a href="{REPO_URL}/releases">releases</a>.</p>
"""
        self.write("methode/", self.page("methode/", "Méthode et sources – Veille AO Formation",
                                         "Sources (BOAMP, TED), licences, critères de sélection (CPV 805, descripteur "
                                         "Formation), fréquence de mise à jour et limites.", body))

    def legal(self) -> None:
        p = PUBLISHER
        mentions = f"""
<h1>Mentions légales</h1>
<h2>Éditeur</h2>
<p>{e(p['name'])} (société par actions simplifiée au capital de 1 000 €), SIREN {e(p['siren'])}, RCS Paris, siège social : {e(p['address'])}. Informations issues du <a href="https://annuaire-entreprises.data.gouv.fr/entreprise/988741773">registre public</a>.</p>
<p>Jalonel est une marque de READ FRANCE SAS.</p>
<p>Directeur de la publication : {e(p['president'])}, président.</p>
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a>.</p>
<!-- TODO phone -->
<h2>Hébergement</h2>
<p>GitHub Inc. (GitHub Pages), 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis — <a href="https://github.com">github.com</a>.</p>
<h2>Données et propriété intellectuelle</h2>
<p>Les annonces proviennent du BOAMP (DILA, Licence Ouverte / Open Licence Etalab) et de TED (Office des publications de l'Union européenne, réutilisation autorisée par la décision 2011/833/UE). Elles sont sélectionnées, raccourcies et normalisées par nos soins. Les logos de TED et du BOAMP ne sont pas utilisés. Les avis officiels font foi.</p>
<p>Les informations publiées ne constituent pas un conseil juridique. Vérifiez toujours l'avis officiel et le dossier de consultation avant de répondre à un marché.</p>
"""
        self.write("mentions-legales/", self.page("mentions-legales/", "Mentions légales – Veille AO Formation",
                                                  "Éditeur, hébergeur et sources de Veille AO Formation.", mentions))
        privacy = f"""
<h1>Confidentialité</h1>
<p>Ce site ne collecte aucune donnée personnelle : pas de compte, pas de formulaire, pas de cookie ni de traceur tiers ; seule une mesure anonyme et agrégée de la fréquentation et de l'usage du site est effectuée (voir ci-dessous). Aucun bandeau cookies n'est donc nécessaire.</p>
<ul>
<li><strong>Hébergement</strong> : GitHub Pages traite des journaux techniques (adresse IP notamment) pour servir le site et le sécuriser, selon la <a href="https://docs.github.com/fr/site-policy/privacy-policies/github-general-privacy-statement">déclaration de confidentialité de GitHub</a>.</li>
<li><strong>Téléchargements</strong> : les exports CSV/ICS sont servis par GitHub Releases, qui ne nous communique qu'un nombre total de téléchargements, sans aucune information sur les personnes.</li>
<li><strong>Annonces</strong> : nous ne reprenons pas les noms ni les coordonnées des personnes de contact figurant dans les avis publics ; seul le nom de l'acheteur public est affiché.</li>
{'' if self.sales.open else "<li><strong>Ticket GitHub</strong> : si vous réagissez au ticket public d'intérêt, votre pseudonyme GitHub est visible publiquement, selon les règles de GitHub.</li>"}
</ul>
<h2>Mesure d'audience et de comportement (mise à jour du 13 octobre 2026)</h2>
<p>Nous mesurons la fréquentation et l'usage de ce site de façon anonyme et agrégée, pour ses seules statistiques et pour l'améliorer. Seuls des totaux journaliers sont conservés, dans un tableur de READ FRANCE hébergé par Google :</p>
<ul>
<li><strong>Audience</strong> : pages vues, clics vers la page de commande et étapes de la commande, avec le domaine du site d'origine et les paramètres de campagne (utm) éventuels.</li>
<li><strong>Comportement</strong> : profondeur de défilement de la page, sections atteintes, temps écoulé avant le premier clic sur un bouton, formulaires commencés et envoyés et le <em>nom</em> du dernier champ touché (jamais son contenu : rien de ce que vous saisissez n'est lu), questions de la FAQ ouvertes, liens sortants (seulement le domaine visé), erreurs techniques (une empreinte anonyme du message), vitesse d'affichage (Core Web Vitals) et clics répétés au même endroit.</li>
<li><strong>Tests A/B</strong> : une partie des visiteurs peut voir une autre version d'un titre, d'un bouton ou d'un prix. La lettre de la version est gardée dans le stockage de session de l'onglet (sessionStorage, effacé à la fermeture de l'onglet) ou choisie selon la date (sans rien stocker), et n'est jamais rattachée à une personne. Le prix payé est toujours celui affiché. Aucun test n'est en cours aujourd'hui.</li>
<li><strong>Ce que nous ne faisons pas</strong> : aucun cookie, aucun identifiant, aucune adresse IP conservée, aucune donnée personnelle, aucun suivi d'un site à l'autre, aucun recoupement avec d'autres données ; rien n'est partagé ni vendu.</li>
<li><strong>Pour refuser</strong> : activez « Do Not Track » ou « Global Privacy Control » dans votre navigateur ; plus rien n'est alors mesuré. Les robots d'indexation et les navigateurs automatisés (reconnus dans votre navigateur, sans que sa signature soit transmise) sont comptés à part ou pas du tout, et une page préchargée n'est comptée que si vous l'ouvrez.</li>
</ul>
<p>Strictement agrégée, limitée aux statistiques propres de ce site, sans suivi d'un site à l'autre, sans recoupement avec d'autres traitements et sans transmission à des tiers, cette mesure relève de l'exemption de consentement prévue pour la mesure d'audience (article 82 de la loi Informatique et Libertés, lignes directrices et recommandation de la CNIL du 17 septembre 2020 sur les cookies et autres traceurs) : aucun bandeau de consentement n'est donc nécessaire.</p>
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a>.</p>
"""
        if self.sales.open:
            privacy = privacy.replace(
                "<p>Ce site ne collecte aucune donnée personnelle : pas de compte, pas de formulaire, pas de cookie ni de traceur tiers ; seule une mesure anonyme et agrégée de la fréquentation et de l'usage du site est effectuée (voir ci-dessous). Aucun bandeau cookies n'est donc nécessaire.</p>",
                "<p>La consultation du site ne collecte aucune donnée personnelle : pas de compte, pas de cookie ni de traceur tiers ; la fréquentation et l'usage du site sont seulement mesurés de façon anonyme et agrégée (voir ci-dessous). Une seule exception : le formulaire d'abonnement aux alertes e-mail, qui charge le module de paiement de Stripe et ses cookies quand vous commencez à le remplir, et qui traite les données de la commande (voir plus bas).</p>")
            privacy = privacy.replace('<p>Contact : <a href="mailto:', shop.privacy_section(self) + '\n<p>Contact : <a href="mailto:')
        self.write("confidentialite/", self.page("confidentialite/", "Confidentialité – Veille AO Formation",
                                                 "Aucun cookie ni traceur à la consultation ; cookies de Stripe et données traitées seulement pour l'abonnement aux alertes."
                                                 if self.sales.open else
                                                 "Aucune donnée personnelle collectée, aucun cookie ni traceur.", privacy))
        cgv = f"""
<h1>Conditions générales de vente — projet</h1>
<div class="panel soon"><p><strong>Projet non applicable, en attente de validation par la direction de READ FRANCE SAS.</strong> Aucun abonnement n'est vendu à ce jour ; ce texte décrit les conditions envisagées pour les futures alertes e-mail.</p></div>
<h2>1. Objet</h2>
<p>Abonnement mensuel à un service d'alertes e-mail signalant des appels d'offres publics de formation selon des critères choisis par l'abonné (régions, mots-clés, CPV).</p>
<h2>2. Prix et paiement</h2>
<p>19&nbsp;€ net par mois (TVA non applicable, art. 293 B du CGI), payable d'avance par carte bancaire via le prestataire de paiement Stripe. Abonnement sans engagement, résiliable à tout moment avec effet à la fin de la période mensuelle en cours.</p>
<h2>3. Nature du service</h2>
<p>Service d'information : sélection automatique d'avis publiés au BOAMP et dans TED. Il n'est pas exhaustif et ne remplace pas la consultation des avis officiels et des dossiers de consultation. READ FRANCE SAS ne garantit pas l'obtention d'un marché.</p>
<h2>4. Droit de rétractation</h2>
<p>Le service s'adresse principalement aux professionnels. Lorsque l'abonné est un consommateur (ou un professionnel employant au plus cinq salariés, hors champ de son activité principale, art. L221-3 du Code de la consommation), il dispose d'un délai de 14 jours pour se rétracter. S'il demande expressément que le service commence avant la fin de ce délai, il doit, en cas de rétractation, le montant correspondant au service fourni jusqu'à la rétractation (art. L221-25).</p>
<h2>5. Responsabilité</h2>
<p>La responsabilité de READ FRANCE SAS est limitée au montant des sommes versées au cours des trois derniers mois. Elle ne saurait être engagée du fait d'une erreur ou omission dans les données publiées par les sources officielles.</p>
<h2>6. Données personnelles</h2>
<p>L'adresse e-mail et les critères de l'abonné seront utilisés uniquement pour l'envoi des alertes et la facturation, conservés pendant la durée de l'abonnement puis pour la durée légale de conservation des pièces comptables.</p>
<h2>7. Droit applicable</h2>
<p>Droit français. Médiation de la consommation : à désigner avant l'ouverture de la vente aux consommateurs.</p>
<p>Éditeur : {e(p['name'])}, SIREN {e(p['siren'])}, {e(p['address'])}.</p>
"""
        if self.sales.open:
            self.write("cgv-pro/", self.page(
                "cgv-pro/", "Conditions générales de vente aux professionnels – Veille AO Formation",
                "CGV B2B des alertes e-mail (périodes prépayées de 3 ou 12 mois, sans renouvellement automatique).",
                shop.cgv_pro_body(self), noindex=True), sitemap=False)
            cgv = f"""
<h1>Conditions générales de vente</h1>
<p>L'ancien projet de conditions publié sur cette page est remplacé par les <a href="{self.link('cgv-pro/')}">conditions générales de vente aux professionnels des alertes e-mail</a> (version du {sales_mod.TERMS_DATE}).</p>
"""
        self.write("cgv/", self.page("cgv/", "CGV (projet) – Veille AO Formation" if not self.sales.open else "CGV – Veille AO Formation",
                                     "Projet de conditions générales de vente des futures alertes e-mail, non applicable.",
                                     cgv, noindex=True), sitemap=False)

    def not_found(self) -> None:
        body = f"""<h1>Page introuvable</h1><p>Cette page n'existe pas ou plus. <a href="{self.link()}">Voir les appels d'offres formation ouverts</a>.</p>"""
        self.write("404.html", self.page("404.html", "Page introuvable – Veille AO Formation",
                                         "Page introuvable.", body, noindex=True), sitemap=False)

    def sitemap(self) -> None:
        urls = "".join(f"<url><loc>{e(self.url + p)}</loc><lastmod>{m}</lastmod></url>" for p, m in self.pages)
        (self.out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")
        (self.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {self.url}sitemap.xml\n",
                                             encoding="utf-8")
