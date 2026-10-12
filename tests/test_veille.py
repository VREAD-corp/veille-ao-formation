import copy
import csv
import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from veille import boamp, geo, sales, store, summarize, ted
from veille.digest import Profile, render, select
from veille.exporters import fmt_deadline, to_atom, to_csv, to_ics, to_json, to_rss
from veille.model import Notice, classify, is_training_cpv
from veille.site import Site

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
TODAY = "2026-10-11"
SITE = "https://example.org/veille-ao-formation/"


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
    assert geo.region_for_department("20") == "Corse"
    assert geo.region_for_department("1") == "Auvergne-Rhône-Alpes"


def test_nuts_and_postcodes():
    assert geo.region_for_nuts("FRK26") == "Auvergne-Rhône-Alpes"
    assert geo.region_for_nuts("FR101") == "Île-de-France"
    assert geo.region_for_nuts("FRY10") == geo.OUTRE_MER
    assert geo.region_for_nuts("FRA") is None
    assert geo.region_for_nuts("anyw") is None
    assert geo.department_from_postcode("75019") == "75"
    assert geo.department_from_postcode("20000") == "2A"
    assert geo.department_from_postcode("20200") == "2B"
    assert geo.department_from_postcode("97400") == "974"
    assert geo.department_from_postcode("ABC") is None


# --- relevance rules --------------------------------------------------------


def test_training_cpv():
    assert is_training_cpv("80530000")
    assert is_training_cpv("80511000-2")
    assert is_training_cpv("79632000")
    assert not is_training_cpv("80100000")  # primary education
    assert not is_training_cpv("90911000")


def test_classify_rules():
    assert classify(title="Nettoyage", cpv=["80500000"], contract_types=["SERVICES"]) == ["cpv"]
    # keyword alone is enough only when the notice has no CPV code
    assert classify(title="Formation SST", cpv=[], contract_types=["SERVICES"]) == ["mot-cle"]
    assert classify(title="Nettoyage du centre de formation", cpv=["90911000"],
                    contract_types=["SERVICES"]) == []
    # works or supplies contracts are excluded, whatever the reason
    assert classify(title="Construction d'un centre de formation", cpv=[], contract_types=["TRAVAUX"]) == []
    assert classify(title="Robot livré avec formation", cpv=["42000000", "80500000"],
                    contract_types=["FOURNITURES"]) == []
    assert classify(title="Analyse des pratiques", cpv=[], descriptors=["410"],
                    contract_types=["SERVICES"]) == ["descripteur"]
    # descriptor "Formation" alone, with only non-training CPV divisions (66, 90, 92): false positive
    assert classify(title="Gestion de l'animation enfance et jeunesse", cpv=["92000000"], descriptors=["410"],
                    contract_types=["SERVICES"]) == []
    assert classify(title="Service de courtage en assurances", cpv=["66518100", "66510000"], descriptors=["410"],
                    contract_types=["SERVICES"]) == []
    # ... but a training word, a training CPV or another CPV division keeps it
    assert classify(title="Formation des animateurs enfance", cpv=["92000000"], descriptors=["410"],
                    contract_types=["SERVICES"]) == ["descripteur", "mot-cle"]
    assert classify(title="Animation", cpv=["92000000", "80530000"], descriptors=["410"],
                    contract_types=["SERVICES"]) == ["cpv", "descripteur"]
    assert classify(title="Sensibilisation des utilisateurs aux risques cyber", cpv=["48931000"], descriptors=["410"],
                    contract_types=["SERVICES"]) == ["descripteur"]
    # accents and plural are handled; "information"/"transformation" are not matches
    assert classify(title="Formations à la sécurité", cpv=[]) == ["mot-cle"]
    assert classify(title="Transformation numérique et information", cpv=[]) == []


# --- BOAMP normalisation ------------------------------------------------------


