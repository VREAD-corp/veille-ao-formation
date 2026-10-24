"""Landing-page content of the home page, the niche pages and /alertes/ (everything around the order form).

The order form itself (shop.order_body, between <section class="checkout"> and its closing tag) is kept
verbatim by `wrap_order`. Every number shown here is computed from the notices of the build.

Prices: the 12-month plan is under an A/B price test (sales.PRICE_XP). Outside /alertes/ the copy only quotes
the 3-month price (« dès 49 € »), the same in both arms, so no page contradicts the arm a visitor sees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Iterable

from .digest import Profile, is_national, select
from .exporters import fmt_date, fmt_deadline
from .geo import REGIONS, slugify
from .model import NICHES, PARIS, Notice
from .sales import PLAN_ARMS, eur_short

CONTACT = "contact@jalonel.com"
BRAND = "Veille AO"
VOLUME_DAYS = 30
VAT_LINE = "TVA non applicable, art. 293 B du CGI"
MAPA_FAMILY = "Marchés <90 k€ (MAPA)"
# Sample e-mail: one niche and two regions, the shortest recent window giving at least SAMPLE_MIN notices.
SAMPLE_PROFILES = (("nettoyage", ("Île-de-France", "Hauts-de-France")), ("nettoyage", ("Auvergne-Rhône-Alpes", "Occitanie")),
                   ("espaces-verts", ("Île-de-France", "Normandie")))
SAMPLE_MIN, SAMPLE_MAX = 8, 15
# Stable ids of the /alertes/ FAQ items, in question order (cro.js counts which ones are opened).
FAQ_IDS = ("boamp-gratuit", "volume", "criteres", "mapa", "facture-tva", "fin-periode")


def e(text: object) -> str:
    return escape("" if text is None else str(text), quote=True)


# -- data ----------------------------------------------------------------------------------------------


@dataclass
class Volume:
    """Notices first published in the last VOLUME_DAYS days (one per procedure, cancelled ones left out)."""

    since: str
    total: int
    national: int
    local: dict[str, int]  # region -> notices located there (national ones not included)
    items: list[list[int]]  # region indexes of each non-national notice, for client-side unions
    mapa: int
    ted_only: int
    median_days: int  # median days between publication and deadline (notices with a deadline)

    def for_region(self, region: str) -> int:
        return self.local.get(region, 0) + self.national

    @property
    def per_week(self) -> int:
        return round(self.total * 7 / VOLUME_DAYS)

    def json(self) -> str:
        return json.dumps({"since": self.since, "nat": self.national, "items": self.items,
                           "regions": [[r, slugify(r), self.for_region(r)] for r in REGIONS]},
                          ensure_ascii=False, separators=(",", ":"))


def volume(tenders: Iterable[Notice], now: datetime) -> Volume:
    since = (now.astimezone(PARIS).date() - timedelta(days=VOLUME_DAYS)).isoformat()
    recent = [t for t in tenders if t.published >= since and t.status != "annule"]
    index = {r: i for i, r in enumerate(REGIONS)}
    local = {r: 0 for r in REGIONS}
    items = []
    national = 0
    for t in recent:
        if is_national(t):
            national += 1
            continue
        regs = [r for r in t.regions if r in index]
        for r in regs:
            local[r] += 1
        items.append(sorted(index[r] for r in regs))
    mapa = sum(1 for t in recent if t.family == MAPA_FAMILY)
    ted_only = sum(1 for t in recent if t.source == "TED" and not any(k.startswith("BOAMP") for k in t.alt_urls))
    delays = sorted((date.fromisoformat(t.deadline[:10]) - date.fromisoformat(t.published)).days
                    for t in recent if t.deadline)
    median_days = delays[len(delays) // 2] if delays else 0
    return Volume(since, len(recent), national, local, items, mapa, ted_only, median_days)


@dataclass
class Sample:
    profile: Profile
    since: str
    items: list[Notice]
    weekly: int  # average notices per week for this profile over the last VOLUME_DAYS days

    @property
    def niche_short(self) -> str:
        return NICHES[self.profile.niches[0]].short

    @property
    def subject(self) -> str:
        n = len(self.items)
        return (f"{n} {'nouveaux appels' if n > 1 else 'nouvel appel'} d'offres {self.niche_short} – "
                f"{', '.join(self.profile.regions)}")


def sample(open_items: list[Notice], tenders: list[Notice], now: datetime) -> Sample:
    today = now.astimezone(PARIS).date()
    best = None
    for niche, pair in SAMPLE_PROFILES:
        profile = Profile(name=f"Exemple : {NICHES[niche].short}, {' + '.join(pair)}", niches=[niche],
                          regions=list(pair))
        for days in (3, 5, 7, 10, 14, 21, 30):
            since = (today - timedelta(days=days)).isoformat()
            picked = select(open_items, profile, now, since=since)
            if best is None or len(picked) > len(best[2]):
                best = (profile, since, picked)
            if len(picked) >= SAMPLE_MIN:
                best = (profile, since, picked)
                break
        if len(best[2]) >= SAMPLE_MIN:
            break
    profile, since, picked = best
    vol_since = (today - timedelta(days=VOLUME_DAYS)).isoformat()
    month = [t for t in tenders if t.published >= vol_since and t.status != "annule"
             and set(profile.niches) & set(t.niches)
             and (is_national(t) or set(profile.regions) & set(t.regions))]
    return Sample(profile, since, picked, round(len(month) * 7 / VOLUME_DAYS))


# -- shared blocks ---------------------------------------------------------------------------------------


def _place(t: Notice) -> str:
    if is_national(t):
        return "National / plusieurs régions"
    deps = f" ({', '.join(t.departments[:3])}{'…' if len(t.departments) > 3 else ''})" if t.departments else ""
    return ", ".join(t.regions) + deps


def _value(t: Notice) -> str:
    if not t.estimated_value:
        return ""
    return f"≈ {t.estimated_value:,.0f} €".replace(",", " ")


def mail_items(items: list[Notice], now: datetime, *, links: bool) -> str:
    rows = []
    for t in items:
        days = t.days_left(now)
        badge = (f'<span class="mi-badge{" urgent" if days is not None and days <= 7 else ""}">J-{days}</span>'
                 if days is not None else "")
        title = (f'<a href="{e(t.url)}" rel="noopener">{e(t.title)}</a>' if links else e(t.title))
        meta = [m for m in (_place(t), t.cpv_main_label(), _value(t)) if m]
        rows.append(
            f'<li><div class="mi-top"><p class="mi-title">{title}</p>{badge}</div>'
            f'<p class="mi-buyer">{e(t.buyer)}</p>'
            f'<p class="mi-meta">{" · ".join(e(m) for m in meta)}</p>'
            f'<p class="mi-dl">Date limite : <strong>{e(fmt_deadline(t.deadline)) if t.deadline else "non précisée"}</strong>'
            f' · avis {e(t.source)}</p></li>')
    return "".join(rows)


def mail_mock(s: Sample, now: datetime, *, compact: bool) -> str:
    """The alert e-mail as an HTML mockup. compact=True: phone screen, 4 notices, no links (decorative)."""
    items = s.items[:4] if compact else s.items[:SAMPLE_MAX]
    more = len(s.items) - len(items)
    foot = (f'<p class="mail-more">+ {more} autre{"s" if more > 1 else ""} avis dans cet e-mail</p>' if more > 0 else "")
    head = f"""<div class="mail-head">
