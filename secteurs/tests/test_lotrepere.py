"""Pipeline (niche rules, BOAMP/TED normalisation, de-duplication, exports, digest) and the static site."""

import copy
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lotrepere import boamp, geo, sales, store, summarize, ted
from lotrepere.digest import Profile, render, select
from lotrepere.exporters import fmt_deadline, to_atom, to_csv, to_ics, to_json, to_rss
from lotrepere.model import NICHE_LABELS, NICHE_SLUGS, NICHES, Notice, classify, classify_niches
from lotrepere.site import Site

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
TODAY = "2026-10-13"
SITE = "https://example.org/lot-repere/"
S = ["SERVICES"]


@pytest.fixture()
def boamp_records():
    return json.loads((FIX / "boamp_records.json").read_text())["results"]


@pytest.fixture()
def ted_notices():
    return json.loads((FIX / "ted_notices.json").read_text())["notices"]


def by_id(records, idweb):
    return next(r for r in records if r["idweb"] == idweb)


# --- geo -----------------------------------------------------------------


def test_regions_cover_all_metropolitan_departments():
    metro = [f"{i:02d}" for i in range(1, 96) if i != 20] + ["2A", "2B"]
    assert all(geo.region_for_department(d) for d in metro)
    assert geo.region_for_department("971") == geo.OUTRE_MER
    assert len(geo.REGION_SLUGS) == 14
    assert geo.region_for_nuts("FR101") == "Île-de-France"
    assert geo.department_from_postcode("75019") == "75"


# --- niche rules -----------------------------------------------------------


def test_three_niches():
    assert NICHE_SLUGS == ("nettoyage", "espaces-verts", "securite")
    assert NICHE_LABELS == {"nettoyage": "Nettoyage et propreté", "espaces-verts": "Espaces verts",
                            "securite": "Sécurité privée"}


def test_classify_by_cpv_descriptor_keyword():
    assert classify_niches(title="Nettoyage des bureaux", cpv=["90919200"], contract_types=S) == {"nettoyage": ["cpv", "mot-cle"]}
    assert classify_niches(title="Entretien", cpv=["77310000"], descriptors=["116"], contract_types=S) == {"espaces-verts": ["cpv", "descripteur"]}
    assert classify_niches(title="Gardiennage du siège", cpv=["79713000"], contract_types=S) == {"securite": ["cpv", "mot-cle"]}
    # descriptor alone (no CPV)
    assert classify_niches(title="Prestations diverses", cpv=[], descriptors=["239"], contract_types=S) == {"nettoyage": ["descripteur"]}
    # keyword alone counts only when the notice has no CPV code at all
    assert classify_niches(title="Nettoyage des locaux de la mairie", cpv=[], contract_types=S) == {"nettoyage": ["mot-cle"]}
    assert classify_niches(title="Nettoyage des locaux de la mairie", cpv=["45000000"], contract_types=S) == {}
    # a multiservice contract belongs to several niches
    multi = classify_niches(title="Contrat multiservices", cpv=["90910000", "77310000", "79710000"], contract_types=S)
    assert list(multi) == ["nettoyage", "espaces-verts", "securite"]
    assert classify(title="Contrat multiservices", cpv=["90910000", "77310000"], contract_types=S) == ["cpv"]


def test_works_and_supplies_are_left_out():
    assert classify_niches(title="Travaux d'aménagement paysager", cpv=["77310000"], contract_types=["TRAVAUX"]) == {}
    assert classify_niches(title="Achat d'une auto-laveuse", cpv=["90910000"], contract_types=["FOURNITURES"]) == {}
    assert classify_niches(title="Vidéoprotection", cpv=["79710000"], descriptors=["331"], contract_types=["FOURNITURES"]) == {}


def test_descriptor_only_false_positives():
    # « Nettoyage » descriptor on street cleaning, refuse or pest control: not cleaning of premises
    assert classify_niches(title="Nettoiement des rues", cpv=["90610000"], descriptors=["239"], contract_types=S) == {}
    assert classify_niches(title="Dératisation", cpv=["90923000"], descriptors=["239"], contract_types=S) == {}
    # « Espaces verts » descriptor on an engineering study
    assert classify_niches(title="AMO écoles et parcs", cpv=["71241000"], descriptors=["116"], contract_types=S) == {}
    # « Gardiennage » descriptor on alarm maintenance
    assert classify_niches(title="Maintenance des alarmes", cpv=["50324100", "35120000"], descriptors=["138"], contract_types=S) == {}
    # ... but a niche CPV or keyword next to the descriptor keeps it
    assert "espaces-verts" in classify_niches(title="Élagage", cpv=["71241000"], descriptors=["116"], contract_types=S)


