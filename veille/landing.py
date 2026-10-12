"""Landing-page content of the home page and of /alertes/ (everything around the order form).

The order form itself (shop.order_body, between <section class="checkout"> and its closing tag) and the
checkout scripts are left untouched: `wrap_order` keeps that section verbatim and only replaces the content
before and after it. Every number shown here is computed from the notices of the build (data/notices.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Iterable

from .digest import Profile, is_national, select
from .exporters import fmt_date, fmt_deadline
from .geo import REGIONS, slugify
from .model import PARIS, Notice
from .sales import eur_short

CONTACT = "octave@vread.fr"
VOLUME_DAYS = 30
VAT_LINE = "TVA non applicable, art. 293 B du CGI"
MAPA_FAMILY = "Marchés <90 k€ (MAPA)"
# Sample e-mail: two regions, the shortest recent window giving at least SAMPLE_MIN notices.
SAMPLE_PAIRS = (("Auvergne-Rhône-Alpes", "Bretagne"), ("Île-de-France", "Normandie"), ("Île-de-France", "Hauts-de-France"))
SAMPLE_MIN, SAMPLE_MAX = 10, 15


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

    def for_region(self, region: str) -> int:
        return self.local.get(region, 0) + self.national

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
    return Volume(since, len(recent), national, local, items, mapa, ted_only)


@dataclass
class Sample:
    profile: Profile
    since: str
    items: list[Notice]
    weekly: int  # average notices per week for this profile over the last VOLUME_DAYS days

    @property
    def subject(self) -> str:
        n = len(self.items)
        return (f"{n} nouvel{'s' if n > 1 else ''} appel{'s' if n > 1 else ''} d'offres formation – "
                f"{', '.join(self.profile.regions)}")


def sample(open_items: list[Notice], tenders: list[Notice], now: datetime) -> Sample:
    today = now.astimezone(PARIS).date()
    best = None
    for pair in SAMPLE_PAIRS:
        profile = Profile(name=f"Exemple : {' + '.join(pair)}, tous thèmes", regions=list(pair))
        for days in (7, 10, 14, 21, 30):
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
    return f"≈ {t.estimated_value:,.0f} €".replace(",", " ")


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
<p class="mail-from"><span class="mail-avatar" aria-hidden="true">AO</span><span><strong>Veille AO Formation</strong><br><span class="mail-time">ce matin, vers 7 h</span></span></p>
<p class="mail-subject">{e(s.subject)}</p>
<p class="mail-profile">Vos critères : {e(' + '.join(s.profile.regions))} · tous thèmes · avis nationaux inclus</p>
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
            f"au {end} pour {' + '.join(s.profile.regions)}, sans mot-clé (mise en page indicative). "
            f"Un vrai e-mail du matin ne contient que les avis repérés depuis la veille : pour ces deux régions, "
            f"environ {s.weekly} par semaine ces {VOLUME_DAYS} derniers jours.")


def about(site) -> str:
    return f"""<section class="about" id="qui-sommes-nous" aria-labelledby="about-h">
<!-- TODO founder photo/name -->
<div class="about-mark" aria-hidden="true">RF</div>
<div>
<h2 id="about-h">Qui sommes-nous</h2>
<p><strong>READ FRANCE SAS</strong>, Paris (SIREN 988 741 773). Nous éditons des kits et des services pour les organismes de formation, les vendeurs en ligne et les PME ; Veille AO Formation en fait partie.</p>
<p>Une question avant de vous abonner, une suggestion ? Écrivez-nous : <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
</div>
</section>"""


def free_formats(site, release_csv: str, release_ics: str) -> str:
    return f"""<details class="formats"><summary>Autres formats gratuits ▾</summary>
