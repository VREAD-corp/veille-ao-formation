"""Sales gate of the paid alerts (sales.config.json SALES_OPEN), the subscription page and the A/B price test."""

import html as htmlmod
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lotrepere import boamp, sales, store
from lotrepere.geo import REGION_SLUGS
from lotrepere.model import NICHE_SLUGS
from lotrepere.site import Site

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
SITE = "https://example.org/lot-repere/"
CRO_TAG = re.compile(r'<script src="[^"]*/cro\.js" [^>]*></script>')
BUY_MARKERS = ["js.stripe.com", "checkout-config", "octave-pay", "pk_live_", "pk_test_", "commande.html"]
TEST_OPEN = dict(SALES_OPEN=True, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")
# Canonical scripts: local octave-checkout checkout (absent in CI; OCTAVE_CHECKOUT overrides the location).
GAS_WEB = Path(os.environ.get("OCTAVE_CHECKOUT") or ROOT.parent / "octave-checkout") / "gas" / "web"


def cfg(tmp_path, **over):
    c = json.loads((ROOT / "sales.config.json").read_text())
    c.update(over)
    f = tmp_path / f"sales-{len(list(tmp_path.iterdir()))}.json"
    f.write_text(json.dumps(c))
    return f


def build(tmp_path, s):
    db = {}
    store.merge(db, boamp.normalise_all(json.loads((FIX / "boamp_records.json").read_text())["results"], today="2026-10-13"))
    out = tmp_path / f"site-{s.open}-{len(list(tmp_path.iterdir()))}"
    Site(out, SITE, NOW, sales=s).build(store.tenders(db), {"updated_at": "2026-10-13T04:20:00+00:00"})
    return out


def opened(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    return build(tmp_path, sales.load(cfg(tmp_path, **{**TEST_OPEN, **over})))


def jsonld(html):
    out = []
    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        d = json.loads(b)
        out.extend(d if isinstance(d, list) else [d])
    return out


def test_committed_config_is_coherent():
    s = sales.load(ROOT / "sales.config.json")
    assert s.products == {"lot-repere-3m": 4900, "lot-repere-12m": 14900}
    assert s.vat == "franchise293B" and sales.TERMS_VERSION == "cgv-pro-veille-ao-secteurs-2026-10-24"
    # price test closed on 2026-10-24 (merge into ao-formation): both arms name the 149 € product
    assert sales.PLAN_ARMS == {"a": "lot-repere-12m", "b": "lot-repere-12m"}
    # the paid alert stays the cheapest paid offer of the analysis (V4-30 §3: marchesonline from 25 €/month)
    assert max(s.products.values()) < 300_00
    assert s.endpoint.startswith("https://script.google.com/macros/s/") and s.publishable_key.startswith("pk_live_")


def test_open_needs_apps_script_endpoint_and_live_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SALES_ALLOW_TEST", raising=False)
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="", publishableKey=""))
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://evil.example/exec", publishableKey="pk_live_x"))
    with pytest.raises(ValueError):
        sales.load(cfg(tmp_path, products={"lot-repere-3m": 4900}))
    assert sales.load(cfg(tmp_path, SALES_OPEN=True, endpoint="https://script.google.com/macros/s/AKfycbTESTTESTTESTTESTTEST/exec", publishableKey="pk_live_x")).open


def test_closed_build_has_no_buy_ui(tmp_path):
    out = build(tmp_path, sales.load(cfg(tmp_path, SALES_OPEN=False)))
    assert not (out / "checkout").exists() and not (out / "cgv-pro").exists() and not (out / "alertes" / "commande.html").exists()
    for f in out.rglob("*"):
        if f.suffix in (".html", ".js", ".xml", ".txt", ".json") and f.is_file() and f.name not in ("mesure.js", "cro.js", "cro-goals.js"):
            body = CRO_TAG.sub("", f.read_text(encoding="utf-8"))
            for m in BUY_MARKERS:
                assert m not in body, f"{f}: {m}"
    page = (out / "alertes" / "index.html").read_text()
    assert "aucun paiement n'est possible" in page
    assert not any(i.get("@type") == "Product" for i in jsonld(page))