def test_boamp_eforms_multi_lot(boamp_records):
    n = boamp.normalise(by_id(boamp_records, "26-92146"), today=TODAY)
    assert n is not None
    assert n.id == "boamp:26-92146"
    assert n.source == "BOAMP"
    assert n.deadline == "2026-10-28T12:00"  # wall-clock time written in the notice
    assert n.regions == ["Auvergne-Rhône-Alpes"]
    assert n.departments == ["01", "38", "69", "73", "74"]  # "1" in the source is normalised
    assert any(c.startswith("805") for c in n.cpv)
    assert "cpv" in n.match
    assert len(n.lots) >= 5 and n.lots[0].startswith("Lot n°1")
    assert n.url.startswith("https://www.boamp.fr/")
    assert n.procedure_id


def test_boamp_fnsimple_training_lot(boamp_records):
    n = boamp.normalise(by_id(boamp_records, "26-93021"), today=TODAY)
    assert n is not None, "lot 2 has CPV 80500000"
    assert n.deadline == "2026-10-26T12:00"
    assert "80500000" in n.cpv and "79998000" in n.cpv
    assert n.estimated_value == 65000.0
    assert n.regions == ["Île-de-France"]


def test_boamp_rejects_stemming_false_positive(boamp_records):
    # "forme de radoub" is returned by the API full-text search for "formation"
    assert boamp.normalise(by_id(boamp_records, "26-91889"), today=TODAY) is None
    # temp staff contract, a rectificatif, no training code
    assert boamp.normalise(by_id(boamp_records, "26-98039"), today=TODAY) is None


def test_boamp_personal_data_not_copied(boamp_records):
    notices = boamp.normalise_all(boamp_records, today=TODAY)
    dumped = json.dumps([n.to_dict() for n in notices], ensure_ascii=False)
    assert "@" not in dumped.replace("@listName", "")


def test_redact():
    from veille.model import redact
    assert redact("Contact : jean.dupont@ville.fr ou 01 23 45 67 89, +33 6 12 34 56 78") == "Contact : […] ou […], […]"
    assert redact("Réf. 2026-0123456789 et UUID 09-6851-4122") == "Réf. 2026-0123456789 et UUID 09-6851-4122"


def test_pick_deadline():
    assert boamp.pick_deadline(["2026-10-01T12:00", "2026-11-01T12:00"], TODAY) == "2026-11-01T12:00"
    assert boamp.pick_deadline(["2026-10-01T12:00", "2026-09-01T12:00"], TODAY) == "2026-10-01T12:00"
    assert boamp.pick_deadline([], TODAY) is None


def test_boamp_fetch_paginates():
    pages = [{"results": [{"idweb": str(i)} for i in range(100)]}, {"results": [{"idweb": "x"}]}]
    calls = []

    def fake_get(url, params, body):
        calls.append(params["offset"])
        return pages[len(calls) - 1]

    got = list(boamp.fetch("true", get=fake_get))
    assert len(got) == 101
    assert calls == [0, 100]


# --- TED normalisation --------------------------------------------------------


def test_ted_normalise(ted_notices):
    items = {n.source_id: n for n in ted.normalise_all(ted_notices, today=TODAY)}
    anfh = items["681506-2026"]
    assert anfh.source == "TED"
    assert anfh.deadline == "2026-11-16T10:00"
    assert anfh.regions == ["Île-de-France"]
    assert anfh.estimated_value == 1500000.0
    assert anfh.url == "https://ted.europa.eu/fr/notice/-/detail/681506-2026"
    assert anfh.procedure == "Autre procédure (une étape)"
    assert anfh.lots and anfh.lots[0].startswith("Lot 1")
    national = items["678033-2026"]
    assert len(national.regions) >= 10


def test_ted_fetch_pages():
    def fake_get(url, params, body):
        assert body["page"] in (1, 2) and "buyer-country=FRA" in body["query"]
        return {"notices": [{"publication-number": f"{body['page']}-{i}"} for i in range(100 if body["page"] == 1 else 3)]}

    assert len(list(ted.fetch(ted.query_open(NOW.date()), get=fake_get))) == 103


# --- store / de-duplication -----------------------------------------------------


