"""Static site generator (no dependencies)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .digest import NATIONAL_THRESHOLD, is_national, render
from .exporters import ATTRIBUTION, fmt_date, fmt_deadline, to_atom, to_csv, to_ics, to_json, to_rss
from . import sales as sales_mod
from . import landing, shop
from .geo import REGIONS, slugify
from .model import NICHES, PARIS, Notice, fold

# Free downloads served by the site itself, rebuilt at every update (open tenders as CSV, deadlines as ICS).
CSV_FILE = "telechargements/ao-secteurs-ouverts.csv"
ICS_FILE = "telechargements/ao-secteurs-echeances.ics"
BRAND = landing.BRAND
STATIC = Path(__file__).resolve().parent.parent / "static"
# Anonymous, cookieless audience measurement (verbatim copy of octave-checkout gas/web/jalonel/mesure.js).
MESURE = Path(__file__).resolve().parent / "mesure.js"
# Aggregate behaviour counters and A/B framework (verbatim copy of octave-checkout gas/web/jalonel/cro.js), same backend.
CRO = Path(__file__).resolve().parent / "cro.js"
CRO_SITE = "veille-ao-formation"  # merged into ao-formation on 2026-10-24 (was "lot-repere")
CRO_API = "https://script.google.com/macros/s/AKfycbzD1IRL8QALluKGZxgGiORFP6-DkC_qFJYR9aOxZiimxa06nbCqpzUI-9iLblp6V2VY/exec"
CRO_HOSTS = "ao-formation.jalonel.com"
INDEXNOW_KEY = "5be19f56d46ca3d2553bee28ebfa8edd"
# Time to first CTA = first click on a paid-alerts call to action (links to /alertes/ or #commander-h, pay button).
CRO_CTA = "[data-cta]"

PUBLISHER = {
    "name": "READ FRANCE SAS",
    "siren": "988 741 773",
    "address": "11 rue Rouvet, 75019 Paris",
    "president": "Tanguy Warsmann",
}

# Brand: Jalonel (hub https://jalonel.com/), a brand of READ FRANCE SAS (the legal operator). Publisher of every page.
JALONEL_URL = "https://jalonel.com/"
JALONEL_ORG = {"@type": "Organization", "@id": "https://jalonel.com/#org", "name": "Jalonel", "url": JALONEL_URL,
               "parentOrganization": {"@type": "Organization", "name": "READ FRANCE SAS", "identifier": "SIREN 988741773"}}

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


FEED_LIMIT = 300
SCRIPT_RE = re.compile(r"(<script\b.*?</script>)", re.S)


def e(text: object) -> str:
    return escape("" if text is None else str(text), quote=True)


def text(value: object) -> str:
    """Escape for <title> and double-quoted attributes, keeping apostrophes literal (d'offres, not d&#x27;offres)."""
    return escape("" if value is None else str(value), quote=False).replace('"', "&quot;")


def fmt_eur(v: float | None) -> str:
    if not v:
        return ""
    return f"{v:,.0f}".replace(",", " ") + " € HT (estimation)"



# Words searchers type (Search Console, 2026-10-19: "gardiennage …", "travaux d entretien des espaces verts"), used in
# <title> only; page text keeps the niche labels.
SEO_TERM = {"securite": "gardiennage et sécurité privée", "espaces-verts": "entretien des espaces verts"}

class Site:
    def __init__(self, out: Path, site_url: str, now: datetime, sales: "sales_mod.Sales | None" = None):
        self.sales = sales or sales_mod.load()
        self.out = out
        self.url = site_url if site_url.endswith("/") else site_url + "/"
        # path prefix for internal links, e.g. "/lot-repere/"
        self.base = urlparse(self.url).path or "/"
        self.now = now
        self.pages: list[tuple[str, str]] = []  # (path, lastmod)
        self.meta: dict = {}
        self.org_id = self.url + "#org"

    # -- helpers -------------------------------------------------------------

    @property
    def org_ref(self) -> dict:
        return JALONEL_ORG

    def org_node(self) -> dict:
        return {"@context": "https://schema.org", "@type": "Organization", "@id": self.org_id, "name": BRAND,
                "url": self.url, "email": landing.CONTACT, "logo": self.url + "static/og.png",
                "parentOrganization": JALONEL_ORG}

    def link(self, path: str = "") -> str:
        return self.base + path

    def cro_tag(self) -> str:
        """cro.js, then the goals. No A/B experiment since 2026-10-24 (lot-repere-prix12 closed)."""
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
            part.replace("« ", "« ").replace(" »", " »") if part.startswith("<script")
            else part.replace("« ", "«&nbsp;").replace(" »", "&nbsp;»")
            for part in SCRIPT_RE.split(html))
        target.write_text(html, encoding="utf-8")
        if sitemap:
            self.pages.append((path, lastmod or TEMPLATE_DATE))

    # Legal notice and privacy pages are near-identical across the Jalonel subdomains: noindex, follow (2026-10-18).
    def page(self, path: str, title: str, description: str, body: str, *,
             jsonld: dict | list | None = None, scripts: bool = False, noindex: bool | str = False,
             csp: str = "", body_scripts: str = "", sticky: bool = False) -> str:
        canonical = self.url + path
        # "<\/" keeps a notice title containing "</script>" from closing the JSON-LD block.
        ld_json = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
        ld = f'<script type="application/ld+json">{ld_json}</script>' if jsonld else ""
        robots = "noindex" if noindex is True else noindex
        nav = [("/", "Formation"), ("", "Trois secteurs")] + [(f"{k}/", n.label) for k, n in NICHES.items()] + [("methode/", "Méthode")]
        nav_html = "".join(
            f'<a href="{p if p.startswith("/") else self.link(p)}"{" aria-current=\"page\"" if p == path else ""}>{e(label)}</a>'
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
<meta name="theme-color" content="#1f4e3d">
<link rel="icon" href="{self.link('static/favicon.svg')}" type="image/svg+xml">
<link rel="alternate" type="application/atom+xml" title="Appels d'offres ouverts (Atom)" href="{self.link('feed.xml')}">
<link rel="alternate" type="application/rss+xml" title="Appels d'offres ouverts (RSS)" href="{self.link('rss.xml')}">
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
<p class="attrib">{e(ATTRIBUTION)} Les avis officiels font foi : vérifiez toujours l'avis et le dossier de consultation avant de répondre.</p>
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a></p>
<nav aria-label="Liens de bas de page">
<a href="{self.link('methode/')}">Méthode et sources</a>
<a href="{self.link('mentions-legales/')}">Mentions légales</a>
<a href="{self.link('confidentialite/')}">Confidentialité</a>
{f'<a href="{self.link("cgv-pro/")}">CGV</a>' if self.sales.open else ''}
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
        meta = [f'<span class="niche-tag">{e(NICHES[k].label)}</span>' for k in t.niches if k in NICHES]
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
        search_text = fold(f"{t.title} {t.buyer} {t.description} {' '.join(t.lots)} {' '.join(t.cpv)} {t.cpv_main_label()} "
                           f"{' '.join(t.regions)} {' '.join(t.departments)}")
        return f"""<article class="card" data-niches="{e('|'.join(t.niches))}" data-regions="{e('|'.join(t.regions))}" data-national="{int(national)}" data-deadline="{e(t.deadline or '')}" data-search="{e(search_text)}" data-source="{e(t.source)}">
<div class="card-head"><{heading} class="card-title"><a href="{e(t.url)}" rel="noopener">{e(t.title)}</a></{heading}>{badge}</div>
<p class="buyer">{e(t.buyer)}</p>
<dl class="facts">
<div><dt>Date limite</dt><dd>{e(fmt_deadline(t.deadline)) if t.deadline else 'Non précisée'}</dd></div>
<div><dt>Lieu</dt><dd>{e(regions)}{e(deps)}</dd></div>
</dl>
{desc}
<p class="meta">{' '.join(meta)}</p>
<p class="src">Publié le {fmt_date(t.published)}{upd} · <a href="{e(t.url)}" rel="noopener">Avis officiel {e(t.source)}</a>{alt}{rect}</p>
</article>"""

    def cards(self, items: list[Notice]) -> str:
        if not items:
            return '<p class="empty">Aucun appel d\'offres à afficher.</p>'
        return '<div class="list">' + "\n".join(self.card(t) for t in items) + "</div>"

    def filters(self, *, with_niche: bool, placeholder: str) -> str:
        region_opts = "".join(f'<option value="{e(r)}">{e(r)}</option>' for r in REGIONS)
        niche = ""
        if with_niche:
            opts = "".join(f'<option value="{k}" data-short="{e(n.short)}">{e(n.label)}</option>' for k, n in NICHES.items())
            niche = f'<div class="field"><label for="niche">Métier</label><select id="niche"><option value="">Les trois métiers</option>{opts}</select></div>'
        return f"""<section aria-labelledby="filtres-titre" class="filters" id="filtres" hidden>
<h2 id="filtres-titre" class="sr-only">Filtrer les annonces</h2>
<div class="field"><label for="q">Mot-clé</label><input id="q" type="search" placeholder="{e(placeholder)}" autocomplete="off"></div>
{niche}
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
</section>"""

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
        self.vols = {k: landing.volume([t for t in tenders if k in t.niches], self.now) for k in NICHES}
        self.open_counts = {k: sum(1 for t in open_items if k in t.niches) for k in NICHES}
        self.sample = landing.sample(open_items, tenders, self.now)

        self.index(open_items, meta)
        for k in NICHES:
            self.niche_pages(k, [t for t in open_items if k in t.niches])
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
        (self.out / CSV_FILE).parent.mkdir(exist_ok=True)
        (self.out / CSV_FILE).write_text(to_csv(open_items, self.now), encoding="utf-8")
        (self.out / ICS_FILE).write_text(to_ics(open_items, self.now, self.url), encoding="utf-8", newline="")
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

    def sources_note(self, meta: dict) -> str:
        failed = [s for s, ok in (meta.get("last_run_ok") or {}).items() if not ok]
        if not failed:
            return ""
        return (f'<p class="warn" role="status">La dernière mise à jour de {", ".join(failed)} a échoué ; '
                f'les annonces de cette source peuvent dater de la mise à jour précédente.</p>')

    def stats(self, items: list[Notice]) -> str:
        soon = sum(1 for t in items if (t.days_left(self.now) or 99) <= 7)
        new7 = sum(1 for t in items if t.published >= (self.now.date() - timedelta(days=7)).isoformat())
        return f"""<ul class="stats" aria-label="Chiffres clés">
<li><strong>{len(items)}</strong><span>appels d'offres ouverts</span></li>
<li><strong>{new7}</strong><span>publiés ces 7 derniers jours</span></li>
<li><strong>{soon}</strong><span>clôturent sous 7 jours</span></li>
</ul>"""

    def vol_attr(self, vol: landing.Volume) -> str:
        return e(json.dumps({r: [vol.for_region(r), slugify(r)] for r in REGIONS}, ensure_ascii=False, separators=(",", ":")))

    def index(self, items: list[Notice], meta: dict) -> None:
        n = len(items)
        since = self.vol.since
        dup = sum(1 for t in self.tenders if t.published >= since and t.status != "annule"
                  and t.source == "BOAMP" and any(k.startswith("TED") for k in t.alt_urls))
        body = f"""
{landing.home_hero(self, n, self.sources_note(meta))}
{self.filters(with_niche=True, placeholder="ex. vitrerie, élagage, SSIAP, bionettoyage")}

<section aria-labelledby="liste-titre" id="liste" data-volume="{self.vol_attr(self.vol)}" data-days="{landing.VOLUME_DAYS}"{' data-sales="1"' if self.sales.open else ''}>
<h2 id="liste-titre">Annonces en cours, par date limite</h2>
{self.cards(items)}
{landing.inline_cta_template(self) if self.sales.open else ''}
</section>

{self.stats(items)}
{landing.niche_tiles(self, self.open_counts, self.vols)}
{landing.home_after_list(self, n, self.open_counts, self.sample, self.link(CSV_FILE), self.link(ICS_FILE))}
{landing.missed_by_free(self, self.vol, dup)}
{landing.band(self)}
{landing.about(self)}
"""
        jsonld = [
            {"@context": "https://schema.org", "@type": "WebSite", "name": BRAND, "url": self.url,
             "inLanguage": "fr-FR", "publisher": self.org_ref},
            self.org_node(),
            {"@context": "https://schema.org", "@type": "Dataset",
             "name": "Appels d'offres publics ouverts en nettoyage de locaux, espaces verts et sécurité privée (France)",
             "description": "Sélection quotidienne des avis de marchés publics français ouverts en nettoyage de locaux "
                            "(CPV 90910, 90911, 90914, 90919), entretien d'espaces verts (CPV 7731, 7734, élagage) et sécurité privée "
                            "(CPV 7971, gardiennage), issus du BOAMP et de TED, doublons fusionnés.",
             "url": self.url, "inLanguage": "fr-FR",
             "isAccessibleForFree": True,
             "license": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
             "isBasedOn": ["https://www.data.gouv.fr/datasets/boamp", "https://ted.europa.eu/"],
             "creator": self.org_ref,
             "dateModified": self.now.date().isoformat(),
             "distribution": [
                 {"@type": "DataDownload", "encodingFormat": "application/json",
                  "contentUrl": self.url + "data/ouverts.json"},
                 {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": self.url + CSV_FILE},
             ]},
        ]
        self.write("", self.page(
            "", "Appels d'offres nettoyage, entretien des espaces verts, gardiennage – liste gratuite",
            f"{n} appels d'offres publics ouverts en France : nettoyage de locaux, entretien des espaces verts, "
            "gardiennage et sécurité privée. Acheteur, date limite, région, lien vers l'avis BOAMP ou TED. Mis à jour 2 fois par jour.",
            body, jsonld=jsonld, scripts=True, sticky=True), lastmod=self.content_date(self.tenders))

    def niche_pages(self, k: str, items: list[Notice]) -> None:
        n = NICHES[k]
        vol = self.vols[k]
        today = fmt_date(self.now.astimezone(PARIS).date().isoformat())
        national = [t for t in items if is_national(t)]
        local_by = {r: [t for t in items if r in t.regions and not is_national(t)] for r in REGIONS}
        region_links = ", ".join(f'<a href="{self.link(f"{k}/{slugify(r)}/")}">{e(r)}</a>&nbsp;({len(local_by[r])})'
                                 for r in REGIONS)
        others = " · ".join(f'<a href="{self.link(o + "/")}">{e(NICHES[o].label)}</a>' for o in NICHES if o != k)
        scope = [t for t in self.tenders if k in t.niches]
        cta = ""
        if self.sales.open:
            cta = (f' <a href="{self.link(f"alertes/?niche={k}")}" data-cta>Recevoir les nouveaux avis {e(n.short)} de vos régions par e-mail</a>, '
                   f"chaque matin vers 7&nbsp;h, dès {e(landing.entry_price(self))} pour 3 mois.")
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › {e(n.label)}</nav>
<h1>Appels d'offres {e(n.short)} ouverts</h1>
<p class="lead"><strong>{len(items)}</strong> marchés publics de {e(n.label.lower())} en cours au {today}, relevés dans le BOAMP et dans TED. Ces {landing.VOLUME_DAYS} derniers jours : {vol.total} avis publiés, soit environ {vol.per_week} par semaine. Mise à jour le {self.updated_label()}.{cta}</p>
{self.stats(items)}
<p class="region-line"><span>Par région :</span> {region_links}.</p>
{self.filters(with_niche=False, placeholder=self.placeholder(k))}
<section aria-labelledby="liste-titre" id="liste" data-volume="{self.vol_attr(vol)}" data-days="{landing.VOLUME_DAYS}"{' data-sales="1"' if self.sales.open else ''}>
<h2 id="liste-titre">Annonces {e(n.short)} en cours, par date limite</h2>
{self.cards(items)}
{landing.inline_cta_template(self) if self.sales.open else ''}
</section>
<p class="small muted">Sélection : {e(self.rule_text(k))} <a href="{self.link('methode/')}">Méthode et sources</a>. Autres métiers : {others}.</p>
{landing.band(self, k)}
"""
        jsonld = {"@context": "https://schema.org", "@type": "CollectionPage",
                  "name": f"Appels d'offres {n.short} ouverts", "url": self.url + f"{k}/", "publisher": self.org_ref,
                  "mainEntity": {"@type": "ItemList", "numberOfItems": len(items),
                                 "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": t.url,
                                                      "name": t.title} for i, t in enumerate(items[:100])]}}
        self.write(f"{k}/", self.page(
            f"{k}/", f"Appels d'offres {SEO_TERM.get(k, n.short)} en cours – marchés publics (BOAMP, TED)",
            f"{len(items)} appels d'offres publics de {n.label.lower()} ouverts en France : acheteur, date limite, "
            "région, lien vers l'avis officiel. Mis à jour deux fois par jour, gratuit.",
            body, jsonld=jsonld, scripts=True, sticky=True), lastmod=self.content_date(scope))

        for r in REGIONS:
            slug = slugify(r)
            local = local_by[r]
            where = region_in(r)
            count = (f"Aucun appel d'offres {e(n.short)} ouvert {e(where)}" if not local else
                     f"<strong>{plural(len(local), 'appel', 'appels')}</strong> d'offres {e(n.short)} "
                     f"{'ouvert' if len(local) == 1 else 'ouverts'} {e(where)}")
            rcta = ""
            if self.sales.open:
                rcta = (f' <a href="{self.link(f"alertes/?niche={k}&amp;region={slug}")}" data-cta>Recevoir les nouveaux avis {e(n.short)} {e(where)} par e-mail</a>, '
                        f"dès {e(landing.entry_price(self))} pour 3 mois.")
            others_r = ", ".join(f'<a href="{self.link(f"{k}/{slugify(o)}/")}">{e(o)}</a>&nbsp;({len(local_by[o])})'
                                 for o in REGIONS if o != r)
            body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › <a href="{self.link(k + '/')}">{e(n.label)}</a> › {e(r)}</nav>
<h1>Appels d'offres {e(n.short)} {e(where)}</h1>
<p class="lead">{count} au {today}, plus {plural(len(national), "annonce nationale ou multi-régions", "annonces nationales ou multi-régions")}. Source : BOAMP et TED, mise à jour le {self.updated_label()}.</p>
<p>Ces {landing.VOLUME_DAYS} derniers jours, {plural(vol.local.get(r, 0), "avis a été publié", "avis ont été publiés")} avec un lieu d'exécution {e(where)}, sans compter les avis nationaux.{rcta}</p>
<h2>Ouverts {e(where)} ({len(local)})</h2>
{self.cards(local)}
<h2>Annonces nationales ou multi-régions ({len(national)})</h2>
<p class="small muted">Avis sans lieu précis, ou dont le lieu couvre {NATIONAL_THRESHOLD} régions ou plus.</p>
{self.cards(national)}
<p class="small muted">Le lieu vient des départements indiqués au BOAMP ou des codes NUTS de TED. Les marchés publiés seulement sur le profil d'acheteur n'y figurent pas. <a href="{self.link('methode/')}">Méthode et sources</a>.</p>
<p class="region-line"><span>Autres régions :</span> {others_r}.</p>
"""
            rscope = [t for t in scope if is_national(t) or r in t.regions]
            self.write(f"{k}/{slug}/", self.page(
                f"{k}/{slug}/", f"Appels d'offres {SEO_TERM.get(k, n.short)} {r} – marchés publics ouverts",
                f"{plural(len(local), 'appel', 'appels')} d'offres {n.short} {'ouvert' if len(local) <= 1 else 'ouverts'} {where} "
                "(BOAMP, TED) : acheteur, date limite, lien vers l'avis officiel. Mis à jour deux fois par jour.",
                # A region with no local notice today is a thin page: kept for visitors, out of the index.
                body, sticky=True, noindex=False if local else "noindex, follow"),
                sitemap=bool(local), lastmod=self.content_date(rscope))

    @staticmethod
    def placeholder(k: str) -> str:
        return {"nettoyage": "ex. vitrerie, bionettoyage, école, hôpital",
                "espaces-verts": "ex. élagage, tonte, cimetière, fauchage",
                "securite": "ex. SSIAP, rondes, maître-chien, événement"}[k]

    @staticmethod
    def rule_text(k: str) -> str:
        n = NICHES[k]
        cpv = ", ".join(n.cpv)
        dc = ", ".join(sorted(n.descriptors))
        return (f"marchés de services avec un code CPV commençant par {cpv}, ou le descripteur BOAMP {dc}, "
                "ou, quand l'avis n'a aucun code CPV, un mot-clé du métier dans l'objet.")

    def soon_body(self) -> str:
        return f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Alertes e-mail</nav>
<h1>Alertes e-mail : ouverture prochaine</h1>
<p class="lead">Un e-mail chaque matin avec les nouveaux appels d'offres de vos métiers et de vos régions. La commande en ligne n'est pas encore ouverte : aucun paiement n'est possible sur ce site aujourd'hui.</p>
<p>En attendant, la liste complète est gratuite : <a href="{self.link()}">toutes les annonces</a>, le <a href="{self.link('rss.xml')}" data-mesure="free_download">flux RSS</a> et l'<a href="{self.link('alertes/exemple-email.html')}" data-mesure="free_download">exemple d'e-mail</a> construit avec les avis du jour.</p>
"""

    def alerts(self, open_items: list[Notice]) -> None:
        profile, since, picked = self.sample.profile, self.sample.since, self.sample.items
        subject, _text, html = render(picked, profile, self.now, self.url, since=since)
        (self.out / "alertes").mkdir(parents=True, exist_ok=True)
        html = html.replace("</body>", f'<script src="{self.link("mesure.js")}" defer></script>\n{self.cro_tag()}</body>', 1)
        # Sample e-mail: linked from /alertes/ for buyers, but not a page to index.
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="robots" content="noindex">', 1)
        (self.out / "alertes" / "exemple-email.html").write_text(html, encoding="utf-8")
        if not self.sales.open:
            self.write("alertes/", self.page("alertes/", "Alertes e-mail appels d'offres (ouverture prochaine) – Veille AO",
                                             "Alertes e-mail sur les appels d'offres nettoyage, espaces verts et sécurité privée : "
                                             "pas encore ouvertes ; exemple d'e-mail et liste gratuite.", self.soon_body()))
            return
        p = self.sales.products
        faq = [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
               for q, a in landing.faq_items(self, self.vol)]
        jsonld = shop.product_jsonld(self, self.org_ref) + [
            {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": faq}]
        self.write("alertes/", self.page(
            "alertes/", "Alertes e-mail appels d'offres nettoyage, espaces verts, sécurité – Veille AO",
            f"Chaque matin, les nouveaux appels d'offres de vos métiers et régions : dès "
            f"{sales_mod.eur_short(p['lot-repere-3m'])} net pour 3 mois, sans renouvellement. {shop.VAT_NOTE}.",
            landing.wrap_order(shop.order_body(self, subject, f"profil d'exemple « {profile.name} »"),
                               landing.alerts_top(self, self.sample, self.vols, self.vol), landing.alerts_bottom(self, self.vol)),
            jsonld=jsonld,
            csp=self.sales.checkout_csp(),
            body_scripts=shop.order_scripts(self) + f'<script src="{self.link("static/landing.js")}" defer></script>\n',
            sticky=True), lastmod=max([TEMPLATE_DATE] + [t.published for t in picked]))
        self.write("alertes/commande.html", self.page(
            "alertes/commande.html", "Votre abonnement – Veille AO", "Confirmation d'abonnement.",
            shop.return_body(self), noindex=True, csp=self.sales.order_csp(), body_scripts=shop.return_scripts(self)),
            sitemap=False)

    def method(self, meta: dict) -> None:
        last = meta.get("last_success") or {}
        rows = []
        for k, n in NICHES.items():
            rows.append(f"""<tr><th scope="row"><a href="{self.link(k + '/')}">{e(n.label)}</a></th>
<td>{e(', '.join(n.cpv))}</td><td>{e(', '.join(sorted(n.descriptors)))}</td>
<td>{e(', '.join(n.exclude_cpv))}</td></tr>""")
        body = f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{self.link()}">Accueil</a> › Méthode</nav>
<h1>Méthode, sources et limites</h1>
<h2>Sources</h2>
<ul>
<li><strong>BOAMP</strong> (Bulletin officiel des annonces des marchés publics, DILA) via l'<a href="https://boamp-datadila.opendatasoft.com/explore/dataset/boamp/api/">API open data BOAMP</a>. Jeu de données publié sous <a href="https://www.data.gouv.fr/datasets/boamp">Licence Ouverte / Open Licence (Etalab)</a>. Dernière récupération réussie : {e(last.get('BOAMP', 'inconnue'))}.</li>
<li><strong>TED</strong> (Tenders Electronic Daily, Office des publications de l'Union européenne) via l'<a href="https://docs.ted.europa.eu/api/latest/search.html">API de recherche TED</a>, pour les avis d'acheteurs français. Les avis publiés au Supplément au JOUE sont librement réutilisables (<a href="https://ted.europa.eu/fr/legal-notice">avis juridique TED</a>, décision 2011/833/UE). Ce site n'est ni affilié ni approuvé par l'Office des publications ou la DILA. Dernière récupération réussie : {e(last.get('TED', 'inconnue'))}.</li>
</ul>
<h2>Ce qui est sélectionné</h2>
<p>Un avis est retenu s'il s'agit d'un appel à la concurrence (pas d'un avis d'attribution) pour un marché de <strong>services</strong>, et s'il remplit au moins une condition pour l'un des trois métiers :</p>
<ul>
<li>un code CPV du métier (tableau ci-dessous) ;</li>
<li>un descripteur BOAMP du métier : 239 « Nettoyage de locaux », 116 « Espaces verts », 138 « Gardiennage ». Le descripteur 331 « Surveillance » sert aussi pour la surveillance de réseaux ou d'ouvrages d'art : il ne compte qu'avec un code CPV ou un mot-clé de sécurité privée ;</li>
<li>un mot-clé du métier dans l'objet ou l'intitulé d'un lot (« nettoyage des locaux », « vitrerie », « élagage », « gardiennage », « SSIAP »…) <em>lorsque l'avis n'a aucun code CPV</em>.</li>
</ul>
<div class="table-wrap"><table class="region-table">
<thead><tr><th scope="col">Métier</th><th scope="col">Codes CPV retenus (préfixes)</th><th scope="col">Descripteurs BOAMP</th><th scope="col">Avis retenus par le seul descripteur, écartés si tous leurs codes CPV commencent par</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
<p>Ce qui est écarté, et pourquoi : le nettoyage de voirie, le déneigement, la collecte des déchets et le curage des réseaux (CPV 9050 à 9064) ne sont pas du nettoyage de locaux ; la dératisation (9092) non plus. En espaces verts, l'exploitation forestière (77211100) et les études d'ingénierie (71) sont écartées, l'élagage et l'abattage d'arbres (77211400, 77211500) sont gardés. En sécurité, la fourniture et la maintenance d'alarmes ou de vidéoprotection (35, 3232, 5032), la gestion du stationnement et la veille presse sont écartées : ce sont des fournitures ou d'autres métiers. Ces règles ont été ajustées le 13 octobre 2026 en relisant les titres réels de 465 avis ouverts.</p>
<h2>Traitement</h2>
<ul>
<li>Mise à jour automatique deux fois par jour (GitHub Actions). Le BOAMP publie ses données deux fois par jour, sept jours sur sept.</li>
<li>Doublons : un même marché publié au BOAMP et dans TED, ou modifié par un rectificatif, n'apparaît qu'une fois (identifiant de procédure eForms), avec les liens vers chaque avis.</li>
<li>Date limite : date et heure telles qu'écrites dans l'avis (heure de Paris en métropole). Pour un marché alloti, la plus proche date limite encore à venir.</li>
<li>Région : départements indiqués par le BOAMP ; pour TED, lieu d'exécution (codes NUTS) ou, à défaut, code postal de l'acheteur.</li>
<li>Aucune intelligence artificielle dans le tri : les textes affichés sont des extraits des avis officiels. Les noms et coordonnées de personnes figurant dans les avis ne sont pas repris.</li>
</ul>
<h2>Limites</h2>
<ul>
<li>La liste n'est pas exhaustive : beaucoup de marchés à procédure adaptée (MAPA) ne sont publiés que sur le profil d'acheteur ou dans la presse locale, et n'apparaissent ni au BOAMP ni dans TED.</li>
<li>Le classement dépend des codes et descripteurs choisis par les acheteurs ; un contrat multiservices peut apparaître dans deux métiers, et quelques avis peu pertinents passer.</li>
<li>Les informations sont fournies à titre indicatif. Seuls l'avis officiel et le dossier de consultation font foi.</li>
</ul>
<h2>Données</h2>
<p>Les données sont téléchargeables librement : <a href="{self.link(CSV_FILE)}" data-mesure="free_download">export CSV</a> des appels d'offres ouverts, <a href="{self.link(ICS_FILE)}" data-mesure="free_download">calendrier des dates limites</a> (.ics) et la liste du jour dans <a href="{self.link('data/ouverts.json')}">data/ouverts.json</a>, mis à jour à chaque relevé.</p>
"""
        self.write("methode/", self.page("methode/", "Méthode et sources – Veille AO",
                                         "Sources (BOAMP, TED), licences, codes CPV et descripteurs retenus pour le nettoyage, "
                                         "les espaces verts et la sécurité privée, ce qui est écarté et pourquoi.", body))

    def legal(self) -> None:
        p = PUBLISHER
        mentions = f"""
<h1>Mentions légales</h1>
<h2>Éditeur</h2>
<p>{BRAND} est un service édité par {e(p['name'])} (société par actions simplifiée au capital de 1 000 €), SIREN {e(p['siren'])}, RCS Paris, siège social : {e(p['address'])}. Informations issues du <a href="https://annuaire-entreprises.data.gouv.fr/entreprise/988741773">registre public</a>.</p>
<p>Jalonel est une marque de READ FRANCE SAS.</p>
<p>Directeur de la publication : {e(p['president'])}, président.</p>
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a>.</p>
<h2>Hébergement</h2>
<p>GitHub Inc. (GitHub Pages), 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis (<a href="https://github.com">github.com</a>).</p>
<h2>Données et propriété intellectuelle</h2>
<p>Les annonces proviennent du BOAMP (DILA, Licence Ouverte / Open Licence Etalab) et de TED (Office des publications de l'Union européenne, réutilisation autorisée par la décision 2011/833/UE). Elles sont sélectionnées, raccourcies et normalisées par nos soins. Les logos de TED et du BOAMP ne sont pas utilisés. Les avis officiels font foi.</p>
<p>Les informations publiées ne constituent pas un conseil juridique. Vérifiez toujours l'avis officiel et le dossier de consultation avant de répondre à un marché.</p>
"""
        self.write("mentions-legales/", self.page("mentions-legales/", "Mentions légales – Veille AO",
                                                  "Éditeur, hébergeur et sources de Veille AO.", mentions,
                                                  noindex="noindex, follow"), sitemap=False)
        exp = self.sales.products
        privacy = f"""
<h1>Confidentialité</h1>
<p>{'La consultation du site ne collecte aucune donnée personnelle : pas de compte, pas de cookie ni de traceur tiers ; la fréquentation et l’usage du site sont seulement mesurés de façon anonyme et agrégée (voir ci-dessous). Une seule exception : le formulaire d’abonnement aux alertes e-mail, qui charge le module de paiement de Stripe et ses cookies quand vous commencez à le remplir, et qui traite les données de la commande (voir plus bas).' if self.sales.open else 'Ce site ne collecte aucune donnée personnelle : pas de compte, pas de formulaire, pas de cookie ni de traceur tiers ; seule une mesure anonyme et agrégée de la fréquentation et de l’usage du site est effectuée (voir ci-dessous). Aucun bandeau cookies n’est donc nécessaire.'}</p>
<ul>
<li><strong>Hébergement</strong> : GitHub Pages traite des journaux techniques (adresse IP notamment) pour servir le site et le sécuriser, selon la <a href="https://docs.github.com/fr/site-policy/privacy-policies/github-general-privacy-statement">déclaration de confidentialité de GitHub</a>.</li>
<li><strong>Téléchargements</strong> : les exports CSV/ICS sont servis par le site (GitHub Pages) ; seul leur nombre total est compté, par la mesure anonyme décrite ci-dessous.</li>
<li><strong>Annonces</strong> : nous ne reprenons pas les noms ni les coordonnées des personnes de contact figurant dans les avis publics ; seul le nom de l'acheteur public est affiché.</li>
</ul>
<h2>Mesure d'audience et de comportement (version du 13 octobre 2026)</h2>
<p>Nous mesurons la fréquentation et l'usage de ce site de façon anonyme et agrégée, pour ses seules statistiques et pour l'améliorer. Seuls des totaux journaliers sont conservés, dans un tableur de READ FRANCE hébergé par Google :</p>
<ul>
<li><strong>Audience</strong> : pages vues, clics vers la page de commande et étapes de la commande, avec le domaine du site d'origine et les paramètres de campagne (utm) éventuels.</li>
<li><strong>Comportement</strong> : profondeur de défilement de la page, sections atteintes, temps écoulé avant le premier clic sur un bouton, formulaires commencés et envoyés et le <em>nom</em> du dernier champ touché (jamais son contenu : rien de ce que vous saisissez n'est lu), questions de la FAQ ouvertes, liens sortants (seulement le domaine visé), erreurs techniques (une empreinte anonyme du message), vitesse d'affichage (Core Web Vitals) et clics répétés au même endroit.</li>
<li><strong>Ce que nous ne faisons pas</strong> : aucun cookie, aucun identifiant, aucune adresse IP conservée, aucune donnée personnelle, aucun suivi d'un site à l'autre, aucun recoupement avec d'autres données ; rien n'est partagé ni vendu.</li>
<li><strong>Pour refuser</strong> : activez « Do Not Track » ou « Global Privacy Control » dans votre navigateur ; plus rien n'est alors mesuré. Les robots d'indexation et les navigateurs automatisés sont comptés à part ou pas du tout, et une page préchargée n'est comptée que si vous l'ouvrez.</li>
</ul>
<p>Strictement agrégée, limitée aux statistiques propres de ce site, sans suivi d'un site à l'autre, sans recoupement avec d'autres traitements et sans transmission à des tiers, cette mesure relève de l'exemption de consentement prévue pour la mesure d'audience (article 82 de la loi Informatique et Libertés, lignes directrices et recommandation de la CNIL du 17 septembre 2020 sur les cookies et autres traceurs) : aucun bandeau de consentement n'est donc nécessaire.</p>
{shop.privacy_section(self) if self.sales.open else ''}
<p>Contact : <a href="mailto:{landing.CONTACT}">{landing.CONTACT}</a>.</p>
"""
        self.write("confidentialite/", self.page(
            "confidentialite/", "Confidentialité – Veille AO",
            "Aucun cookie ni traceur à la consultation ; mesure d'audience anonyme et agrégée ; données traitées seulement "
            "pour l'abonnement aux alertes." if self.sales.open else
            "Aucune donnée personnelle collectée, aucun cookie ni traceur ; mesure d'audience anonyme et agrégée.", privacy,
            noindex="noindex, follow"), sitemap=False)
        if self.sales.open:
            self.write("cgv-pro/", self.page(
                "cgv-pro/", "Conditions générales de vente aux professionnels – Veille AO",
                "CGV B2B des alertes e-mail (périodes prépayées de 3 ou 12 mois, sans renouvellement automatique).",
                shop.cgv_pro_body(self), noindex=True), sitemap=False)

    def not_found(self) -> None:
        body = f"""<h1>Page introuvable</h1><p>Cette page n'existe pas ou plus. <a href="{self.link()}">Voir les appels d'offres ouverts</a>.</p>"""
        self.write("404.html", self.page("404.html", "Page introuvable – Veille AO",
                                         "Page introuvable.", body, noindex=True), sitemap=False)

    def sitemap(self) -> None:
        urls = "".join(f"<url><loc>{e(self.url + p)}</loc><lastmod>{m}</lastmod></url>" for p, m in self.pages)
        (self.out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")
        (self.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {self.url}sitemap.xml\n",
                                             encoding="utf-8")