def test_surveillance_descriptor_is_weak():
    # 331 « Surveillance » alone: also network or structure monitoring
    assert classify_niches(title="Surveillance du réseau pluvial", cpv=[], descriptors=["331"], contract_types=S) == {}
    assert classify_niches(title="Gardiennage de la préfecture", cpv=[], descriptors=["331"], contract_types=S) == {"securite": ["descripteur", "mot-cle"]}
    assert classify_niches(title="Sûreté", cpv=["79714000"], descriptors=["331"], contract_types=S) == {"securite": ["cpv", "descripteur"]}
    assert classify_niches(title="Gardiennage", cpv=[], descriptors=["138"], contract_types=S) == {"securite": ["descripteur", "mot-cle"]}


def test_tree_felling_yes_logging_no():
    assert "espaces-verts" in classify_niches(title="Abattage d'arbres", cpv=["77211400"], contract_types=S)
    assert "espaces-verts" in classify_niches(title="Entretien des arbres", cpv=["77211500"], contract_types=S)
    assert classify_niches(title="Exploitation forestière", cpv=["77211100"], contract_types=S) == {}


def test_server_side_prefilters_cover_every_niche():
    for n in NICHES.values():
        for d in n.descriptors:
            assert f'dc="{d}"' in boamp.CANDIDATES
        for p in n.cpv:
            assert f'donnees like "{p}*"' in boamp.CANDIDATES
            assert (p + "0" * 8)[:8] in ted.CPV_QUERY


# --- BOAMP / TED normalisation ----------------------------------------------


def test_boamp_niches_on_real_notices(boamp_records):
    got = {r["idweb"]: boamp.normalise(r, today=TODAY) for r in boamp_records}
    niches = {k: (v.niches if v else None) for k, v in got.items()}
    assert niches["26-97059"] == ["nettoyage"] and niches["26-96228"] == ["nettoyage"]
    assert niches["26-98400"] == ["espaces-verts"] and niches["26-98451"] == ["espaces-verts"]
    assert niches["26-98569"] == ["securite"] and niches["26-99014"] == ["securite"]
    # street cleaning (CPV 9061), video-protection and nurse-call supplies, a works contract
    for idweb in ("26-90400", "26-87158", "26-98143", "26-97987", "26-98922"):
        assert niches[idweb] is None, idweb
    n = got["26-96228"]
    assert n.descriptors == ["239"] and n.regions == ["Grand Est"] and n.url.startswith("https://")
    assert n.deadline and n.deadline >= TODAY


def test_boamp_personal_data_not_copied(boamp_records):
    notices = boamp.normalise_all(boamp_records, today=TODAY)
    dumped = json.dumps([n.to_dict() for n in notices], ensure_ascii=False)
    assert "@" not in dumped.replace("@listName", "")


def test_redact_and_deadline():
    from lotrepere.model import redact
    assert redact("Contact : jean.dupont@ville.fr ou 01 23 45 67 89") == "Contact : […] ou […]"
    assert boamp.pick_deadline(["2026-10-01T12:00", "2026-11-01T12:00"], TODAY) == "2026-11-01T12:00"
    assert boamp.pick_deadline([], TODAY) is None


def test_boamp_fetch_paginates():
    pages = [{"results": [{"idweb": str(i)} for i in range(100)]}, {"results": [{"idweb": "x"}]}]
    calls = []

    def fake_get(url, params, body):
        calls.append(params["offset"])
        return pages[len(calls) - 1]

    assert len(list(boamp.fetch("true", get=fake_get))) == 101 and calls == [0, 100]


def test_ted_normalise(ted_notices):
    items = {n.source_id: n for n in ted.normalise_all(ted_notices, today=TODAY)}
    assert items["620145-2026"].niches == ["nettoyage"] and items["620145-2026"].regions == ["Occitanie"]
    assert items["546322-2026"].niches == ["espaces-verts"]
    assert items["64318-2026"].niches == ["securite"] and items["64318-2026"].regions == ["Grand Est"]
    assert all(n.url.startswith("https://ted.europa.eu/fr/notice/-/detail/") for n in items.values())