def mk(id_, **kw):
    base = dict(id=id_, source=id_.split(":")[0].upper(), source_id=id_.split(":")[1], title="Formation",
                buyer="Ville", published="2026-10-01", deadline="2026-11-01T12:00",
                url=f"https://x/{id_}", regions=["Bretagne"], cpv=["80500000"], match=["cpv"])
    base.update(kw)
    return Notice(**base)


def test_dedupe_boamp_ted_and_rectificatif():
    db = {}
    store.merge(db, [
        mk("boamp:1", procedure_id="P1", published="2026-10-01"),
        mk("ted:9-2026", procedure_id="P1", published="2026-10-02", estimated_value=50000.0),
        mk("boamp:2", procedure_id="P1", published="2026-10-05", status="rectificatif",
           deadline="2026-11-15T12:00"),
        mk("boamp:3", published="2026-10-01"),
        mk("boamp:4", published="2026-10-03", supersedes=["boamp:3"], status="rectificatif"),
        mk("boamp:5", procedure_id="P5", published="2026-10-04"),
        mk("boamp:6", procedure_id="P5", published="2026-10-06", status="annule"),
    ])
    ts = {t.id: t for t in store.tenders(db)}
    assert set(ts) == {"boamp:2", "boamp:4", "boamp:6"}
    t = ts["boamp:2"]
    assert t.published == "2026-10-01" and t.updated == "2026-10-05"
    assert t.deadline == "2026-11-15T12:00"
    assert t.estimated_value == 50000.0
    assert any(k.startswith("TED") for k in t.alt_urls)
    assert ts["boamp:6"].status == "annule" and not ts["boamp:6"].is_open(NOW)


def test_store_roundtrip(tmp_path):
    db = {}
    store.merge(db, [mk("boamp:1"), mk("ted:2-2026")])
    p = tmp_path / "n.json"
    assert store.save(p, db) is True
    assert store.save(p, db) is False  # unchanged
    again = store.load(p)
    assert again["boamp:1"] == db["boamp:1"]


def test_tenders_drop_stored_descriptor_false_positives():
    db = {}
    fp = mk("boamp:fp")
    fp.match, fp.cpv = ["descripteur"], ["92000000"]
    ok = mk("boamp:ok")
    ok.match, ok.cpv = ["descripteur"], ["48931000"]
    store.merge(db, [fp, ok, mk("boamp:1")])
    assert {t.id for t in store.tenders(db)} == {"boamp:ok", "boamp:1"}


def test_open_tenders_sorted():
    items = [mk("boamp:1", deadline="2026-12-01T12:00"), mk("boamp:2", deadline="2026-10-20T12:00"),
             mk("boamp:3", deadline="2026-10-01T12:00")]
    assert [t.id for t in store.open_tenders(items, NOW)] == ["boamp:2", "boamp:1"]


# --- exports --------------------------------------------------------------------


def sample_items():
    return [mk("boamp:1", title="Formation; « SST », lot 1", description="Desc, avec virgule"),
            mk("ted:2-2026", regions=[], deadline="2026-10-14T09:30", title="E" * 300)]


def test_csv_export():
    text = to_csv(sample_items(), NOW)
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text[1:]), delimiter=";"))
    assert rows[0][0] == "id" and len(rows) == 3
    assert rows[1][2] == "Formation; « SST », lot 1"
    assert rows[2][6] == "3"  # days left


def test_ics_export_is_valid_and_folded():
    ics = to_ics(sample_items(), NOW, SITE)
    lines = ics.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 2
    assert all(len(line.encode()) <= 75 for line in lines)
    assert "DTEND:20261014T073000Z" in ics  # 09:30 Paris (CEST) = 07:30 UTC
    assert "SUMMARY:Date limite AO : Formation\\; « SST »\\, lot 1" in ics.replace("\r\n ", "")