<p class="mail-from"><span class="mail-avatar" aria-hidden="true">AO</span><span><strong>{BRAND}</strong><br><span class="mail-time">ce matin, vers 7 h</span></span></p>
<p class="mail-subject">{e(s.subject)}</p>
<p class="mail-profile">Vos critères : {e(s.niche_short)} · {e(' + '.join(s.profile.regions))} · avis nationaux inclus</p>
</div>"""
    body = f"""{head}
<ol class="mail-items">{mail_items(items, now, links=not compact)}</ol>
{foot}<p class="mail-foot">Par date limite · lien vers chaque avis officiel · <span class="mail-stop">Arrêter les alertes</span></p>"""
    if compact:
        return f"""<div class="phone" role="img" aria-label="Aperçu d'un e-mail d'alerte sur téléphone : {e(s.subject)}">
<div class="phone-screen" aria-hidden="true"><div class="phone-notch"></div><div class="mail-mock compact">{body}</div></div>
</div>"""
    return f"""<div class="mail-window"><div class="mail-chrome" aria-hidden="true"><span></span><span></span><span></span><em>Boîte de réception</em></div>
<div class="mail-mock">{body}</div></div>"""


def sample_caption(s: Sample, now: datetime) -> str:
    end = fmt_date(now.astimezone(PARIS).date().isoformat())
    return (f"Exemple construit avec les {len(s.items)} avis réels encore ouverts publiés du {fmt_date(s.since)} "
            f"au {end} en {s.niche_short} pour {' + '.join(s.profile.regions)}, sans mot-clé (mise en page indicative). "
            f"Un vrai e-mail du matin ne contient que les avis repérés depuis la veille : pour ce métier et ces deux régions, "
            f"environ {s.weekly} par semaine ces {VOLUME_DAYS} derniers jours.")


def about(site) -> str:
    return f"""<section class="about" id="qui-sommes-nous" aria-labelledby="about-h">