def test_ted_fetch_pages():
    def fake_get(url, params, body):
        assert "buyer-country=FRA" in body["query"] and "79710000" in body["query"]
        return {"notices": [{"publication-number": f"{body['page']}-{i}"} for i in range(100 if body["page"] == 1 else 3)]}

    assert len(list(ted.fetch(ted.query_open(NOW.date()), get=fake_get))) == 103


# --- store ----------------------------------------------------------------------


def mk(id_, **kw):
    base = dict(id=id_, source=id_.split(":")[0].upper(), source_id=id_.split(":")[1], title="Nettoyage des locaux",
                buyer="Ville", published="2026-10-01", deadline="2026-11-01T12:00",
                url=f"https://x/{id_}", regions=["Bretagne"], cpv=["90919200"], match=["cpv"], niches=["nettoyage"],
                descriptors=["239"], contract_types=["Services"])
    base.update(kw)
    return Notice(**base)


def test_dedupe_boamp_ted_and_rectificatif():
    db = {}
    store.merge(db, [
        mk("boamp:1", procedure_id="P1", published="2026-10-01"),
        mk("ted:9-2026", procedure_id="P1", published="2026-10-02", estimated_value=50000.0, descriptors=[]),
        mk("boamp:2", procedure_id="P1", published="2026-10-05", status="rectificatif", deadline="2026-11-15T12:00"),
        mk("boamp:3", published="2026-10-01"),
        mk("boamp:4", published="2026-10-03", supersedes=["boamp:3"], status="rectificatif"),
        mk("boamp:5", procedure_id="P5", published="2026-10-04"),
        mk("boamp:6", procedure_id="P5", published="2026-10-06", status="annule"),
    ])
    ts = {t.id: t for t in store.tenders(db)}
    assert set(ts) == {"boamp:2", "boamp:4", "boamp:6"}
    t = ts["boamp:2"]
    assert t.published == "2026-10-01" and t.updated == "2026-10-05" and t.deadline == "2026-11-15T12:00"
    assert t.estimated_value == 50000.0 and any(k.startswith("TED") for k in t.alt_urls)
    assert t.niches == ["nettoyage"]
    assert ts["boamp:6"].status == "annule" and not ts["boamp:6"].is_open(NOW)


def test_tenders_reapply_current_rules():
    """A notice stored under looser rules (descriptor 239 on street cleaning) disappears without a refetch."""
    db = {}
    stale = mk("boamp:old", title="Nettoiement des rues", cpv=["90610000"], match=["descripteur"], niches=["nettoyage"])
    store.merge(db, [stale, mk("boamp:1")])
    assert {t.id for t in store.tenders(db)} == {"boamp:1"}


def test_store_roundtrip(tmp_path):
    db = {}
    store.merge(db, [mk("boamp:1"), mk("ted:2-2026", descriptors=[])])
    p = tmp_path / "n.json"
    assert store.save(p, db) is True and store.save(p, db) is False
    assert store.load(p)["boamp:1"] == db["boamp:1"]


# --- exports --------------------------------------------------------------------


def sample_items():
    return [mk("boamp:1", title="Nettoyage; « vitrerie », lot 1", description="Desc, avec virgule"),
            mk("ted:2-2026", regions=[], deadline="2026-10-16T09:30", title="E" * 300, niches=["securite"],
               cpv=["79713000"], descriptors=[])]


def test_csv_export():
    text = to_csv(sample_items(), NOW)
    rows = list(csv.reader(io.StringIO(text[1:]), delimiter=";"))
    assert rows[0][:4] == ["id", "source", "metiers", "titre"] and len(rows) == 3
    assert rows[1][2] == "Nettoyage et propreté" and rows[2][2] == "Sécurité privée"
    assert rows[1][3] == "Nettoyage; « vitrerie », lot 1"


