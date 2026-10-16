"""Command line: python -m veille {update,build,export,digest}."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import boamp, store, summarize, ted
from .digest import Profile, render, select
from .exporters import to_csv, to_ics
from .model import PARIS, iso_week_bounds
from .site import Site

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "notices.json"
META = ROOT / "data" / "meta.json"
DEFAULT_SITE_URL = "https://ao-formation.jalonel.com/"
RECENT_DAYS = 14

log = logging.getLogger("veille")


def now_utc() -> datetime:
    fixed = os.environ.get("VEILLE_NOW")  # for reproducible tests/builds
    if fixed:
        return datetime.fromisoformat(fixed)
    return datetime.now(timezone.utc)


def load_meta() -> dict:
    return json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}


def cmd_update(args: argparse.Namespace) -> int:
    now = now_utc()
    today = now.astimezone(PARIS).date()
    db = store.load(DATA)
    meta = load_meta()
    meta.setdefault("last_success", {})
    meta["last_run_ok"] = {}
    since = date.fromisoformat(args.since) if args.since else today - timedelta(days=RECENT_DAYS)

    def ranges(start: date, end: date):
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=7), end)
            yield cur, nxt
            cur = nxt

    end = today + timedelta(days=1)
    jobs = {
        "BOAMP": lambda: [boamp.where_open()] + [boamp.where_published(a, b) for a, b in ranges(since, end)],
        "TED": lambda: [ted.query_open(today)] + [ted.query_published(a, b) for a, b in ranges(since, end)],
    }
    for source, queries in jobs.items():
        try:
            raw: dict[str, dict] = {}
            for q in queries():
                if source == "BOAMP":
                    for rec in boamp.fetch(q):
                        raw[str(rec.get("idweb"))] = rec
                else:
                    for rec in ted.fetch(q):
                        raw[str(rec.get("publication-number"))] = rec
            mod = boamp if source == "BOAMP" else ted
            notices = mod.normalise_all(raw.values(), today=today.isoformat())
            added = store.merge(db, notices)
            log.info("%s: %d fetched, %d relevant, %d new", source, len(raw), len(notices), added)
            meta["last_success"][source] = now.astimezone(PARIS).strftime("%Y-%m-%d %H:%M (heure de Paris)")
            meta["last_run_ok"][source] = True
        except Exception as exc:  # keep the other source and the previous data
            log.error("%s update failed: %s", source, exc)
            meta["last_run_ok"][source] = False

    if any(meta["last_run_ok"].values()):
        meta["updated_at"] = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    summarize.apply(list(db.values()), summarize.get_summarizer())
    changed = store.save(DATA, db)
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log.info("store: %d notices, changed=%s", len(db), changed)
    if not any(meta["last_run_ok"].values()):
        log.error("all sources failed")
        return 1
    return 0


def _tenders():
    return store.tenders(store.load(DATA))


def cmd_build(args: argparse.Namespace) -> int:
    now = now_utc()
    tenders = _tenders()
    Site(Path(args.out), args.site_url, now).build(tenders, load_meta())
    log.info("site built in %s (%d tenders, %d open)", args.out, len(tenders),
             sum(t.is_open(now) for t in tenders))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Weekly release assets: open tenders as CSV and ICS."""
    now = now_utc()
    open_items = store.open_tenders(_tenders(), now)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ao-formation-ouverts.csv").write_text(to_csv(open_items, now), encoding="utf-8")
    (out / "ao-formation-echeances.ics").write_text(to_ics(open_items, now, args.site_url), encoding="utf-8",
                                                    newline="")
    y, w, _ = now.astimezone(PARIS).date().isocalendar()
    week = f"{y}-W{w:02d}"
    start, end = iso_week_bounds(week)
    prev_start = (start - timedelta(days=7)).isoformat()
    new_last_week = sum(1 for t in open_items if prev_start <= t.published < start.isoformat())
    (out / "release-notes.md").write_text(
        f"Export hebdomadaire des appels d'offres formation ouverts au {now.astimezone(PARIS):%d/%m/%Y}.\n\n"
        f"- {len(open_items)} appels d'offres ouverts, dont {new_last_week} publiés la semaine précédente.\n"
        f"- `ao-formation-ouverts.csv` : tableau (séparateur `;`, UTF-8), s'ouvre dans Excel ou LibreOffice.\n"
        f"- `ao-formation-echeances.ics` : dates limites à importer dans votre agenda (rappel à J-3).\n\n"
        f"Site : {args.site_url}\n\n"
        "Sources : BOAMP (DILA, Licence Ouverte / Open Licence Etalab) et TED (Office des publications de l'UE, "
        "décision 2011/833/UE). Les avis officiels font foi.\n", encoding="utf-8")
    (out / "tag.txt").write_text(f"export-{week}\n", encoding="utf-8")
    log.info("exported %d open tenders for %s", len(open_items), week)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    now = now_utc()
    profile = Profile.from_dict(json.loads(Path(args.profile).read_text(encoding="utf-8")))
    items = select(_tenders(), profile, now, since=args.since)
    subject, text, html = render(items, profile, now, args.site_url, since=args.since)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest.txt").write_text(text, encoding="utf-8")
    (out / "digest.html").write_text(html, encoding="utf-8")
    print(subject)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="veille")
    p.add_argument("--site-url", default=os.environ.get("SITE_URL", DEFAULT_SITE_URL))
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="fetch BOAMP + TED and update data/notices.json")
    u.add_argument("--since", help="also (re)fetch notices published since YYYY-MM-DD (backfill)")
    b = sub.add_parser("build", help="generate the static site")
    b.add_argument("--out", default="_site")
    x = sub.add_parser("export", help="weekly CSV + ICS release assets")
    x.add_argument("--out", default="dist")
    d = sub.add_parser("digest", help="render a personalised digest (not sent)")
    d.add_argument("--profile", required=True)
    d.add_argument("--since")
    d.add_argument("--out", default="dist/digest")
    args = p.parse_args(argv)
    return {"update": cmd_update, "build": cmd_build, "export": cmd_export, "digest": cmd_digest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