<div class="about-intro">
<p class="eyebrow">Qui publie ce site</p>
<h2 id="about-h">Jalonel, Paris</h2>
<p>{BRAND} est un service <strong>Jalonel</strong>, une marque de READ FRANCE SAS (SIREN 988 741 773, 11 rue Rouvet, 75019 Paris). Les annonces sont relevées et triées par un programme, sans intelligence artificielle ; Octave, l'assistant IA de READ FRANCE, a écrit ce programme et les textes du site, relus avant publication.</p>
<p class="about-foot">Une question avant de vous abonner, un métier à ajouter ? Écrivez à <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
</div>
</section>"""


def free_formats(site, release_csv: str, release_ics: str) -> str:
    return f"""<details class="formats"><summary>Autres formats gratuits ▾</summary>
<ul>
<li><a href="{site.link('rss.xml')}" data-mesure="free_download">Flux RSS</a> : les nouvelles annonces dans votre lecteur de flux.</li>
<li><a href="{release_csv}" data-mesure="free_download">Tableau des avis ouverts (CSV)</a> : mis à jour deux fois par jour, s'ouvre dans Excel.</li>
<li><a href="{release_ics}" data-mesure="free_download">Calendrier des dates limites (.ics)</a> : Google Agenda, Outlook, Apple Calendrier.</li>
<li class="muted small">Pour les développeurs : <a href="{site.link('data/ouverts.json')}" data-mesure="free_download">fichier de données ouvertes (JSON)</a>.</li>
</ul>
</details>"""


def price(cents: int) -> str:
    """'49 €' with a non-breaking space, so the amount never wraps away from its currency."""
    return eur_short(cents).replace(" ", " ")


def per_month(cents: int, months: int) -> str:
    v = round(cents / months)
    return f"{v // 100},{v % 100:02d} €"


def entry_price(site) -> str:
    return price(site.sales.products["lot-repere-3m"])


# -- home and niche pages -------------------------------------------------------------------------------


def niche_tiles(site, open_counts: dict[str, int], vols: dict[str, Volume]) -> str:
    tiles = []
    for slug, n in NICHES.items():
        tiles.append(f"""<li class="who-card"><p class="who-title"><a href="{site.link(slug + '/')}">{e(n.label)}</a></p>
