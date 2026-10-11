# Veille AO Formation

Open French public tenders for **training services** (formation professionnelle), refreshed twice a day from free official open data, published as a static site with filters, RSS/Atom, JSON and weekly CSV/ICS exports.

**Site:** https://vread-corp.github.io/veille-ao-formation/

A product of READ FRANCE SAS. No accounts, no cookies, no trackers, no LLM.

## What it does

1. `python -m veille update` fetches:
   - **BOAMP** (DILA) through the Opendatasoft Explore API v2.1 (`boamp-datadila.opendatasoft.com`, dataset `boamp`): calls for tenders still open, plus everything published in the last 14 days (rectificatifs, cancellations);
   - **TED** search API v3 (`api.ted.europa.eu/v3/notices/search`, no key): French buyers, CPV 80000000 tree + 79632000/79633000, calls for competition only.
2. Keeps a notice if it is a services contract and has a training CPV (804/805/806xxxxx, 80000000, 79632000, 79633000), or BOAMP's descriptor "Formation" (code 410), or (only when no CPV is given) a training keyword in the title/lot names. See `veille/model.py`.
3. Normalises (deadline as written in the notice, regions from departments / NUTS codes, CPV, estimated value, lots), merges into `data/notices.json` and de-duplicates by eForms procedure id (BOAMP + TED copies, rectificatifs).
4. `python -m veille build` writes the site to `_site/`: open tenders with client-side filters (keyword, region, deadline), weekly archive pages, region pages, Atom + RSS feeds, `data/ouverts.json`, sitemap.
5. `python -m veille export` writes the weekly release assets `ao-formation-ouverts.csv` and `ao-formation-echeances.ics`.
6. `python -m veille digest --profile examples/profil-exemple.json` renders a personalised e-mail digest (engine of the planned paid alerts; **not sent**: no mailbox or payments yet).

Personal data minimisation: contact names, e-mails and phone numbers present in notices are never copied; only the buyer organisation is shown.

## Automation (GitHub Actions)

- `update.yml`: twice a day (05:40 and 17:40 UTC) — tests, fetch, commit `data/` if changed, build and deploy GitHub Pages. Manual run accepts `since=YYYY-MM-DD` to backfill.
- `weekly-release.yml`: Mondays 06:30 UTC — creates the release `export-YYYY-Www` with the CSV and ICS. Assets keep stable names, so `releases/latest/download/ao-formation-ouverts.csv` always points to the latest week. An existing release is never overwritten (download counts are the KPI).

## Summaries (off)

`veille/summarize.py` defines a pluggable `Summarizer`; the default is `none`. No LLM key exists for this project yet. To add one later: implement a class, register it, set `VEILLE_SUMMARIZER` and the key as an Actions secret.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m veille update            # network
python -m veille build --out _site
```

Python 3.12, standard library only at runtime.

## Sources and licences

- BOAMP — DILA, [Licence Ouverte / Open Licence](https://www.data.gouv.fr/datasets/boamp) (Etalab).
- TED — Publications Office of the EU; notices reusable under Commission Decision 2011/833/EU ([legal notice](https://ted.europa.eu/en/legal-notice)). TED logos are not used. Not affiliated with or endorsed by the Publications Office or the DILA.
- Code: MIT (see `LICENSE`). Official notices prevail; always check the notice and tender documents.

## Limits

Not exhaustive: many MAPA (below 90 k€) are published only on buyer profiles. Classification relies on the codes chosen by buyers.
