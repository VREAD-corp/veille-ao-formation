"""cro.js (Octave aggregate behaviour counters + A/B framework): vendored copy, its portable node check, and the
tag on every generated page. No A/B experiment runs on this site (no OCTAVE_XP)."""
import html as html_mod
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from veille import boamp, sales, store
from veille.site import CRO_API, CRO_SITE, Site

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
# Jalonel copies of the tracking scripts (no reading-site host or prefix).
GAS_WEB = ROOT.parent / "octave-checkout" / "gas" / "web" / "jalonel"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
SITE = "https://ao-formation.jalonel.com/"
TAG = re.compile(r'<script src="/cro\.js" data-site="([^"]+)" data-api="([^"]+)" '
                 r'data-hosts="ao-formation\.jalonel\.com" data-cta="\[data-cta\]" defer></script>')


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_cro_js_behaviour():
    r = subprocess.run(["node", str(Path(__file__).with_name("cro.check.mjs")), str(ROOT / "veille" / "cro.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr + r.stdout


@pytest.mark.skipif(not GAS_WEB.is_dir(), reason="octave-checkout checkout not next to this repo")
def test_cro_files_match_octave_checkout():
    assert (ROOT / "veille" / "cro.js").read_bytes() == (GAS_WEB / "cro.js").read_bytes()
    assert (ROOT / "veille" / "mesure.js").read_bytes() == (GAS_WEB / "mesure.js").read_bytes()
    # tests/cro.check.mjs: gas/web/cro.check.mjs adapted to the Jalonel cro.js (no default host).


def test_cro_backend_is_the_mesure_backend():
    assert CRO_API in (ROOT / "veille" / "mesure.js").read_text(encoding="utf-8")
    assert CRO_SITE == "veille-ao-formation"


@pytest.mark.parametrize("open_", [True, False])
def test_every_page_loads_cro_js(tmp_path, monkeypatch, open_):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    c = json.loads((ROOT / "sales.config.json").read_text())
    c["SALES_OPEN"] = open_
    f = tmp_path / "sales.json"
    f.write_text(json.dumps(c))
    db = {}
    store.merge(db, boamp.normalise_all(json.loads((FIX / "boamp_records.json").read_text())["results"], today="2026-10-11"))
    out = tmp_path / "site"
    Site(out, SITE, NOW, sales=sales.load(f)).build(store.tenders(db), {"updated_at": "2026-10-11T04:20:00+00:00"})
    assert (out / "cro.js").read_bytes() == (ROOT / "veille" / "cro.js").read_bytes()
    assert (out / "static" / "cro-goals.js").is_file()
    pages = list(out.rglob("*.html"))
    assert len(pages) > 10 and (out / "404.html") in pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        m = TAG.search(html)
        assert m and m.groups() == (CRO_SITE, CRO_API), page
        assert html.count('src="/cro.js"') == 1, page
        assert html.index("/cro.js") < html.index("/static/cro-goals.js"), page
        assert "OCTAVE_XP" not in html, page  # no A/B experiment on this site
        assert 'type="module"' not in TAG.search(html).group(0)
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="hero"' in home and "data-cta" in home
    if open_:
        alerts = (out / "alertes" / "index.html").read_text(encoding="utf-8")
        for sid in ("hero", "prix", "commander", "faq"):
            assert f'<section class="' in alerts and f' id="{sid}"' in alerts, sid
        assert '<form id="octave-pay"' in alerts and 'id="pay-button" class="pay-button" data-cta' in alerts
        assert len(re.findall(r'<details class="faq-item" id="faq-[a-z-]+">', alerts)) == 6
        # the CSP of the checkout pages allows cro.js (self) and its POST to script.google.com
        for p in ("alertes/index.html", "alertes/commande.html"):
            csp = html_mod.unescape(re.search(r'Content-Security-Policy" content="([^"]+)"', (out / p).read_text()).group(1))
            assert "script-src 'self'" in csp and "connect-src 'self' https://script.google.com" in csp, p
    privacy = (out / "confidentialite" / "index.html").read_text(encoding="utf-8")
    for w in ("13 octobre 2026", "sessionStorage", "Global Privacy Control", "nom</em> du dernier champ",
              "recommandation de la CNIL du 17 septembre 2020", "article 82"):
        assert w in privacy, w