<p><strong>{open_counts[slug]}</strong> appels d'offres ouverts aujourd'hui, environ {vols[slug].per_week} nouveaux par semaine (moyenne des {VOLUME_DAYS} derniers jours).</p></li>""")
    return f'<ul class="who niche-tiles" aria-label="Les trois métiers suivis">{"".join(tiles)}</ul>'


def home_hero(site, n: int, sources_note: str) -> str:
    """Home first screen: H1, one sentence, a short link to the paid alerts; the filters and the list follow
    right after it (JALONEL-HUB-02, 2026-10-19: the tool is visible without scrolling on a phone)."""
    if site.sales.open:
        cta = (f'<p class="hero-alert"><a href="{site.link("alertes/")}" data-cta>Recevoir les nouveaux avis de ma région chaque matin →</a> '
               f'<span>dès {e(entry_price(site))} pour 3 mois</span></p>')
    else:
        cta = f'<p class="hero-alert"><a href="{site.link("rss.xml")}" data-mesure="free_download">Suivre les nouvelles annonces (flux RSS)</a></p>'
    return f"""<section class="hero hero-tool" id="hero">
<h1>Appels d'offres nettoyage, espaces verts et sécurité privée ouverts</h1>
<p class="lead"><strong>{n}</strong> marchés publics ouverts (BOAMP et TED), en accès libre, mis à jour le {site.updated_label()}.</p>
{cta}
{sources_note}
</section>"""


def home_after_list(site, n: int, open_counts: dict[str, int], s: Sample, release_csv: str, release_ics: str) -> str:
    """Under the list: what used to be in the hero (counts per trade, alerts pitch with prices, e-mail preview,
    free formats)."""
    if site.sales.open:
        cta = (f'<a class="btn btn-lg" href="{site.link("alertes/")}" data-cta>Recevoir les nouveaux avis de ma région chaque matin →</a>'
               f'<p class="price-note">Alertes e-mail dès {e(entry_price(site))} pour 3 mois, payés une fois, '
               f'sans renouvellement automatique. {VAT_LINE}. Envois interrompus plus de 7 jours de notre fait : '
               f'période prolongée ou remboursée au prorata (<a href="{site.link("cgv-pro/")}#art-9">CGV, art. 9</a>).</p>')
    else:
        cta = f'<a class="btn btn-lg" href="{site.link("rss.xml")}" data-mesure="free_download">Suivre les nouvelles annonces (flux RSS)</a>'
    counts = ", ".join(f'<a href="{site.link(k + "/")}">{open_counts[k]} en {e(NICHES[k].short)}</a>' for k in NICHES)
    return f"""<section class="hero-split after-list" id="apercu-alertes" aria-labelledby="apercu-h">
<div class="hero-copy">
<h2 id="apercu-h">D'où viennent ces annonces</h2>
<p>{n} marchés publics en cours en France ({counts}), relevés dans le BOAMP et dans TED, le journal européen des marchés publics, deux fois par jour. La liste est gratuite et sans inscription ; les alertes e-mail sont payantes.</p>
<div class="cta-block">{cta}</div>
{free_formats(site, release_csv, release_ics)}
</div>
<div class="hero-visual">{mail_mock(s, site.now, compact=True)}<p class="visual-cap">Aperçu des alertes e-mail, avec de vrais avis récents</p></div>
</section>"""


def missed_by_free(site, vol: Volume, dup: int) -> str:
    """What free alerts miss: only facts computed from this build's data."""
    return f"""<section class="section" id="gratuit" aria-labelledby="gratuit-h">
<h2 id="gratuit-h">Ce que les alertes gratuites ne font pas pour vous</h2>
<p>Les alertes du BOAMP sont gratuites et utiles. Elles portent sur les avis du BOAMP. Sur les {vol.total} avis de nos trois métiers publiés ces {VOLUME_DAYS} derniers jours, <strong>{vol.ted_only}</strong> ne venaient que de TED (acheteurs français publiant au journal européen) et <strong>{dup}</strong> marchés étaient parus à la fois au BOAMP et dans TED : ici, ils n'apparaissent qu'une fois, avec les deux liens.</p>
<p>La liste est triée par date limite, filtrable par région (lieu d'exécution, pas siège de l'acheteur) et par métier. Le tri par métier suit des codes CPV et des descripteurs publiés sur la page <a href="{site.link('methode/')}">Méthode</a>.</p>
</section>"""