def test_open_build_order_form(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch)
    page = (out / "alertes" / "index.html").read_text()
    assert "Content-Security-Policy" in page and "https://octave-test.invalid" in page
    form = page[page.index('<form id="octave-pay"'):page.index("</form>", page.index('<form id="octave-pay"'))]
    # the extra fields read by octave-checkout (Alerts.js): niches, regions, keywords; no CPV family any more
    for slug in NICHE_SLUGS:
        assert f'name="niches" value="{slug}" data-octave-extra' in form
    assert 'name="niches" value="nettoyage" data-octave-extra checked' in form
    for slug in REGION_SLUGS:
        assert f'name="regions" value="{slug}" data-octave-extra>' in form
    assert form.count('name="regions"') == 14 and 'name="keywords" data-octave-extra' in form and 'name="cpv"' not in form
    # plans: 12 months (one price since 2026-10-24) and 3 months
    assert 'name="product" value="lot-repere-12m" data-arm-a="lot-repere-12m" data-arm-b="lot-repere-12m" checked' in form
    assert 'name="product" value="lot-repere-3m"' in form
    assert "149,00 € net (TVA non applicable, art. 293 B du CGI)" in form and "249" not in form
    assert "49,00 € net" in form and "sans renouvellement automatique" in form
    for n in ("email", "company", "siren", "addressLine1", "postalCode", "city", "terms"):
        assert f'name="{n}"' in form, n
    assert 'id="pay-button" class="pay-button" data-cta data-label-b="Payer 149 € et activer mes alertes" disabled>Payer 149 € et activer mes alertes</button>' in form
    # scripts: config, lazy Stripe loader, criteria checks; no A/B price switch any more
    assert '<script src="https://js.stripe.com/v3/">' not in page
    assert '<script src="/lot-repere/checkout/stripe-lazy.js" data-pay="/lot-repere/checkout/pay.js"></script>' in page
    assert "xp-price.js" not in page and "xp-config.js" not in page
    conf = (out / "checkout" / "checkout-config.js").read_text()
    assert '"offers": {"lot-repere-3m": {"amount": 4900}, "lot-repere-12m": {"amount": 14900}}' in conf
    assert '"termsVersion": "cgv-pro-veille-ao-secteurs-2026-10-24"' in conf and '"product": "lot-repere-12m"' in conf
    assert '"termsUrl": "/lot-repere/cgv-pro/"' in conf and '"orderPage": "commande.html"' in conf
    ids = Counter(re.findall(r'\sid="([^"]+)"', page))
    assert [i for i, n in ids.items() if n > 1] == []
    ret = (out / "alertes" / "commande.html").read_text()
    assert 'id="order-status"' in ret and '<meta name="robots" content="noindex">' in ret
    sitemap = (out / "sitemap.xml").read_text()
    assert SITE + "alertes/" in sitemap and "cgv-pro" not in sitemap and "commande" not in sitemap


def test_price_outside_alerts_never_shows_the_tested_12_month_price(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch)
    for f in out.rglob("*.html"):
        rel = f.relative_to(out).as_posix()
        if rel.startswith(("alertes/", "cgv-pro/", "confidentialite/")):
            continue
        body = f.read_text()
        assert "149" not in body.replace("1490", "") or "149 €" not in body, rel
        assert "149 €" not in body and "249 €" not in body, rel
    index = (out / "index.html").read_text()
    assert "dès 49 € pour 3 mois" in index


def test_single_12_month_price_after_the_test(tmp_path, monkeypatch):
    """lot-repere-prix12 closed on 2026-10-24: one 12-month price (149 €) on the order page, in the terms and nowhere 249 €."""
    out = opened(tmp_path, monkeypatch)
    page = (out / "alertes" / "index.html").read_text()
    assert not (out / "static" / "xp-config.js").exists() and not (out / "checkout" / "xp-price.js").exists()
    spans = re.findall(r'class="xp-price" data-a="([^"]+)" data-b="([^"]+)">', page)
    assert spans and all(a == b for a, b in spans) and "249" not in page
    cgv = (out / "cgv-pro" / "index.html").read_text()
    assert "149,00 € net" in cgv and "249,00 € net" not in cgv and "49,00 € net" in cgv and "fait l'objet d'un test" not in cgv
    privacy = (out / "confidentialite" / "index.html").read_text()
    assert "Test A/B" not in privacy and "249 €" not in privacy


def test_open_build_terms_and_privacy(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch)
    cgv = (out / "cgv-pro" / "index.html").read_text()
    assert "PAS UN CONSEIL JURIDIQUE" in cgv and '<meta name="robots" content="noindex">' in cgv
    for needle in ["version du 24 octobre 2026", "cgv-pro-veille-ao-secteurs-2026-10-24", "au capital de 1 000 €", "SIREN 988 741 773",
                   "TVA non applicable, art. 293 B du CGI", "Veille AO", "anciennement «&nbsp;Lot Repéré&nbsp;»", "nettoyage de locaux", "sécurité privée",
                   "période ferme et prépayée de 3 mois ou de 12 mois", "ni renouvelé ni prélevé automatiquement",
                   "arrêter les alertes à tout moment", "n'est pas exhaustive", "ne s'applique pas", "limitée au prix payé",
                   "40 €", "CNIL", "tribunaux de Paris"]:
        assert needle in cgv, needle
    privacy = (out / "confidentialite" / "index.html").read_text()
    assert "Abonnement aux alertes e-mail" in privacy and "métiers, régions, mots-clés" in privacy
    assert 'href="/lot-repere/cgv-pro/">CGV</a>' in (out / "index.html").read_text()