<ul>
<li><a href="{site.link('rss.xml')}" data-mesure="free_download">Flux RSS</a> : les nouvelles annonces dans votre lecteur de flux.</li>
<li><a href="{release_csv}" data-mesure="free_download">Tableau de la semaine (CSV)</a> : publié chaque lundi, s'ouvre dans Excel.</li>
<li><a href="{release_ics}" data-mesure="free_download">Calendrier des dates limites (.ics)</a> : Google Agenda, Outlook, Apple Calendrier.</li>
<li><a href="{site.link('region/')}">Pages par région</a> et <a href="{site.link('semaine/')}">archives par semaine</a>.</li>
<li class="muted small">Pour les développeurs : <a href="{site.link('data/ouverts.json')}" data-mesure="free_download">fichier de données ouvertes</a>.</li>
</ul>
</details>"""


# -- home page -------------------------------------------------------------------------------------------


def home_hero(site, n: int, s: Sample, release_csv: str, release_ics: str, sources_note: str) -> str:
    p = site.sales.products
    if site.sales.open:
        cta = (f'<a class="btn btn-lg" href="{site.link("alertes/")}">Recevoir les AO formation de ma région chaque matin →</a>'
               f'<p class="price-note">Alertes e-mail : {e(price(p["veille-ao-3m"]))} pour 3 mois ou '
               f'{e(price(p["veille-ao-12m"]))} pour 12 mois, payés une fois, sans renouvellement automatique. '
               f'{VAT_LINE}.</p>')
    else:
        cta = f'<a class="btn btn-lg" href="{site.link("rss.xml")}" data-mesure="free_download">Suivre les nouvelles annonces (flux RSS)</a>'
    return f"""<section class="hero hero-split">
<div class="hero-copy">
<p class="eyebrow">Marchés publics · formation professionnelle</p>
<h1>Appels d'offres formation ouverts</h1>
<p class="lead"><strong>{n}</strong> marchés publics de formation en cours en France, repérés dans le BOAMP et dans TED (journal européen). Mis à jour le {site.updated_label()}, deux fois par jour. Gratuit, sans inscription.</p>
<div class="cta-block">{cta}</div>
<p class="hero-links"><a href="#liste-titre">Parcourir les {n} annonces ↓</a></p>
{free_formats(site, release_csv, release_ics)}
{sources_note}
</div>
<div class="hero-visual">{mail_mock(s, site.now, compact=True)}<p class="visual-cap">Aperçu des alertes e-mail, avec de vrais avis récents</p></div>
</section>"""


def inline_cta_template(site) -> str:
    """Cloned by static/app.js after the 5th card and every 20 cards, tailored to the region filter."""
    p = site.sales.products
    return f"""<template id="cta-tpl"><aside class="inline-cta" aria-label="Alertes e-mail">
<div><p class="ic-title">Recevez chaque matin les nouveaux appels d'offres de votre région</p>
<p class="ic-text">Choisissez vos régions et vos mots-clés : un e-mail par matin, dès {e(price(p['veille-ao-3m']))} pour 3 mois, sans renouvellement automatique.</p></div>
<a class="btn" href="{site.link('alertes/')}">Recevoir les AO formation de ma région chaque matin →</a>
</aside></template>"""


def home_bottom(site) -> str:
    if not site.sales.open:
        return about(site)
    p = site.sales.products
    return f"""<section class="band" aria-labelledby="band-h">
<div><h2 id="band-h">Ne revenez plus vérifier : les nouveaux avis viennent à vous</h2>
<p>Chaque matin vers 7&nbsp;h, uniquement les avis de vos régions et de vos mots-clés, avec la date limite et le lien vers l'avis officiel. {e(price(p['veille-ao-12m']))} pour 12 mois, soit {e(per_month(p['veille-ao-12m'], 12))} par mois.</p></div>
<a class="btn btn-lg btn-light" href="{site.link('alertes/')}">Voir les alertes e-mail →</a>
</section>
{about(site)}"""


def per_month(cents: int, months: int) -> str:
    v = round(cents / months)
    return f"{v // 100},{v % 100:02d}\u00a0€"


def price(cents: int) -> str:
    """'49 €' with a non-breaking space, so the amount never wraps away from its currency."""
    return eur_short(cents).replace(" ", "\u00a0")


# -- /alertes/ -------------------------------------------------------------------------------------------


def alerts_top(site, s: Sample, vol: Volume) -> str:
    p = site.sales.products
    y, q = p["veille-ao-12m"], p["veille-ao-3m"]
    regions_opts = "".join(f'<option value="{slugify(r)}">{e(r)}</option>' for r in REGIONS)
    lo = min(REGIONS, key=vol.for_region)
    hi = max(REGIONS, key=vol.for_region)
    table = "".join(f"<li><span>{e(r)}</span><strong>{vol.for_region(r)}</strong></li>"
                    for r in sorted(REGIONS, key=vol.for_region, reverse=True))
    return f"""