def inline_cta_template(site) -> str:
    """Cloned by static/app.js after the 5th card and every 20 cards, tailored to the region and niche filters."""
    return f"""<template id="cta-tpl"><aside class="inline-cta" aria-label="Alertes e-mail">
<div><p class="ic-title">Les nouveaux appels d'offres de votre région, chaque matin</p>
<p class="ic-text">Choisissez vos métiers, vos régions et vos mots-clés : un e-mail par matin, dès {e(entry_price(site))} pour 3 mois, sans renouvellement automatique.</p></div>
<a class="btn" href="{site.link('alertes/')}" data-cta>Recevoir les nouveaux avis chaque matin →</a>
</aside></template>"""


def band(site, niche: str | None = None) -> str:
    if not site.sales.open:
        return ""
    href = site.link("alertes/") + (f"?niche={niche}" if niche else "")
    what = f"en {NICHES[niche].short}" if niche else "de vos métiers"
    return f"""<section class="band" id="offre" aria-labelledby="band-h">
<div><h2 id="band-h">Les nouveaux avis {e(what)} par e-mail, vers 7&nbsp;h</h2>
<p>Seulement les avis de vos régions et de vos mots-clés, avec la date limite et le lien vers l'avis officiel. Dès {e(entry_price(site))} pour 3 mois, payés une fois, {VAT_LINE}.</p></div>
<a class="btn btn-lg btn-light" href="{href}" data-cta>Voir un exemple d'e-mail et les prix →</a>
</section>"""


# -- /alertes/ -------------------------------------------------------------------------------------------


def xp_price(site, *, per: int | None = None) -> str:
    """The 12-month price under test: arm a text by default, static/xp-price.js swaps in arm b."""
    p = site.sales.products
    a, b = p[PLAN_ARMS["a"]], p[PLAN_ARMS["b"]]
    if per:
        return f'<span class="xp-price" data-a="{e(per_month(a, per))}" data-b="{e(per_month(b, per))}">{e(per_month(a, per))}</span>'
    return f'<span class="xp-price" data-a="{e(price(a))}" data-b="{e(price(b))}">{e(price(a))}</span>'


def alerts_top(site, s: Sample, vols: dict[str, Volume], vol_all: Volume) -> str:
    q = site.sales.products["lot-repere-3m"]
    regions_opts = "".join(f'<option value="{slugify(r)}">{e(r)}</option>' for r in REGIONS)
    lines = " ; ".join(f"{e(NICHES[k].label.lower())} {vols[k].total}" for k in NICHES)
    table = "".join(f"<li><span>{e(r)}</span><strong>{vol_all.for_region(r)}</strong></li>"
                    for r in sorted(REGIONS, key=vol_all.for_region, reverse=True))
    return f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{site.link()}">Accueil</a> › Alertes e-mail</nav>
<section class="hero hero-split" id="hero">
<div class="hero-copy">
<p class="eyebrow">Alertes e-mail · BOAMP + TED</p>
<h1>Les appels d'offres nettoyage, espaces verts ou sécurité de votre région, chaque matin</h1>
<p class="lead">Vers 7&nbsp;h, les avis BOAMP et TED publiés depuis la veille pour vos métiers, vos régions et vos mots-clés. {xp_price(site)} pour 12 mois, soit {xp_price(site, per=12)} par mois, payé une fois, sans renouvellement automatique.</p>
<div class="cta-block">
<a class="btn btn-lg" href="#commander-h" data-cta><span>Recevoir mes alertes, {xp_price(site)} pour 12 mois</span></a>
<p class="price-note">ou {e(price(q))} pour 3 mois · prix nets, {VAT_LINE} · réservé aux entreprises immatriculées en France · paiement par carte, facture PDF</p>
<p class="price-note">Envois interrompus plus de 7 jours de notre fait : période prolongée ou remboursée au prorata (<a href="{site.link('cgv-pro/')}#art-9">CGV, art. 9</a>). Pas de droit de rétractation entre professionnels (art. 10).</p>
</div>
<p class="hero-links"><a href="{site.link()}">Ou consulter gratuitement la liste des annonces →</a></p>
</div>
<div class="hero-visual">{mail_mock(s, site.now, compact=True)}<p class="visual-cap">Un exemple avec de vrais avis récents</p></div>
</section>