def test_open_build_jsonld(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch)
    page = (out / "alertes" / "index.html").read_text()
    items = jsonld(page)
    product = next(i for i in items if i.get("@type") == "Product")
    assert product["brand"] == {"@type": "Brand", "name": "Jalonel"} and product["sku"] == "lot-repere-alertes"
    offers = {o["name"]: o for o in product["offers"]}
    assert offers["3 mois"]["@type"] == "Offer" and offers["3 mois"]["price"] == "49.00"
    assert offers["12 mois"]["@type"] == "Offer" and offers["12 mois"]["price"] == "149.00"
    faq = next(i for i in items if i.get("@type") == "FAQPage")
    assert len(faq["mainEntity"]) == page.count('class="faq-item"') == 6
    assert all(q["acceptedAnswer"]["text"] and "<" not in q["acceptedAnswer"]["text"] for q in faq["mainEntity"])
    assert "aggregateRating" not in page and '"review' not in page
    title = htmlmod.unescape(re.search(r"<title>(.*?)</title>", page).group(1))
    desc = htmlmod.unescape(re.search(r'<meta name="description" content="([^"]*)">', page).group(1))
    assert len(desc) <= 170 and "49 € net" in desc and "149" not in desc and "Veille AO" in title


def test_no_ht_ttc_on_our_prices(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch)
    for f in ("alertes/index.html", "cgv-pro/index.html", "index.html"):
        body = (out / f).read_text().replace("€ HT (estimation)", "")
        assert "TTC" not in body and "€ HT" not in body, f


def test_quotes_flag_off_when_committed(tmp_path, monkeypatch):
    assert json.loads((ROOT / "sales.config.json").read_text())["quotesEnabled"] is False
    out = opened(tmp_path, monkeypatch, quotesEnabled=False)
    page = (out / "alertes" / "index.html").read_text()
    assert "octave-devis" not in page and "devis.js" not in page


def test_quotes_on_build_has_the_same_extra_fields(tmp_path, monkeypatch):
    out = opened(tmp_path, monkeypatch, quotesEnabled=True)
    page = (out / "alertes" / "index.html").read_text()
    card = page[page.index('<form id="octave-pay"'):page.index("</form>", page.index('<form id="octave-pay"'))]
    quote = page[page.index('<form id="octave-devis"'):page.index("</form>", page.index('<form id="octave-devis"'))]
    extra = r'<input [^>]*data-octave-extra[^>]*>'
    strip = lambda tags: [re.sub(r'\s(?:id|aria-describedby)="[^"]*"', "", t) for t in tags]
    assert strip(re.findall(extra, quote)) == strip(re.findall(extra, card))
    ids = Counter(re.findall(r'\sid="([^"]+)"', page))
    assert [i for i, n in ids.items() if n > 1] == []


@pytest.mark.skipif(not GAS_WEB.is_dir(), reason="octave-checkout checkout not next to this repo")
def test_vendored_checkout_scripts_match_octave_checkout():
    for f in ("pay.js", "devis.js", "order.js"):
        assert (ROOT / "lotrepere" / "checkout" / f).read_bytes() == (GAS_WEB / f).read_bytes(), f


def test_home_first_screen_is_the_tool(tmp_path, monkeypatch):
    # JALONEL-HUB-02: H1, one sentence and the alerts link with its starting price, then the filters and the list;
    # counts per trade, the full price note and the e-mail preview come after the list.
    index = (opened(tmp_path, monkeypatch) / "index.html").read_text()
    hero, rest = index.split('<section aria-labelledby="filtres-titre"', 1)
    words = lambda h: [w for w in re.sub(r"<[^>]+>", " ", h).split() if re.search(r"\w", w)]
    top = hero.split("<h1>", 1)[1]
    assert len(words(top.split('<p class="hero-alert"')[0])) <= 30 and len(words(top)) <= 42
    assert 'alertes/" data-cta>' in top and "pour 3 mois" in top
    after = rest.split('id="liste-titre"', 1)[1]
    assert 'id="apercu-alertes"' in after and "niche-tiles" in after and "CGV, art. 9" in after