def test_feeds_are_well_formed():
    items = sample_items()
    atom = ET.fromstring(to_atom(items, NOW, SITE))
    assert len(atom.findall("{http://www.w3.org/2005/Atom}entry")) == 2
    rss = ET.fromstring(to_rss(items, NOW, SITE))
    assert len(rss.findall("./channel/item")) == 2
    data = json.loads(to_json(items, NOW, SITE))
    assert data["count"] == 2 and "Licence Ouverte" in data["licence"]


def test_fmt_deadline():
    assert fmt_deadline("2026-11-03T12:00") == "3 nov. 2026 à 12h00"
    assert fmt_deadline("2026-11-03T23:59") == "3 nov. 2026"


# --- digest (paid tier engine) -----------------------------------------------------


def test_digest_profile_filters():
    items = [
        mk("boamp:1", title="Formation secourisme", regions=["Bretagne"]),
        mk("boamp:2", title="Formation anglais", regions=["Bretagne"]),
        mk("boamp:3", title="Formation secourisme", regions=["Normandie"]),
        mk("ted:4-2026", title="Formation secourisme", regions=[]),  # national
        mk("boamp:5", title="Formation secourisme", regions=["Bretagne"], deadline="2026-10-12T12:00"),
    ]
    p = Profile(regions=["Bretagne"], keywords=["SECOURISME"], min_days_left=3)
    assert [t.id for t in select(items, p, NOW)] == ["boamp:1", "ted:4-2026"]
    p.include_national = False
    assert [t.id for t in select(items, p, NOW)] == ["boamp:1"]
    subject, text, html = render(select(items, p, NOW), p, NOW, SITE)
    assert subject.startswith("1 appel d'offres") and "https://x/boamp:1" in text and "<table" in html


def test_summarizer_off_by_default(monkeypatch):
    monkeypatch.delenv("VEILLE_SUMMARIZER", raising=False)
    s = summarize.get_summarizer()
    assert s.name == "none"
    items = [mk("boamp:1")]
    assert summarize.apply(items, s) == 0 and items[0].summary == ""
    with pytest.raises(ValueError):
        summarize.get_summarizer("gpt")


# --- site -------------------------------------------------------------------------


def test_site_build(tmp_path, boamp_records, ted_notices):
    db = {}
    store.merge(db, boamp.normalise_all(boamp_records, today=TODAY))
    store.merge(db, ted.normalise_all(ted_notices, today=TODAY))
    evil = copy.deepcopy(next(iter(db.values())))
    evil.id, evil.source_id, evil.procedure_id = "boamp:evil", "evil", None
    evil.title = '<script>alert(1)</script>'
    db[evil.id] = evil
    out = tmp_path / "site"
    Site(out, SITE, NOW).build(store.tenders(db), {"updated_at": "2026-10-11T04:20:00+00:00"})
    index = (out / "index.html").read_text()
    assert "<script>alert(1)</script>" not in index and "&lt;script&gt;" in index
    assert index.count('class="card"') == len([t for t in store.tenders(db) if t.is_open(NOW)])
    assert "11 oct. 2026 à 06h20" in index  # data update time, Paris
    assert 'href="/veille-ao-formation/feed.xml"' in index
    for path in ["feed.xml", "rss.xml", "data/ouverts.json", "sitemap.xml", "robots.txt", "404.html",
                 "semaine/index.html", "region/bretagne/index.html", "alertes/index.html",
                 "alertes/exemple-email.html", "methode/index.html", "mentions-legales/index.html",
                 "confidentialite/index.html", "cgv/index.html", "static/style.css", "static/app.js"]:
        assert (out / path).exists(), path
    weeks = list((out / "semaine").glob("20*-W*/index.html"))
    assert weeks
    alerts = (out / "alertes" / "index.html").read_text()
    if sales.load().open:  # committed sales.config.json: subscription page (details in test_sales.py)
        assert 'id="octave-pay"' in alerts and "sans renouvellement automatique" in alerts
    else:
        assert "pas encore disponible" in alerts.lower() and "19&nbsp;€" in alerts
    sitemap = (out / "sitemap.xml").read_text()
    assert "cgv/" not in sitemap and SITE + "semaine/" in sitemap