<section class="section" id="difference" aria-labelledby="diff-h">
<h2 id="diff-h">Ce que l'e-mail ajoute à la liste gratuite</h2>
<div class="compare">
<div class="compare-col">
<p class="compare-tag">Liste gratuite</p>
<p class="compare-title">Vous venez vérifier</p>
<ul>
<li>À chaque visite, vous refaites vos filtres parmi les {vol_all.total} avis publiés ces {VOLUME_DAYS} derniers jours dans les trois métiers.</li>
<li>Un avis vu une semaine après sa parution vous laisse une semaine de moins pour répondre. Entre parution et date limite, le délai médian est de {vol_all.median_days} jours (avis publiés depuis le {fmt_date(vol_all.since)}).</li>
</ul>
<a href="{site.link()}">Voir la liste gratuite</a>
</div>
<div class="compare-col pro">
<p class="compare-tag">Alertes e-mail</p>
<p class="compare-title">Les nouveaux avis arrivent déjà triés</p>
<ul>
<li>Un e-mail vers 7&nbsp;h avec seulement les <strong>nouveaux</strong> avis de vos métiers, de vos régions et de vos mots-clés.</li>
<li>Le premier e-mail, envoyé à l'inscription, liste tous les avis encore ouverts qui correspondent.</li>
<li>Vos mots-clés sont cherchés jusque dans l'intitulé des lots (« vitrerie », « SSIAP », « élagage »).</li>
<li>Pas de compte ni d'application. Chaque e-mail a un lien « arrêter les alertes » et aucun pixel de suivi.</li>
</ul>
<a href="#commander-h">Choisir mes critères</a>
</div>
</div>
</section>

<section class="section" id="exemple" aria-labelledby="recu-h">
<h2 id="recu-h">Ce que vous recevez</h2>
<div class="get-grid">
<div>{mail_mock(s, site.now, compact=False)}
<p class="caption">{e(sample_caption(s, site.now))} <a href="{site.link('alertes/exemple-email.html')}" data-mesure="free_download">Version texte brut de l'exemple</a>.</p></div>
<ul class="checks">
<li>Les avis du BOAMP et de TED, relevés deux fois par jour.</li>
<li>Seulement des marchés de services dans vos métiers : codes CPV du métier, descripteur BOAMP du métier, ou mot-clé quand l'avis n'a pas de code. Les marchés de travaux et de fournitures sont écartés (une auto-laveuse ou des caméras ne sont pas des prestations).</li>
<li>Un marché publié au BOAMP et dans TED, ou rectifié, n'arrive qu'une fois.</li>
<li>Pour chaque avis : acheteur, lieu, date limite, montant estimé s'il est publié, lien vers l'avis officiel.</li>
</ul>
</div>
</section>