<nav class="crumbs" aria-label="Fil d'Ariane"><a href="{site.link()}">Accueil</a> › Alertes e-mail</nav>
<section class="hero hero-split">
<div class="hero-copy">
<p class="eyebrow">Alertes e-mail · BOAMP + TED</p>
<h1>Les appels d'offres formation de votre région, chaque matin dans votre boîte</h1>
<p class="lead">{e(per_month(y, 12))} par mois, payé une fois, sans renouvellement automatique.</p>
<div class="cta-block">
<a class="btn btn-lg" href="#commander-h">Recevoir mes alertes – {e(price(y))} pour 12 mois</a>
<p class="price-note">ou {e(price(q))} pour 3 mois · réservé aux professionnels · paiement par carte, facture PDF</p>
</div>
<p class="hero-links"><a href="{site.link()}">Ou consulter gratuitement la liste des annonces →</a></p>
</div>
<div class="hero-visual">{mail_mock(s, site.now, compact=True)}<p class="visual-cap">Un exemple avec de vrais avis récents</p></div>
</section>

<section class="section" aria-labelledby="diff-h">
<h2 id="diff-h">La liste gratuite ou les alertes : quelle différence ?</h2>
<div class="compare">
<div class="compare-col">
<p class="compare-tag">Liste gratuite</p>
<p class="compare-title">Vous venez vérifier</p>
<ul>
<li>Vous pensez à revenir sur le site, puis vous refaites vos filtres.</li>
<li>Toutes les régions et tous les thèmes sont mélangés : à vous de trier.</li>
<li>Un avis manqué une semaine, c'est une semaine de moins pour répondre.</li>
</ul>
<a href="{site.link()}">Voir la liste gratuite</a>
</div>
<div class="compare-col pro">
<p class="compare-tag">Alertes e-mail</p>
<p class="compare-title">Ça vient à vous, filtré par vos mots-clés</p>
<ul>
<li>Un e-mail vers 7&nbsp;h avec seulement les <strong>nouveaux</strong> avis de vos régions et de vos mots-clés.</li>
<li>Dès l'inscription, tous les avis encore ouverts qui correspondent.</li>
<li>Pas de compte, pas d'application : un lien « arrêter les alertes » dans chaque e-mail, aucun pixel de suivi.</li>
</ul>
<a href="#commander-h">Choisir mes critères</a>
</div>
</div>
</section>

<section class="section" aria-labelledby="recu-h">
<h2 id="recu-h">Ce que vous recevez</h2>
<div class="get-grid">
<div>{mail_mock(s, site.now, compact=False)}
<p class="caption">{e(sample_caption(s, site.now))} <a href="{site.link('alertes/exemple-email.html')}" data-mesure="free_download">Version texte brut de l'exemple</a>.</p></div>
<ul class="checks">
<li><strong>Les deux sources officielles</strong> : le BOAMP et TED, le journal européen des marchés publics, vérifiés deux fois par jour.</li>
<li><strong>Déjà triés « formation »</strong> : codes de formation, descripteur « Formation » du BOAMP ; travaux et fournitures écartés.</li>
<li><strong>Un seul exemplaire</strong> quand un marché paraît au BOAMP et dans TED, ou est rectifié.</li>
<li><strong>L'essentiel par avis</strong> : acheteur, lieu, date limite, montant estimé quand il est publié, lien vers l'avis officiel.</li>
<li><strong>Vos mots-clés</strong> cherchés dans le titre, la description, l'acheteur et les lots (accents ignorés).</li>
</ul>
</div>
</section>

