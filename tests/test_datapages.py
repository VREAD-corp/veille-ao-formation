"""Region v2, theme and OPCO pages, the two guides, the index rule, the theme classifier and the Qualiopi counts."""

import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from veille import datapages, stats, supply, taxonomy
from veille.model import Notice
from veille.site import Site

FIX = Path(__file__).parent / "fixtures"
SITE = "https://example.org/"
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 17)


def mk(i, *, title="Formation des agents", published="2026-09-01", deadline="2026-12-01T12:00", regions=("Bretagne",),
       buyer="Ville de Rennes", lots=(), cpv=("80530000",), status="initial"):
    return Notice(id=f"boamp:{i}", source="BOAMP", source_id=str(i), title=title, buyer=buyer, published=published,
                  deadline=deadline, url=f"https://www.boamp.fr/pages/avis/?q=idweb:{i}", regions=list(regions),
                  departments=["35"] if "Bretagne" in regions else [], cpv=list(cpv), lots=list(lots), status=status,
                  match=["cpv"], contract_types=["Services"])


def stream(n, *, start=date(2025, 10, 20), every=6, open_days=40, **kw):
    """n notices published every `every` days from `start`, each open `open_days` days."""
    out = []
    for k in range(n):
        pub = start + timedelta(days=every * k)
        out.append(mk(f"s{k}-{kw.get('title', 'x')[:5]}", published=pub.isoformat(),
                      deadline=(pub + timedelta(days=open_days)).isoformat() + "T12:00", **kw))
    return out


# --- theme classifier -------------------------------------------------------------------------------------------

def test_segments_clean_html_and_apostrophes():
    segs = taxonomy.segments("Usages de l’intelligence artificielle\n02 : Gerbeurs (R485)<br/>")
    assert "l'intelligence artificielle" in segs[0]
    assert " br " not in segs[1]  # « <br/> » must not read as the habilitation code BR
    sec = taxonomy.THEMES["securite-sst-incendie"]
    assert not sec.matches_text("Formation à la conduite en sécurité de gerbeurs (R485)<br/>")
    assert taxonomy.THEMES["numerique-bureautique-ia"].matches_text("Usages professionnels de l’intelligence artificielle")


@pytest.mark.parametrize("slug,text,expected", [
    ("securite-sst-incendie", "Formation Sauveteur Secouriste du Travail (SST) - MAC", True),
    ("securite-sst-incendie", "Programme Acculturation au numérique et prévention des risques cybersécurité", False),
    ("securite-sst-incendie", "Formation au permis poids lourds du service départemental d'incendie et de secours", False),
    ("conduite-caces-permis", "Formation travail en hauteur et permis d'échafauder", False),
    ("conduite-caces-permis", "Formations CACES R489 et R486", True),
    ("management-rh", "Fourniture d'une solution de Learning Management System (LMS)", False),
    ("management-rh", "Parcours de formation managériale des cadres", True),
    ("numerique-bureautique-ia", "Programmation et techniques du spectacle", False),
    ("langues", "Organisation de séjours linguistiques pour lycéens étrangers", False),
    ("sante-medico-social", "Formations IA dans les établissements de santé", False),
    ("sante-medico-social", "Formation de première intervention incendie des professionnels médicaux et non médicaux", False),
    ("insertion-emploi-poec", "Préparation à l'insertion professionnelle pour les élèves ingénieurs", False),
    ("insertion-emploi-poec", "POEC Formation aux métiers de l'industrie", True),
])
def test_theme_rules_examples(slug, text, expected):
    assert taxonomy.THEMES[slug].matches_text(text) is expected


def test_mission_locale_staff_training_is_not_insertion():
    t = mk(1, title="Programme régional de formation Occitanie 2027-2028", buyer="ARML Occitanie")
    assert not taxonomy.THEMES["insertion-emploi-poec"].matches(t)
    assert taxonomy.THEMES["insertion-emploi-poec"].matches(mk(2, title="Programme régional de formation 2027", buyer="Région Occitanie"))


def test_published_themes_pass_the_precision_gate():
    for slug, th in taxonomy.THEMES.items():
        ok, n = taxonomy.PRECISION[slug]
        assert th.published == (ok / n >= taxonomy.PRECISION_GATE), slug