<section class="section offer" id="prix" aria-labelledby="offre-h">
<h2 id="offre-h">Une seule offre, deux durées</h2>
<div class="plans">
<div class="plan-card best"><p class="plan-tag">Pour une année de veille</p><p class="plan-name">12 mois</p><p class="plan-price">{xp_price(site)}</p><p class="plan-sub">soit {xp_price(site, per=12)} par mois, payé une fois</p><a class="btn" href="#commander-h" data-cta>Choisir 12 mois</a></div>
<div class="plan-card"><p class="plan-tag">Pour essayer sur un trimestre</p><p class="plan-name">3 mois</p><p class="plan-price">{e(price(q))}</p><p class="plan-sub">soit {e(per_month(q, 3))} par mois, payé une fois</p><a class="btn btn-ghost" href="#commander-h" data-cta>Choisir 3 mois</a></div>
</div>
<ul class="checks inline">
<li>Un, deux ou trois métiers, autant de régions que vous voulez, jusqu'à 10 mots-clés</li>
<li>Pas de renouvellement ni de prélèvement automatique : rappel 7 jours avant la fin</li>
<li>Facture PDF au nom de votre entreprise</li>
<li>Envois interrompus plus de 7 jours de notre fait : période prolongée ou remboursée (<a href="{site.link('cgv-pro/')}">CGV</a>, art. 9)</li>
</ul>
<p class="small muted">Prix nets, {VAT_LINE}. Paiement unique par carte bancaire.</p>

<div class="estimator" id="volume" data-volume="{e(vol_all.json())}">
<h3>Combien d'avis pour vous ?</h3>
<p class="small muted">Avis publiés ces {VOLUME_DAYS} derniers jours (depuis le {fmt_date(vol_all.since)}) : {lines}. Chiffres par région ci-dessous pour les trois métiers ensemble, avis nationaux compris, avant filtrage par mots-clés.</p>
<label class="est-field" for="vol-region">Votre région</label>
<select id="vol-region"><option value="">Choisir une région</option>{regions_opts}</select>
<p id="vol-out" class="est-out" aria-live="polite">Toute la France : <strong>{vol_all.total}</strong> avis en {VOLUME_DAYS} jours, trois métiers ensemble.</p>
<p id="vol-form" class="est-out small" aria-live="polite" hidden></p>
<details><summary>Toutes les régions</summary><ul class="est-table">{table}</ul></details>
</div>
</section>
"""


def faq(site, vol: Volume) -> str:
    p = site.sales.products
    qa = [
        ("Pourquoi payer alors que le BOAMP propose des alertes gratuites ?",
         f"""<p>Les alertes du BOAMP sont gratuites : un compte sur boamp.fr, jusqu'à 10 alertes sur les avis qu'il diffuse (<a href="https://www.boamp.fr/telechargements/cms/fichiers/CGU_site_boamp.fr_et_service_d_alerte_BOAMP_2024-10-21.pdf" rel="noopener">conditions d'utilisation de la DILA</a>). Bien réglées, elles couvrent une partie du besoin. Ce que nous ajoutons :</p>
<ul>
<li>le tri par métier est déjà fait (codes CPV, descripteurs, mots-clés), travaux et fournitures écartés ;</li>
<li>les avis publiés dans TED par des acheteurs français, dans le même e-mail ({vol.ted_only} des {vol.total} avis de ces {VOLUME_DAYS} derniers jours ne venaient que de TED) ;</li>
<li>un même marché publié au BOAMP et dans TED, ou rectifié, n'arrive qu'une fois.</li>
</ul>"""),
        ("Combien d'avis vais-je recevoir ?",
         f"""<p>Cela dépend de vos métiers et de vos régions. Ces {VOLUME_DAYS} derniers jours, {vol.total} avis ont été publiés en France dans les trois métiers (doublons fusionnés) : voir <a href="#volume">l'estimation par région</a>. Vous recevez un e-mail les matins où il y a du nouveau ; après 7 jours sans nouvel avis, un court e-mail « rien de nouveau ».</p>"""),
        ("Puis-je changer mes critères ?",
         """<p>Oui. Répondez à n'importe quel e-mail d'alerte avec vos nouveaux métiers, régions ou mots-clés : la modification est faite sous 2 jours ouvrés. Le lien « arrêter les alertes » de chaque e-mail permet aussi une pause, puis une reprise jusqu'à la fin de la période.</p>"""),
        ("Et les petits marchés (MAPA) ?",
         f"""<p>Les marchés à procédure adaptée publiés au BOAMP sont inclus ({vol.mapa} des {vol.total} avis de ces {VOLUME_DAYS} derniers jours étaient des marchés de moins de 90&nbsp;000&nbsp;€). Beaucoup de petits contrats d'entretien ne paraissent que sur la plateforme de l'acheteur ou dans un journal d'annonces légales : <strong>ceux-là ne sont pas dans nos alertes</strong>. Gardez un œil sur les plateformes de vos acheteurs habituels.</p>"""),
        ("Facture et TVA ?",
         """<p>La facture PDF arrive avec l'e-mail de bienvenue, au nom de l'entreprise et du SIREN indiqués à la commande, avec les dates de la période. READ FRANCE SAS relève de la franchise en base de TVA : aucune TVA n'est facturée, le prix affiché est le montant total payé.</p>"""),
        ("Que se passe-t-il à la fin de la période ?",
         """<p>Rien n'est prélevé. Vous recevez un rappel environ 7 jours avant la fin, puis les alertes s'arrêtent. Pour continuer, vous commandez une nouvelle période ; commandée avant la fin, elle démarre le lendemain de la période en cours.</p>"""),
    ]
    items = "\n".join(f"<details class=\"faq-item\" id=\"faq-{fid}\"><summary>{q}</summary>{a}</details>"
                      for fid, (q, a) in zip(FAQ_IDS, qa, strict=True))
    return f"""<section class="section faq" id="faq" aria-labelledby="faq-h">
<h2 id="faq-h">Questions fréquentes</h2>
{items}
<p class="small muted">Les règles complètes sont dans les <a href="{site.link('cgv-pro/')}">conditions générales de vente</a>. Une autre question : <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
</section>"""


