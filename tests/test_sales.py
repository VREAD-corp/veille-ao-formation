"""Sales gate of the paid alerts (sales.config.json SALES_OPEN) and the subscription page."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from veille import sales, shop, store, boamp
from veille.geo import REGION_SLUGS
from veille.site import Site

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
SITE = "https://example.org/veille-ao-formation/"
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
        if f.suffix in (".html", ".js", ".xml", ".txt", ".json") and f.is_file() and f.name != "mesure.js":  # audience, not buy UI
            body = f.read_text(encoding="utf-8")
            for m in BUY_MARKERS:
                assert m not in body, f"{f}: {m}"
    assert "pas encore disponible" in (out / "alertes" / "index.html").read_text().lower()


def test_open_build_subscription_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    s = sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123"))
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
    assert 'src="/veille-ao-formation/checkout/pay.js"' in page
    for f in ("pay.js", "order.js", "alerts-form.js"):
        assert (out / "checkout" / f).read_bytes() == (ROOT / "veille" / "checkout" / f).read_bytes()
    conf = (out / "checkout" / "checkout-config.js").read_text()
    assert '"orderPage": "commande.html"' in conf and '"termsUrl": "/veille-ao-formation/cgv-pro/"' in conf
    # Static prices + terms version: pay.js mounts the Payment Element without waiting for the catalog.
    assert '"offers": {"veille-ao-3m": {"amount": 4900}, "veille-ao-12m": {"amount": 14900}}' in conf
    assert '"termsVersion": "cgv-pro-alertes-2026-10-12"' in conf and '"payLabel": "Payer {prix} et activer mes alertes"' in conf
    assert 'id="pay-button" class="pay-button" disabled>Payer 149\u00a0€ et activer mes alertes</button>' in page
    assert 'id="payment-loading"' in page and "Chargement du paiement sécurisé…" in page
    ret = (out / "alertes" / "commande.html").read_text()
    assert 'id="order-status"' in ret and "js.stripe.com" not in ret and '<meta name="robots" content="noindex">' in ret
    index = (out / "index.html").read_text()
    assert "Recevoir les AO formation de ma région chaque matin →" in index and "Bientôt" not in index
    assert 'id="cta-tpl"' in index and 'class="sticky-cta"' in index
    # landing content around the untouched order form
    assert "Les appels d'offres formation de votre région, chaque matin dans votre boîte" in page
    assert '<p class="lead">12,42\u00a0€ par mois, payé une fois, sans renouvellement automatique.</p>' in page
    assert page.count('class="faq-item"') == 6 and 'id="volume" data-volume=' in page
    assert page.index('id="volume"') < page.index('id="octave-pay"') < page.index('id="faq-h"')
    assert 'src="/veille-ao-formation/static/landing.js"' in page and (out / "static" / "landing.js").exists()
    sitemap = (out / "sitemap.xml").read_text()
    assert SITE + "alertes/" in sitemap and "cgv-pro" not in sitemap and "commande" not in sitemap


def test_open_build_terms_and_privacy(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")))
    cgv = (out / "cgv-pro" / "index.html").read_text()
    assert "PAS UN CONSEIL JURIDIQUE" in cgv and '<meta name="robots" content="noindex">' in cgv
    for needle in ["version du 12 octobre 2026", "cgv-pro-alertes-2026-10-12", "au capital de 1 000 €", "SIREN 988 741 773",
                   "TVA non applicable, art. 293 B du CGI", "professionnel", "période ferme et prépayée de 3 mois ou de 12 mois",
                   "ni renouvelé ni prélevé automatiquement", "rappel environ 7 jours avant la fin", "49,00 € net pour 3 mois",
                   "149,00 € net pour 12 mois", "arrêter les alertes à tout moment", "plus de 7 jours consécutifs",
                   "n'est pas exhaustive", "ne s'applique pas", "limitée au prix payé", "trois fois le taux d'intérêt légal",
                   "40 €", "12 mois après sa fin", "10 ans", "CNIL", "tribunaux de Paris", "pixel"]:
        assert needle in cgv, needle
    privacy = (out / "confidentialite" / "index.html").read_text()
    assert "Abonnement aux alertes e-mail" in privacy and "12 mois après sa fin" in privacy
    assert "Ce site ne collecte aucune donnée personnelle" not in privacy
    assert 'href="/veille-ao-formation/cgv-pro/">CGV</a>' in (out / "index.html").read_text()
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
    org = next(i for i in items if i.get("@type") == "Organization")
    assert org["@id"] == "https://outils.vread.fr/#org" and org["legalName"] == "READ FRANCE"
    assert org["identifier"] == {"@type": "PropertyValue", "propertyID": "SIREN", "value": "988741773"}
    assert org["address"]["postalCode"] == "75019" and org["address"]["addressCountry"] == "FR"
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
    # SEO head: literal apostrophes, net prices with the VAT franchise note, sane lengths
    import html as htmlmod, re
    title = htmlmod.unescape(re.search(r"<title>(.*?)</title>", page).group(1))
    desc = htmlmod.unescape(re.search(r'<meta name="description" content="([^"]*)">', page).group(1))
    assert "<title>Alerte appels d'offres formation par e-mail" in page and len(title) <= 65
    assert "49 € net" in desc and "149 € net" in desc and "TVA non applicable, art. 293 B du CGI" in desc and len(desc) <= 158
    # home page: same Organization, visible priced link to /alertes/ near the top, no Product
    index = (out / "index.html").read_text()
    home = jsonld_blocks(index)
    assert {i["@type"] for i in home} == {"Organization", "WebSite", "Dataset"}
    assert next(i for i in home if i["@type"] == "Organization") == org
    assert "&nbsp;" not in json.dumps(home + items, ensure_ascii=False)  # typography fix stays out of JSON-LD
    hero = index.split('<section aria-labelledby="filtres-titre"')[0]
    assert 'href="/veille-ao-formation/alertes/">Recevoir les AO formation de ma région chaque matin →</a>' in hero
    assert "49\u00a0€ pour 3 mois" in hero
    assert "TVA non applicable, art. 293 B du CGI" in hero


def test_closed_build_has_no_product_jsonld(tmp_path):
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=False)))
    assert not any(i.get("@type") == "Product" for i in jsonld_blocks((out / "alertes" / "index.html").read_text()))
    assert any(i.get("@type") == "Organization" for i in jsonld_blocks((out / "index.html").read_text()))


def test_no_ht_ttc_on_our_prices(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    for s in (sales.load(cfg(tmp_path, SALES_OPEN=False)),
              sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123"))):
        out = build(tmp_path, s)
        for f in ("alertes/index.html", "cgv/index.html", "cgv-pro/index.html", "index.html"):
            if (out / f).exists():
                body = (out / f).read_text().replace("€ HT (estimation)", "")  # buyers' estimated contract values
                assert "TTC" not in body and "€ HT" not in body and "hors tax" not in body.lower(), f