def test_precision_on_the_hand_labelled_fixture():
    """Regression guard: the current rules on the 337 hand-labelled notices (tests/fixtures/theme_labels.json)."""
    data = json.loads((FIX / "theme_labels.json").read_text())["notices"]
    assert len(data) >= 300
    for slug, th in taxonomy.THEMES.items():
        if not th.published:
            continue
        pool = [x for x in data if x["all_themes_labelled"] or slug == "insertion-emploi-poec"]
        pred = [x for x in pool if th.matches(mk(x["id"], title=x["title"], lots=x["lots"], buyer=x["buyer"]))]
        good = [x for x in pred if slug in x["labels"]]
        assert len(pred) >= 15, slug
        assert len(good) / len(pred) >= taxonomy.PRECISION_GATE, (slug, len(good), len(pred))


def test_opco_and_france_travail_buyers():
    assert taxonomy.opco_of(mk(1, buyer="OPCO 2i - Direction Appui aux branches")).slug == "2i"
    assert taxonomy.opco_of(mk(2, buyer="L'OPCOMMERCE")).slug == "opcommerce"
    assert taxonomy.opco_of(mk(3, buyer="Uniformation")).slug == "cohesion-sociale"
    assert taxonomy.opco_of(mk(4, buyer="Atlas Copco France")) is None
    assert len(taxonomy.OPCOS) == 11
    assert taxonomy.is_france_travail(mk(5, buyer="FRANCE TRAVAIL - GRAND EST"))


# --- index rule and figures ---------------------------------------------------------------------------------------

def test_index_rule_thresholds_and_hysteresis():
    busy = stream(70, open_days=40)  # ~70 a year, ~6 open on a typical day
    d = stats.index_decision(busy, TODAY)
    assert d.index and d.n12 >= 40 and d.median_open >= 4 and d.robots is False
    thin = stream(20, every=18)
    assert not stats.index_decision(thin, TODAY).index
    assert stats.index_decision(thin, TODAY).robots == "noindex, follow"
    # 35 a year passes neither rule: keeps the state it had on RULE_START (indexed), until it drops under 30
    mid = stream(35, every=10, open_days=60)
    assert stats.index_decision(mid, TODAY, start=TODAY - timedelta(days=30)).index == stats.index_decision(
        mid, TODAY - timedelta(days=30), start=TODAY - timedelta(days=30)).index


def test_cancelled_notices_do_not_count():
    items = stream(50) + [mk("x", status="annule")]
    assert stats.index_decision(items, TODAY).n12 == 50


def test_figures():
    items = [mk(1, published="2026-09-01", deadline="2026-10-01T12:00", lots=["a", "b"], title="Accord-cadre de formation"),
             mk(2, published="2026-08-01", deadline="2026-08-21T12:00", buyer="VILLE DE RENNES"),
             mk(3, published="2025-01-01")]
    f = stats.figures(items, TODAY)
    assert f.n12 == 2 and f.median_days == 25 and f.with_lots == 1 and f.framework == 1
    assert f.top_buyers == [("Ville de Rennes", 2)]  # spelling variants grouped
    assert [m for m, _ in f.months][-1] == "2026-10" and len(f.months) == 12
    closed = stats.closed_recently(items, NOW)
    assert [t.id for t in closed] == ["boamp:1", "boamp:2"]


# --- Qualiopi counts ------------------------------------------------------------------------------------------