def faq_items(site, vol: Volume) -> list[tuple[str, str]]:
    """(question, plain-text answer) pairs for the FAQPage JSON-LD of /alertes/."""
    import re
    html = faq(site, vol)
    out = []
    for q, a in re.findall(r'<summary>(.*?)</summary>(.*?)</details>', html, re.S):
        text = re.sub(r"<[^>]+>", "", a)
        out.append((re.sub(r"<[^>]+>", "", q), re.sub(r"\s+", " ", text).replace("&nbsp;", " ").strip()))
    return out


def alerts_bottom(site, vol: Volume) -> str:
    return f"""
{faq(site, vol)}
{about(site)}
<section class="section legal-note" id="limites" aria-labelledby="limites-h">
<h2 id="limites-h">Sources et limites</h2>
<p>Sélection automatique d'avis publiés au BOAMP (DILA) et dans TED (Office des publications de l'Union européenne), sans intelligence artificielle. Elle n'est pas exhaustive et repose sur les codes choisis par les acheteurs : vérifiez toujours l'avis officiel et le dossier de consultation. <a href="{site.link('methode/')}">Méthode et sources</a>.</p>
<p class="small muted">Service indépendant de Jalonel (marque de READ FRANCE SAS), ni affilié ni approuvé par la DILA ou l'Office des publications de l'UE.</p>
</section>
"""


def wrap_order(order_html: str, top: str, bottom: str) -> str:
    """Keep the order section (form included) verbatim; replace what comes before and after it."""
    start = order_html.find('<section class="checkout"')
    form_end = order_html.find("</form>", start)
    end = order_html.find("</section>", form_end)
    if start < 0 or form_end < 0 or end < 0:
        return top + order_html + bottom  # unexpected markup: keep everything rather than lose the form
    end += len("</section>")
    return top + order_html[start:end] + bottom


def sticky_bar(site, href: str) -> str:
    return (f'<div class="sticky-cta" id="sticky-cta"><span>Alertes e-mail <strong>dès {e(entry_price(site))}</strong></span>'
            f'<a class="btn" href="{href}" data-cta>Commander →</a></div>')