<section class="section" aria-labelledby="pour-h">
<h2 id="pour-h">Pour qui ?</h2>
<div class="who">
<div class="who-card"><p class="who-title">Organismes de formation</p><p>Repérer les marchés de votre territoire sans y passer vos matinées.</p></div>
<div class="who-card"><p class="who-title">Formateurs et consultants indépendants</p><p>Ne suivre que vos spécialités : « anglais », « management », « CACES »…</p></div>
<div class="who-card"><p class="who-title">Cabinets RH et conseil</p><p>Suivre plusieurs régions et thèmes dans un seul e-mail.</p></div>
</div>
<p class="small muted">Moins utile si vous visez surtout de petits marchés locaux publiés seulement sur les plateformes des acheteurs : voir « Et les MAPA ? » plus bas.</p>
</section>

<section class="section offer" aria-labelledby="offre-h">
<h2 id="offre-h">Une seule offre, deux durées</h2>
<div class="plans">
<div class="plan-card best"><p class="plan-tag">Pour une année complète de veille</p><p class="plan-name">12 mois</p><p class="plan-price">{e(price(y))}</p><p class="plan-sub">soit {e(per_month(y, 12))} par mois, payé une fois</p><a class="btn" href="#commander-h">Choisir 12 mois</a></div>
<div class="plan-card"><p class="plan-tag">Pour essayer sur un trimestre</p><p class="plan-name">3 mois</p><p class="plan-price">{e(price(q))}</p><p class="plan-sub">soit {e(per_month(q, 3))} par mois, payé une fois</p><a class="btn btn-ghost" href="#commander-h">Choisir 3 mois</a></div>
</div>
<ul class="checks inline">
<li>Toutes les régions, mots-clés et thèmes que vous voulez</li>
<li>Pas de renouvellement ni de prélèvement automatique : rappel 7 jours avant la fin</li>
<li>Facture PDF au nom de votre entreprise</li>
<li>Envois interrompus plus de 7 jours de notre fait : période prolongée ou remboursée (<a href="{site.link('cgv-pro/')}">CGV</a>, art. 9)</li>
</ul>
<p class="small muted">Prix nets, {VAT_LINE}. Paiement unique par carte bancaire.</p>