def test_qualiopi_aggregate_and_refresh(tmp_path):
    head = ("numeroDeclarationActivite;adressePhysiqueOrganismeFormation.codePostal;"
            "adressePhysiqueOrganismeFormation.codeRegion;certifications.actionsDeFormation\n")
    rows = ["1;35000;53;true", "2;35000;53;false", "3;97400;04;true", "4;20000;;true", "5;75019;11;"]
    agg = supply.aggregate(io.StringIO(head + "\n".join(rows) + "\n"), "2026-10-17")
    assert agg["regions"]["Bretagne"] == {"declared": 2, "qualiopi": 1}
    assert agg["regions"]["Outre-mer"]["qualiopi"] == 1 and agg["territories"]["La Réunion"]["qualiopi"] == 1
    assert agg["regions"]["Corse"]["qualiopi"] == 1  # no region code: placed from the postcode
    assert agg["qualiopi_total"] == 3 and agg["rows"] == 5
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"extracted": "2026-10-10"}))
    assert supply.refresh(path, TODAY, fetch=lambda: pytest.fail("fresh file must not be re-downloaded")) is False
    with pytest.raises(IOError):  # a truncated download never replaces the file
        supply.refresh(path, TODAY + timedelta(days=40), fetch=lambda: head + rows[0] + "\n")
    assert json.loads(path.read_text()) == {"extracted": "2026-10-10"}


def test_editorial_date():
    checked = date.fromisoformat(datapages.EDITORIAL_CHECKED)
    assert checked <= date.today()
    assert datapages.editorial_age_warning(checked) is None
    assert "EDITORIAL_CHECKED" in datapages.editorial_age_warning(checked + timedelta(days=200))


# --- built pages ------------------------------------------------------------------------------------------------

@pytest.fixture()
def built(tmp_path):
    items = (stream(60, title="Formation Sauveteur Secouriste du Travail (SST)")
             + stream(10, title="Formation des managers", regions=("Corse",), buyer="Collectivité de Corse", every=30)
             + stream(45, title="POEC métiers de l'industrie", buyer="OPCO 2i - Direction Appui aux branches",
                      regions=("Normandie",), every=8)
             + [mk("ft1", title="Mobilité Erasmus+ des demandeurs d'emploi", buyer="FRANCE TRAVAIL - BRETAGNE",
                   published="2026-10-01", deadline="2026-11-02T12:00")])
    out = tmp_path / "site"
    site = Site(out, SITE, NOW)
    site.build(items, {"updated_at": "2026-10-17T09:40:00+00:00"})
    return out, site


def read(out, path):
    return (out / path / "index.html").read_text()


def test_region_v2(built):
    out, site = built
    page = read(out, "region/bretagne")
    for part in ("Clôturés ces 90 derniers jours", "sur 12 mois", "Acheteurs les plus actifs",
                 'href="/alertes/?region=bretagne"', '"@type": "BreadcrumbList"', '"@type": "CollectionPage"',
                 "Délai médian pour répondre", 'class="national"'):
        assert part in page, part
    assert '<meta name="robots"' not in page  # 60 notices a year, ~6 open: indexed
    corse = read(out, "region/corse")
    assert '<meta name="robots" content="noindex, follow">' in corse and "reste consultable" in corse
    sitemap = (out / "sitemap.xml").read_text()
    assert SITE + "region/bretagne/" in sitemap and SITE + "region/corse/" not in sitemap


def test_theme_pages_only_when_they_pass(built):
    out, site = built
    assert (out / "theme" / "securite-sst-incendie" / "index.html").exists()
    assert not (out / "theme" / "langues").exists()  # never passed the rule: never built
    page = read(out, "theme/securite-sst-incendie")
    assert "Ce que les acheteurs commandent" in page and "INRS" in page and datapages.EVALUATION_PATH in page
    assert 'href="/theme/securite-sst-incendie/"' in read(out, "")  # home links the theme
    assert SITE + "theme/securite-sst-incendie/" in (out / "sitemap.xml").read_text()


def test_opco_page_and_guides(built):
    out, site = built
    opco = read(out, "acheteur/opco")
    assert opco.count('class="section opco"') == 11 and '<h2 id="2i-h">Opco 2i</h2>' in opco
    ft = read(out, "guide/appels-offres-france-travail-formation")
    assert "PLACE" in ft and "FRANCE TRAVAIL - BRETAGNE" in ft and '"@type": "Article"' in ft
    assert "assistant IA" in ft and "Tanguy Warsmann" in ft
    cpv = read(out, "guide/codes-cpv-formation")
    assert "80530000-8" in cpv and "Services de formation professionnelle" in cpv
    for page in (opco, ft, cpv):
        assert 'src="/mesure.js"' in page and 'src="/cro.js"' in page and 'rel="canonical"' in page
        assert "data-cta" in page
