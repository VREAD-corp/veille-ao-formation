"""Sales gate of the paid alerts (sales.config.json SALES_OPEN) and the subscription page."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from veille import sales, shop, store, boamp
from veille.geo import REGION_SLUGS
from veille.site import Site

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
SITE = "https://example.org/"
CRO_TAG = re.compile(r'<script src="[^"]*/cro\.js" [^>]*></script>')
BUY_MARKERS = ["js.stripe.com", "checkout-config", "octave-pay", "pk_live_", "pk_test_", "script.google.com", "commande.html"]


def cfg(tmp_path, **over):
    c = json.loads((ROOT / "sales.config.json").read_text())
    c.update(over)
    f = tmp_path / f"sales-{len(list(tmp_path.iterdir()))}.json"
    f.write_text(json.dumps(c))
    return f


def build(tmp_path, s):
    db = {}
    store.merge(db, boamp.normalise_all(json.loads((FIX / "boamp_records.json").read_text())["results"], today="2026-10-11"))
    out = tmp_path / f"site-{s.open}"
    Site(out, SITE, NOW, sales=s).build(store.tenders(db), {"updated_at": "2026-10-11T04:20:00+00:00"})
    return out


def test_committed_config_is_coherent():
    s = sales.load(ROOT / "sales.config.json")
    assert s.products == {"veille-ao-3m": 4900, "veille-ao-12m": 14900}
    assert s.vat == "franchise293B"
    if s.open:
        assert s.endpoint.startswith("https://script.google.com/macros/s/") and s.publishable_key.startswith("pk_live_")


def test_open_needs_apps_script_endpoint_and_live_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SALES_ALLOW_TEST", raising=False)
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="", publishableKey=""))
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://evil.example/exec", publishableKey="pk_live_x"))
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://script.google.com/macros/s/AKfycbTESTTESTTESTTESTTEST/exec", publishableKey="pk_test_x"))
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, products={"veille-ao-3m": 4900}))
    assert sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://script.google.com/macros/s/AKfycbTESTTESTTESTTESTTEST/exec", publishableKey="pk_live_x")).open


def test_closed_build_has_no_buy_ui(tmp_path):
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=False)))
    assert not (out / "checkout").exists() and not (out / "cgv-pro").exists() and not (out / "alertes" / "commande.html").exists()
    for f in out.rglob("*"):
        # audience / behaviour counters (mesure.js, cro.js and its goals script), not buy UI
        if f.suffix in (".html", ".js", ".xml", ".txt", ".json") and f.is_file() and f.name not in ("mesure.js", "cro.js", "cro-goals.js"):
            body = CRO_TAG.sub("", f.read_text(encoding="utf-8"))
            for m in BUY_MARKERS:
                assert m not in body, f"{f}: {m}"
    assert "pas encore disponible" in (out / "alertes" / "index.html").read_text().lower()


def test_open_build_subscription_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    # Card form only (the quote form repeats the order fields; see test_quotes_on_build_adds_quote_form).
    s = sales.load(cfg(tmp_path, SALES_OPEN=True, quotesEnabled=False, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123"))
    out = build(tmp_path, s)
    page = (out / "alertes" / "index.html").read_text()
    assert '<meta http-equiv="Content-Security-Policy" content="default-src &#x27;self&#x27;; script-src &#x27;self&#x27; https://js.stripe.com' in page
    assert "https://octave-test.invalid" in page
    for slug in REGION_SLUGS:
        assert f'name="regions" value="{slug}" data-octave-extra' in page
    assert page.count('name="regions"') == 14
    for code, _ in shop.CPV_FAMILIES:
        assert f'name="cpv" value="{code}" data-octave-extra' in page
    assert 'name="product" value="veille-ao-12m" checked' in page and 'name="product" value="veille-ao-3m"' in page
    assert "149,00 € net (TVA non applicable, art. 293 B du CGI)" in page and "49,00 € net" in page
    assert "sans renouvellement automatique" in page
    for n in ("email", "company", "siren", "addressLine1", "postalCode", "city", "terms"):
        assert f'name="{n}"' in page
    assert 'name="keywords" data-octave-extra' in page
    assert "pas encore disponible" not in page.lower() and "19&nbsp;€" not in page
    # Stripe.js and pay.js wait for the first interaction with the order form (stripe-lazy.js).
    assert '<script src="https://js.stripe.com/v3/">' not in page and 'src="/checkout/pay.js"' not in page
    assert '<script src="/checkout/stripe-lazy.js" data-pay="/checkout/pay.js"></script>' in page
    assert (out / "checkout" / "stripe-lazy.js").exists()
    for f in ("pay.js", "order.js", "alerts-form.js"):
        assert (out / "checkout" / f).read_bytes() == (ROOT / "veille" / "checkout" / f).read_bytes()
    conf = (out / "checkout" / "checkout-config.js").read_text()
    assert '"orderPage": "commande.html"' in conf and '"termsUrl": "/cgv-pro/"' in conf
    # Static prices + terms version: pay.js mounts the Payment Element without waiting for the catalog.
    assert '"offers": {"veille-ao-3m": {"amount": 4900}, "veille-ao-12m": {"amount": 14900}}' in conf
    assert '"termsVersion": "cgv-pro-alertes-2026-10-13"' in conf and '"payLabel": "Payer {prix} et activer mes alertes"' in conf
    assert 'id="pay-button" class="pay-button" data-cta disabled>Payer 149\u00a0€ et activer mes alertes</button>' in page
    assert 'id="payment-loading"' in page and "Chargement du paiement sécurisé…" in page
    ret = (out / "alertes" / "commande.html").read_text()
    assert 'id="order-status"' in ret and "js.stripe.com" not in ret and '<meta name="robots" content="noindex">' in ret
    index = (out / "index.html").read_text()
    assert "Recevoir les AO formation de ma région chaque matin →" in index and "Bientôt" not in index
    assert 'id="cta-tpl"' in index and 'class="sticky-cta"' in index
    # landing content around the untouched order form
    assert "Les appels d'offres formation de votre région, chaque matin dans votre boîte" in page
    assert "149\u00a0€ pour 12 mois, soit 12,42\u00a0€ par mois, payé une fois, sans renouvellement automatique.</p>" in page
    assert page.count('class="faq-item"') == 6 and 'id="volume" data-volume=' in page
    assert page.index('id="volume"') < page.index('id="octave-pay"') < page.index('id="faq-h"')
    assert 'src="/static/landing.js"' in page and (out / "static" / "landing.js").exists()
    sitemap = (out / "sitemap.xml").read_text()
    assert SITE + "alertes/" in sitemap and "cgv-pro" not in sitemap and "commande" not in sitemap


def test_open_build_terms_and_privacy(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")))
    cgv = (out / "cgv-pro" / "index.html").read_text()
    assert "PAS UN CONSEIL JURIDIQUE" in cgv and '<meta name="robots" content="noindex">' in cgv
    for needle in ["version du 13 octobre 2026", "cgv-pro-alertes-2026-10-13", "au capital de 1 000 €", "SIREN 988 741 773",
                   "TVA non applicable, art. 293 B du CGI", "professionnel", "période ferme et prépayée de 3 mois ou de 12 mois",
                   "ni renouvelé ni prélevé automatiquement", "rappel environ 7 jours avant la fin", "49,00 € net pour 3 mois",
                   "149,00 € net pour 12 mois", "arrêter les alertes à tout moment", "plus de 7 jours consécutifs",
                   "n'est pas exhaustive", "ne s'applique pas", "limitée au prix payé", "trois fois le taux d'intérêt légal",
                   "40 €", "12 mois après sa fin", "10 ans", "CNIL", "tribunaux de Paris",
                   # bank transfer on quote (internal legal review REVUE-CGV-VIREMENT.md §3, Alertes numbering)
                   "Avant de payer par carte ou de demander un devis", "virement bancaire SEPA en euros",
                   "Stripe Payments Europe, Limited et Stripe Technology Europe, Limited", "numéro de compte bancaire virtuel",
                   "valable trente (30) jours", "Un paiement partiel ne forme pas le contrat", "Affectation des paiements",
                   "par le moyen de paiement utilisé", "la « confirmation du paiement » désigne",
                   "(article 6). La facture, établie à la confirmation du paiement, est acquittée",
                   "Escompte pour paiement anticipé : néant", "quatre derniers chiffres de l'IBAN",
                   "devis non suivis de commande, 1 an après leur expiration", "lors de sa commande ou de sa demande de devis"]:
        assert needle in cgv.replace("&nbsp;", " "), needle  # the site's typography adds &nbsp; inside « » and before :
    assert "Ltd." not in cgv and "à réception du devis" not in cgv and "compte de paiement" not in cgv
    for n, title in ((6, "Commande et paiement"), (13, "Facturation et pénalités de retard"), (14, "Données personnelles"), (16, "Dispositions diverses")):
        assert f"<h2>{n}. {title}</h2>" in cgv
    privacy = (out / "confidentialite" / "index.html").read_text()
    assert "Abonnement aux alertes e-mail" in privacy and "12 mois après sa fin" in privacy
    for needle in ("demandez un devis", "quatre derniers chiffres de l'IBAN", "mesures précontractuelles",
                   "devis non suivis de commande, 1 an après leur expiration", "lutte contre le blanchiment", "ni pixel"):
        assert needle in privacy.replace("&nbsp;", " "), needle
    assert "Ce site ne collecte aucune donnée personnelle" not in privacy
    assert 'href="/cgv-pro/">CGV</a>' in (out / "index.html").read_text()
    assert "remplacé par les" in (out / "cgv" / "index.html").read_text()
    assert "au capital de 1 000 €" in (out / "mentions-legales" / "index.html").read_text()


def jsonld_blocks(html):
    import re
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    out = []
    for b in blocks:
        data = json.loads(b)
        out.extend(data if isinstance(data, list) else [data])
    return out


def test_open_build_product_jsonld(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")))
    page = (out / "alertes" / "index.html").read_text()
    items = jsonld_blocks(page)
    # the full Organization lives on the hub only: referenced by @id, never repeated here
    org = {"@id": "https://jalonel.com/#org"}
    assert not any(i.get("@type") == "Organization" for i in items)
    assert "legalName" not in page and "PostalAddress" not in page
    product = next(i for i in items if i.get("@type") == "Product")
    assert product["@context"] == "https://schema.org" and product["sku"] == "veille-ao-alertes"
    assert product["url"] == SITE + "alertes/" and product["image"] == SITE + "static/og.png"
    assert product["brand"] == {"@type": "Brand", "name": "Veille AO Formation"}
    offers = product["offers"]
    assert isinstance(offers, list) and len(offers) == 2
    assert sorted((o["name"], o["price"]) for o in offers) == [("12 mois", "149.00"), ("3 mois", "49.00")]
    for o in offers:
        assert o["@type"] == "Offer" and o["priceCurrency"] == "EUR"
        assert o["availability"] == "https://schema.org/InStock" and o["url"] == SITE + "alertes/"
        assert o["seller"] == {"@id": org["@id"]} and "valueAddedTaxIncluded" not in o
        assert o["shippingDetails"]["shippingRate"]["value"] == "0"
        assert o["shippingDetails"]["shippingDestination"]["addressCountry"] == "FR"
        assert o["hasMerchantReturnPolicy"]["returnPolicyCategory"] == "https://schema.org/MerchantReturnNotPermitted"
        assert o["hasMerchantReturnPolicy"]["merchantReturnLink"] == SITE + "cgv-pro/"
    # SEO head: literal apostrophes, net prices with the VAT franchise note, sane lengths
    import html as htmlmod, re
    title = htmlmod.unescape(re.search(r"<title>(.*?)</title>", page).group(1))
    desc = htmlmod.unescape(re.search(r'<meta name="description" content="([^"]*)">', page).group(1))
    assert "<title>Alerte appels d'offres formation par e-mail" in page and len(title) <= 65
    assert "49 € net" in desc and "149 € net" in desc and "TVA non applicable, art. 293 B du CGI" in desc and len(desc) <= 158
    assert 'href="https://qualiopi.jalonel.com/">' in page  # contextual link to the Qualiopi kit
    # home page: WebSite and Dataset point at the hub Organization, visible priced link to /alertes/ near the top, no Product
    index = (out / "index.html").read_text()
    home = jsonld_blocks(index)
    assert {i["@type"] for i in home} == {"WebSite", "Dataset"}
    ref = {"@type": "Organization", "@id": "https://jalonel.com/#org", "name": "Jalonel", "url": "https://jalonel.com/",
           "parentOrganization": {"@type": "Organization", "name": "READ FRANCE SAS", "identifier": "SIREN 988741773"}}
    assert next(i for i in home if i["@type"] == "WebSite")["publisher"] == ref
    assert next(i for i in home if i["@type"] == "Dataset")["creator"] == ref
    assert "&nbsp;" not in json.dumps(home + items, ensure_ascii=False)  # typography fix stays out of JSON-LD
    # First screen (JALONEL-HUB-02): H1, one sentence and the alerts link with its starting price, then the filters
    # and the list; the full price note (both periods, VAT line) and the e-mail preview come after the list.
    hero, rest = index.split('<section aria-labelledby="filtres-titre"', 1)
    assert 'href="/alertes/" data-cta>Recevoir les AO formation de ma région chaque matin →</a>' in hero
    assert "49\u00a0€ pour 3 mois" in hero
    words = lambda h: [w for w in re.sub(r"<[^>]+>", " ", h).split() if re.search(r"\w", w)]
    assert len(words(hero.split("<h1>", 1)[1].split('class="hero-alert"')[0])) <= 30  # H1 + one sentence
    assert len(words(hero.split("<h1>", 1)[1])) <= 40  # with the alerts link
    after = rest.split('id="liste-titre"', 1)[1]
    assert "TVA non applicable, art. 293 B du CGI" in after and 'id="apercu-alertes"' in after


def test_closed_build_has_no_product_jsonld(tmp_path):
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=False)))
    assert not any(i.get("@type") == "Product" for i in jsonld_blocks((out / "alertes" / "index.html").read_text()))
    home = jsonld_blocks((out / "index.html").read_text())
    assert not any(i.get("@type") == "Organization" for i in home)
    assert next(i for i in home if i["@type"] == "WebSite")["publisher"]["@id"] == "https://jalonel.com/#org"


def test_no_ht_ttc_on_our_prices(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    for s in (sales.load(cfg(tmp_path, SALES_OPEN=False)),
              sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123"))):
        out = build(tmp_path, s)
        for f in ("alertes/index.html", "cgv/index.html", "cgv-pro/index.html", "index.html"):
            if (out / f).exists():
                body = (out / f).read_text().replace("€ HT (estimation)", "")  # buyers' estimated contract values
                assert "TTC" not in body and "€ HT" not in body and "hors tax" not in body.lower(), f


# --- Quote / bank-transfer form (sales.config.json quotesEnabled) ---------------------------------------------

import os  # noqa: E402
# Canonical scripts: local octave-checkout checkout (absent in CI; OCTAVE_CHECKOUT overrides the location).
GAS_WEB = Path(os.environ.get("OCTAVE_CHECKOUT") or ROOT.parent / "octave-checkout") / "gas" / "web"
TEST_OPEN = dict(SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")


def form_html(page, form_id):
    start = page.index(f'<form id="{form_id}"')
    return page[start:page.index("</form>", start)]


def test_quotes_flag_is_boolean_and_on_when_committed(tmp_path):
    # Quotes on since 2026-10-16 (backend QUOTES_ENABLED=true); an absent key still means off.
    assert json.loads((ROOT / "sales.config.json").read_text())["quotesEnabled"] is True
    assert sales.load(ROOT / "sales.config.json").quotes_enabled is True
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, quotesEnabled="true"))
    c = json.loads((ROOT / "sales.config.json").read_text())
    c.pop("quotesEnabled")
    (tmp_path / "legacy.json").write_text(json.dumps(c))
    assert sales.load(tmp_path / "legacy.json").quotes_enabled is False


def test_quotes_off_build_has_no_quote_form(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    out = build(tmp_path, sales.load(cfg(tmp_path, quotesEnabled=False, **TEST_OPEN)))
    page = (out / "alertes" / "index.html").read_text()
    assert "octave-devis" not in page and "devis.js" not in page and 'class="hp"' not in page
    assert '"quotesEnabled": false' in (out / "checkout" / "checkout-config.js").read_text()
    assert (out / "checkout" / "devis.js").read_bytes() == (ROOT / "veille" / "checkout" / "devis.js").read_bytes()


def test_quotes_on_build_adds_quote_form(tmp_path, monkeypatch):
    import re
    from collections import Counter
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    out = build(tmp_path, sales.load(cfg(tmp_path, quotesEnabled=True, **TEST_OPEN)))
    page = (out / "alertes" / "index.html").read_text()
    assert '"quotesEnabled": true' in (out / "checkout" / "checkout-config.js").read_text()
    # block once, right after the card form, inside the order section kept by landing.wrap_order
    assert page.count('id="octave-devis-box"') == 1 and page.count('<form id="octave-devis"') == 1
    pay_end = page.index("</form>", page.index('<form id="octave-pay"'))
    assert page[pay_end:].startswith('</form>\n<details id="octave-devis-box" class="card devis-box" hidden>')
    assert page.index('id="octave-devis-box"') < page.index("</section>", pay_end) < page.index('id="faq-h"')
    # script after the pay.js loader (and before alerts-form.js, which binds the duplicated region buttons);
    # devis.js needs no Stripe.js, so it is not deferred
    pay = page.index('<script src="/checkout/stripe-lazy.js" data-pay="/checkout/pay.js"></script>')
    devis = page.index('<script src="/checkout/devis.js"></script>')
    assert page.count("checkout/devis.js") == 1 and pay < devis < page.index("checkout/alerts-form.js")
    ids = Counter(re.findall(r'\sid="([^"]+)"', page))
    assert [i for i, n in ids.items() if n > 1] == []
    for attr in ("for", "aria-describedby"):
        for ref in re.findall(rf'\s{attr}="([^"]+)"', page):
            assert ids[ref] == 1, (attr, ref)
    card, quote = form_html(page, "octave-pay"), form_html(page, "octave-devis")
    # same offers and the same [data-octave-extra] fields (name + data-octave-extra) as the card form
    radios = r'<input type="radio" name="product" value="[^"]+"(?: checked)?>'
    assert re.findall(radios, quote) == re.findall(radios, card) and len(re.findall(radios, quote)) == 2
    extra = r'<input [^>]*data-octave-extra[^>]*>'
    strip_ids = lambda tags: [re.sub(r'\s(?:id|aria-describedby)="[^"]*"', "", t) for t in tags]
    assert strip_ids(re.findall(extra, quote)) == strip_ids(re.findall(extra, card))
    assert quote.count('name="regions"') == 14 and quote.count('name="cpv"') == len(shop.CPV_FAMILIES)
    assert 'aria-describedby="kw-help-devis"' in quote and 'id="regions-all-devis"' in quote
    for n in ("email", "company", "siren", "addressLine1", "addressLine2", "postalCode", "city", "buyerRef", "website", "terms"):
        assert f'name="{n}"' in quote, n
    # honeypot + terms: same CGV link and version date as the card checkbox
    assert '<div class="hp" aria-hidden="true">' in quote and ".hp{position:absolute;left:-9999px" in (out / "static" / "style.css").read_text()
    assert 'class="terms-link" href="/cgv-pro/"' in quote and 'id="terms-link" href="/cgv-pro/"' in card
    assert f"(version du {sales.TERMS_DATE})" in quote and f"(version du {sales.TERMS_DATE})" in card and "{{" not in page
    for f in ("pay.js", "devis.js", "alerts-form.js"):
        assert (out / "checkout" / f).read_bytes() == (ROOT / "veille" / "checkout" / f).read_bytes()


@pytest.mark.skipif(not GAS_WEB.is_dir(), reason="octave-checkout checkout not next to this repo")
def test_vendored_checkout_scripts_match_octave_checkout():
    for f in ("pay.js", "devis.js", "order.js"):
        assert (ROOT / "veille" / "checkout" / f).read_bytes() == (GAS_WEB / f).read_bytes(), f