def test_ics_and_feeds():
    ics = to_ics(sample_items(), NOW, SITE)
    assert ics.count("BEGIN:VEVENT") == 2 and all(len(x.encode()) <= 75 for x in ics.split("\r\n"))
    assert "@lot-repere" in ics and "Veille AO (Jalonel)" in ics  # UIDs kept stable through the 2026-10-24 merge
    atom = ET.fromstring(to_atom(sample_items(), NOW, SITE))
    assert len(atom.findall("{http://www.w3.org/2005/Atom}entry")) == 2
    rss = ET.fromstring(to_rss(sample_items(), NOW, SITE))
    assert "Sécurité privée" in [c.text for c in rss.findall("./channel/item/category")]


def test_json_feed_contract():
    """The contract read every morning by octave-checkout gas/src/Alerts.js."""
    data = json.loads(to_json(sample_items(), NOW, SITE))
    assert data["niches"] == {"nettoyage": "Nettoyage et propreté", "espaces-verts": "Espaces verts",
                              "securite": "Sécurité privée"}
    assert data["count"] == 2 and "Licence Ouverte" in data["licence"] and data["generated_at"].startswith("2026-10-13")
    t = data["tenders"][0]
    for k in ("id", "title", "buyer", "url", "deadline", "regions", "cpv", "cpv_libelle", "procedure", "published",
              "lots", "description", "status", "niches"):
        assert k in t, k
    assert t["niches"] == ["nettoyage"] and "descriptors" not in t and t["url"].startswith("https://")


def test_fmt_deadline():
    assert fmt_deadline("2026-11-03T12:00") == "3 nov. 2026 à 12h00"
    assert fmt_deadline("2026-11-03T23:59") == "3 nov. 2026"


# --- digest -----------------------------------------------------------------------


def test_digest_profile_filters():
    items = [
        mk("boamp:1", title="Nettoyage vitrerie", regions=["Bretagne"]),
        mk("boamp:2", title="Gardiennage", regions=["Bretagne"], niches=["securite"]),
        mk("boamp:3", title="Nettoyage vitrerie", regions=["Normandie"]),
        mk("ted:4-2026", title="Nettoyage vitrerie", regions=[]),  # national
    ]
    p = Profile(niches=["nettoyage"], regions=["Bretagne"], keywords=["VITRERIE"])
    assert [t.id for t in select(items, p, NOW)] == ["boamp:1", "ted:4-2026"]
    assert [t.id for t in select(items, Profile(niches=["securite"]), NOW)] == ["boamp:2"]
    subject, text, html = render(select(items, p, NOW), p, NOW, SITE)
    assert subject.startswith("2 appels d'offres") and "https://x/boamp:1" in text and "<table" in html


def test_summarizer_off_by_default(monkeypatch):
    monkeypatch.delenv("LOTREPERE_SUMMARIZER", raising=False)
    assert summarize.get_summarizer().name == "none"


# --- site -------------------------------------------------------------------------


def build_fixture_site(tmp_path, boamp_records, ted_notices, **kw):
    db = {}
    store.merge(db, boamp.normalise_all(boamp_records, today=TODAY))
    store.merge(db, ted.normalise_all(ted_notices, today=TODAY))
    out = tmp_path / "site"
    Site(out, SITE, NOW, **kw).build(store.tenders(db), {"updated_at": "2026-10-13T04:20:00+00:00"})
    return out, store.tenders(db)