<div class="estimator" id="volume" data-volume="{e(vol.json())}">
<h3>Combien d'avis pour vous ?</h3>
<p class="small muted">Avis de formation publiés ces {VOLUME_DAYS} derniers jours (depuis le {fmt_date(vol.since)}), avis nationaux compris, avant filtrage par mots-clés.</p>
<label class="est-field" for="vol-region">Votre région</label>
<select id="vol-region"><option value="">Choisir une région</option>{regions_opts}</select>
<p id="vol-out" class="est-out" aria-live="polite">Toute la France : <strong>{vol.total}</strong> avis en {VOLUME_DAYS} jours, de {vol.for_region(lo)} ({e(lo)}) à {vol.for_region(hi)} ({e(hi)}) par région.</p>
<p id="vol-form" class="est-out small" aria-live="polite" hidden></p>
<details><summary>Toutes les régions</summary><ul class="est-table">{table}</ul></details>
</div>
</section>
"""


def faq(site, vol: Volume) -> str:
    lo = min(REGIONS, key=vol.for_region)
    hi = max(REGIONS, key=vol.for_region)
    qa = [
        ("Pourquoi payer alors que le BOAMP propose des alertes gratuites ?",
         f"""<p>Les alertes du BOAMP sont gratuites et utiles : un compte sur boamp.fr, jusqu'à 10 alertes sur les avis qu'il diffuse (<a href="https://www.boamp.fr/telechargements/cms/fichiers/CGU_site_boamp.fr_et_service_d_alerte_BOAMP_2024-10-21.pdf" rel="noopener">conditions d'utilisation de la DILA</a>). Bien réglées, elles couvrent une partie du besoin. Ce que nous ajoutons :</p>
<ul>
<li>le tri « formation » est déjà fait : codes de formation, descripteur « Formation », mots-clés quand l'avis n'a pas de code ; travaux et fournitures écartés ;</li>
<li>les avis publiés dans TED par des acheteurs français, dans le même e-mail ({vol.ted_only} des {vol.total} avis de ces {VOLUME_DAYS} derniers jours ne venaient que de TED) ;</li>
<li>un même marché publié au BOAMP et dans TED, ou rectifié, n'arrive qu'une fois ;</li>
<li>un seul e-mail par matin, avec vos régions (lieu d'exécution) et vos mots-clés cherchés jusque dans les lots.</li>
</ul>"""),
        ("Combien d'avis vais-je recevoir ?",
         f"""<p>Cela dépend surtout de vos régions. Ces {VOLUME_DAYS} derniers jours, {vol.total} avis de formation ont été publiés en France (doublons fusionnés), de {vol.for_region(lo)} en {e(lo)} à {vol.for_region(hi)} en {e(hi)}, avis nationaux compris : voir <a href="#volume">l'estimation par région</a>. Vos mots-clés réduisent encore ce nombre. Vous recevez un e-mail les matins où il y a du nouveau ; après 7 jours sans nouvel avis, un court e-mail « rien de nouveau ».</p>"""),
        ("Puis-je changer mes critères ?",
         """<p>Oui. Répondez à n'importe quel e-mail d'alerte avec vos nouvelles régions ou vos nouveaux mots-clés : la modification est faite sous 2 jours ouvrés. Le lien « arrêter les alertes » de chaque e-mail permet aussi de faire une pause, puis de reprendre jusqu'à la fin de la période.</p>"""),
        ("Et les MAPA ?",
         f"""<p>Les marchés à procédure adaptée publiés au BOAMP sont inclus ({vol.mapa} des {vol.total} avis de ces {VOLUME_DAYS} derniers jours étaient des marchés de moins de 90&nbsp;000&nbsp;€). Mais beaucoup de petits marchés ne paraissent que sur la plateforme de l'acheteur (profil d'acheteur) ou dans un journal d'annonces légales : <strong>ceux-là ne sont pas dans nos alertes</strong>. Si votre cible principale est le petit marché local, gardez aussi un œil sur les plateformes de vos acheteurs habituels.</p>"""),
        ("Facture et TVA ?",
         """<p>La facture PDF arrive avec l'e-mail de bienvenue, au nom de l'entreprise et du SIREN indiqués à la commande, avec les dates de la période. READ FRANCE SAS relève de la franchise en base de TVA : aucune TVA n'est facturée, le prix affiché est le montant total payé.</p>"""),
        ("Que se passe-t-il à la fin de la période ?",
         """<p>Rien n'est prélevé. Vous recevez un rappel environ 7 jours avant la fin, puis les alertes s'arrêtent. Pour continuer, vous commandez une nouvelle période ; commandée avant la fin, elle démarre le lendemain de la période en cours.</p>"""),
    ]
    items = "\n".join(f"<details class=\"faq-item\"><summary>{q}</summary>{a}</details>" for q, a in qa)
    return f"""<section class="section faq" aria-labelledby="faq-h">
<h2 id="faq-h">Questions fréquentes</h2>
{items}
<p class="small muted">Les règles complètes sont dans les <a href="{site.link('cgv-pro/')}">conditions générales de vente</a>. Une autre question : <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
</section>"""


def alerts_bottom(site, vol: Volume) -> str:
    return f"""
{faq(site, vol)}
{about(site)}
<section class="section legal-note" aria-labelledby="limites-h">
<h2 id="limites-h">Sources et limites</h2>
<p>Sélection automatique d'avis publiés au BOAMP (DILA) et dans TED (Office des publications de l'Union européenne), sans intelligence artificielle. Elle n'est pas exhaustive et repose sur les codes choisis par les acheteurs : vérifiez toujours l'avis officiel et le dossier de consultation. <a href="{site.link('methode/')}">Méthode et sources</a>.</p>
<p class="small muted">Produit indépendant de READ FRANCE SAS, ni affilié ni approuvé par la DILA ou l'Office des publications de l'UE.</p>
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
    p = site.sales.products
    return (f'<div class="sticky-cta" id="sticky-cta"><span>Alertes e-mail <strong>dès {e(price(p["veille-ao-3m"]))}</strong></span>'
            f'<a class="btn" href="{href}">Commander →</a></div>')
