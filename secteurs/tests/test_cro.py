"""mesure.js + cro.js (Octave cookieless audience and behaviour counters, A/B framework): vendored copies, the
portable node check of cro.js, and the tags on every generated page. One A/B experiment: lot-repere-prix12."""
import html as html_mod
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lotrepere import boamp, sales, store
from lotrepere.site import CRO_API, CRO_SITE, Site

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
# Jalonel copies of the tracking scripts (no reading-site host or prefix).
GAS_WEB = ROOT.parent.parent / "octave-checkout" / "gas" / "web" / "jalonel"  # secteurs/ sits in veille-ao-formation
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
SITE = "https://ao-formation.jalonel.com/secteurs/"
TAG = re.compile(r'<script src="/secteurs/cro\.js" data-site="([^"]+)" data-api="([^"]+)" '
                 r'data-hosts="ao-formation\.jalonel\.com" data-cta="\[data-cta\]" defer></script>')


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_cro_js_behaviour():
    r = subprocess.run(["node", str(Path(__file__).with_name("cro.check.mjs")), str(ROOT / "lotrepere" / "cro.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr + r.stdout


@pytest.mark.skipif(not GAS_WEB.is_dir(), reason="octave-checkout checkout not next to this repo")
def test_tracking_files_match_octave_checkout():
    for f in ("cro.js", "mesure.js"):
        assert (ROOT / "lotrepere" / f).read_bytes() == (GAS_WEB / f).read_bytes(), f
    # tests/cro.check.mjs: gas/web/cro.check.mjs adapted to the Jalonel cro.js (no default host).


def test_mesure_counts_this_site_under_its_own_id():
    js = (ROOT / "lotrepere" / "mesure.js").read_text(encoding="utf-8")
    # since the 2026-10-24 merge the pages are served on ao-formation.jalonel.com/secteurs/: the host maps to the
    # ao-formation site id (paths /secteurs/...), and cro.js is tagged with the same id
    assert CRO_API in js and "'ao-formation':'veille-ao-formation'" in js and "site:J," in js and "vread" not in js
    assert "octave_notrack" in js and "v:2" in js
    assert CRO_SITE == "veille-ao-formation"


@pytest.mark.parametrize("open_", [True, False])
def test_every_page_loads_tracking(tmp_path, monkeypatch, open_):
    monkeypatch.setenv("SALES_ALLOW_TEST", "1")
    c = json.loads((ROOT / "sales.config.json").read_text())
    c.update(SALES_OPEN=open_, endpoint="https://octave-test.invalid/exec", publishableKey="pk_test_fake123")
    f = tmp_path / "sales.json"
    f.write_text(json.dumps(c))
    db = {}
    store.merge(db, boamp.normalise_all(json.loads((FIX / "boamp_records.json").read_text())["results"], today="2026-10-13"))
    out = tmp_path / "site"
    Site(out, SITE, NOW, sales=sales.load(f)).build(store.tenders(db), {"updated_at": "2026-10-13T04:20:00+00:00"})
    assert (out / "cro.js").read_bytes() == (ROOT / "lotrepere" / "cro.js").read_bytes()
    assert (out / "mesure.js").read_bytes() == (ROOT / "lotrepere" / "mesure.js").read_bytes()
    pages = list(out.rglob("*.html"))
    assert len(pages) > 40 and (out / "404.html") in pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        m = TAG.search(html)
        assert m and m.groups() == (CRO_SITE, CRO_API), page
        assert html.count('src="/secteurs/cro.js"') == 1 and html.count('src="/secteurs/mesure.js"') == 1, page
        assert "xp-config.js" not in html and html.index("/cro.js") < html.index("/static/cro-goals.js"), page
        assert "window.OCTAVE_XP" not in html, page  # config in a file: the order pages forbid inline scripts
    if open_:
        alerts = (out / "alertes" / "index.html").read_text(encoding="utf-8")
        for sid in ("hero", "prix", "commander", "faq"):
            assert f' id="{sid}"' in alerts, sid
        for p in ("alertes/index.html", "alertes/commande.html"):
            csp = html_mod.unescape(re.search(r'Content-Security-Policy" content="([^"]+)"', (out / p).read_text()).group(1))
            assert "script-src 'self'" in csp and "connect-src 'self' https://script.google.com" in csp, p
    privacy = (out / "confidentialite" / "index.html").read_text(encoding="utf-8")
    for w in ("13 octobre 2026", "Global Privacy Control", "nom</em> du dernier champ",
              "recommandation de la CNIL du 17 septembre 2020", "article 82"):
        assert w in privacy, w
    assert "Test A/B" not in privacy  # lot-repere-prix12 closed on 2026-10-24