def test_site_build(tmp_path, boamp_records, ted_notices):
    out, ts = build_fixture_site(tmp_path, boamp_records, ted_notices)
    index = (out / "index.html").read_text()
    open_ = [t for t in ts if t.is_open(NOW)]
    assert index.count('class="card"') == len(open_)
    assert "13 oct. 2026 à 06h20" in index
    assert 'href="/lot-repere/feed.xml"' in index and 'id="niche"' in index
    for k in NICHE_SLUGS:
        page = (out / k / "index.html").read_text()
        assert page.count('class="card"') == sum(1 for t in open_ if k in t.niches), k
        assert 'id="niche"' not in page  # the niche page is already filtered
        assert (out / k / "ile-de-france" / "index.html").exists()
    for path in ["feed.xml", "rss.xml", "data/ouverts.json", "sitemap.xml", "robots.txt", "404.html",
                 "alertes/index.html", "alertes/exemple-email.html", "methode/index.html",
                 "mentions-legales/index.html", "confidentialite/index.html", "static/style.css", "static/app.js",
                 "mesure.js", "cro.js"]:
        assert (out / path).exists(), path
    assert not (out / "static" / "xp-config.js").exists()  # price test closed on 2026-10-24
    assert (out / "5be19f56d46ca3d2553bee28ebfa8edd.txt").read_text() == "5be19f56d46ca3d2553bee28ebfa8edd"  # IndexNow
    assert "Jalonel est une marque de READ FRANCE SAS." in (out / "mentions-legales" / "index.html").read_text()
    # Free CSV / ICS downloads served by the site itself (not GitHub Releases); no GitHub repository link anywhere.
    assert (out / "telechargements" / "ao-secteurs-ouverts.csv").read_text(encoding="utf-8").startswith("\ufeffid;")
    assert (out / "telechargements" / "ao-secteurs-echeances.ics").read_text(encoding="utf-8").startswith("BEGIN:VCALENDAR")
    assert 'telechargements/ao-secteurs-ouverts.csv" data-mesure="free_download"' in index
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r"github\.com/VREAD-corp|/releases/|Code source|vread", html, re.I), page
    for path in ["index.html", "404.html", "alertes/index.html"]:
        footer = (out / path).read_text().split('<footer class="foot">')[1]
        assert '<a href="https://jalonel.com/">Une marque Jalonel</a>' in footer and "READ FRANCE" not in footer, path
        assert "outils.vread.fr" not in footer, path
    feed = json.loads((out / "data" / "ouverts.json").read_text())
    assert feed["niches"]["securite"] == "Sécurité privée" and all(t["niches"] for t in feed["tenders"])
    sitemap = (out / "sitemap.xml").read_text()
    assert "exemple-email" not in sitemap and SITE + "nettoyage/" in sitemap
    # thin region pages (no local notice) stay out of the index and of the sitemap
    corse = (out / "securite" / "corse" / "index.html").read_text()
    assert '<meta name="robots" content="noindex, follow">' in corse and SITE + "securite/corse/" not in sitemap
    assert '<meta name="robots" content="noindex">' in (out / "alertes" / "exemple-email.html").read_text()


def test_no_other_brand_on_the_site(tmp_path, boamp_records, ted_notices):
    out, _ = build_fixture_site(tmp_path, boamp_records, ted_notices)
    for f in out.rglob("*"):
        # Since the 2026-10-24 merge into ao-formation the section is « Veille AO »; the old name survives only in the CGV
        # (« anciennement Lot Repéré »), and the training tenders are one link away (nav « Formation » → site root).
        if f.is_file() and f.suffix in (".html", ".xml", ".json", ".txt", ".css") or f.name in ("app.js", "landing.js", "cro-goals.js"):
            body = f.read_text(encoding="utf-8")
            if "cgv-pro" not in f.parts:
                assert "Lot Repéré" not in body and "lot-repere.jalonel.com" not in body, f
            if f.suffix == ".html" and f.name != "exemple-email.html":
                assert '<a href="/">Formation</a>' in body or 'href="/" aria-current' in body, f


def test_title_escaped(tmp_path, boamp_records, ted_notices):
    db = {}
    store.merge(db, boamp.normalise_all(boamp_records, today=TODAY))
    evil = copy.deepcopy(next(t for t in db.values() if t.niches))
    evil.id, evil.source_id, evil.procedure_id = "boamp:evil", "evil", None
    evil.title = "</script><script>alert(1)</script>"
    db[evil.id] = evil
    out = tmp_path / "s"
    Site(out, SITE, NOW).build(store.tenders(db), {})
    for page in ("index.html", f"{evil.niches[0]}/index.html"):
        html = (out / page).read_text()
        assert "<script>alert(1)</script>" not in html, page


def test_home_jsonld(tmp_path, boamp_records, ted_notices):
    out, _ = build_fixture_site(tmp_path, boamp_records, ted_notices)
    html = (out / "index.html").read_text()
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    items = [i for b in blocks for i in (b if isinstance(b, list) else [b])]
    types = {i["@type"] for i in items}
    assert types == {"WebSite", "Organization", "Dataset"}
    org = next(i for i in items if i["@type"] == "Organization")
    assert org["name"] == "Veille AO" and org["parentOrganization"]["@id"] == "https://jalonel.com/#org"
    assert org["parentOrganization"]["parentOrganization"]["name"] == "READ FRANCE SAS"
    assert "aggregateRating" not in html and "review" not in html.lower().replace("preview", "")
