"""CSV, ICS, JSON, Atom and RSS exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Iterable
from xml.sax.saxutils import escape

from .model import CPV_LABELS, PARIS, Notice

ATTRIBUTION = (
    "Sources : BOAMP (DILA, Licence Ouverte / Open Licence Etalab) et TED "
    "(Office des publications de l'UE, réutilisation libre, décision 2011/833/UE). "
    "Données sélectionnées et normalisées par Veille AO Formation."
)

CSV_COLUMNS = [
    "id", "source", "titre", "acheteur", "date_publication", "date_limite", "jours_restants",
    "regions", "departements", "cpv", "cpv_libelle", "procedure", "valeur_estimee_eur",
    "statut", "critere_selection", "url_avis", "autres_liens",
]


def to_csv(items: Iterable[Notice], now: datetime) -> str:
    """Semicolon-separated UTF-8 with BOM (opens cleanly in French Excel)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for t in items:
        w.writerow([
            t.id, t.source, t.title, t.buyer, t.published, (t.deadline or "").replace("T", " "),
            "" if t.days_left(now) is None else t.days_left(now),
            ", ".join(t.regions), ", ".join(t.departments), " ".join(t.cpv), t.cpv_main_label(),
            t.procedure, "" if t.estimated_value is None else f"{t.estimated_value:.0f}",
            t.status, ", ".join(t.match), t.url, " ".join(t.alt_urls.values()),
        ])
    return "\ufeff" + buf.getvalue()


# --- ICS ----------------------------------------------------------------------


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Fold content lines at 75 octets (RFC 5545 §3.1)."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 75:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_ics(items: Iterable[Notice], now: datetime, site_url: str) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Veille AO Formation//veille-ao-formation//FR",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Appels d'offres formation – dates limites",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    stamp = _utc(now)
    for t in items:
        dl = t.deadline_dt()
        if not dl:
            continue
        desc = (f"Acheteur : {t.buyer}\nRégion(s) : {', '.join(t.regions) or 'non précisée'}\n"
                f"Avis officiel : {t.url}\nDate limite telle qu'indiquée dans l'avis (heure de Paris). "
                f"Vérifiez toujours l'avis officiel et le DCE.\n{ATTRIBUTION}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{t.id.replace(':', '-')}@veille-ao-formation",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(dl - timedelta(hours=1))}",
            f"DTEND:{_utc(dl)}",
            f"SUMMARY:{_ics_escape('Date limite AO : ' + t.title[:150])}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            f"URL:{t.url}",
            "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P3D",
            f"DESCRIPTION:{_ics_escape('J-3 : ' + t.title[:100])}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# --- JSON ---------------------------------------------------------------------


def to_json(items: Iterable[Notice], now: datetime, site_url: str) -> str:
    rows = []
    for t in items:
        d = t.to_dict()
        if not t.summary:
            d.pop("summary", None)
        d["cpv_libelle"] = t.cpv_main_label()
        d["jours_restants"] = t.days_left(now)
        rows.append(d)
    return json.dumps({
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "site": site_url,
        "licence": ATTRIBUTION,
        "count": len(rows),
        "tenders": rows,
    }, ensure_ascii=False, indent=1) + "\n"


# --- Atom & RSS -----------------------------------------------------------------


def _entry_html(t: Notice) -> str:
    parts = [f"<p><strong>Acheteur :</strong> {escape(t.buyer)}</p>"]
    if t.deadline:
        parts.append(f"<p><strong>Date limite :</strong> {escape(fmt_deadline(t.deadline))}</p>")
    parts.append(f"<p><strong>Région(s) :</strong> {escape(', '.join(t.regions) or 'non précisée')}</p>")
    if t.cpv:
        parts.append(f"<p><strong>CPV :</strong> {escape(' '.join(t.cpv[:4]))} {escape(t.cpv_main_label())}</p>")
    if t.description:
        parts.append(f"<p>{escape(t.description)}</p>")
    parts.append(f'<p><a href="{escape(t.url)}">Avis officiel ({escape(t.source)})</a></p>')
    return "".join(parts)


def _pub_dt(t: Notice) -> datetime:
    return datetime.fromisoformat(t.published).replace(hour=6, tzinfo=PARIS)


def to_atom(items: list[Notice], now: datetime, site_url: str) -> str:
    updated = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="fr">',
        "<title>Appels d'offres formation ouverts – Veille AO Formation</title>",
        f'<link rel="alternate" type="text/html" href="{escape(site_url)}"/>',
        f'<link rel="self" type="application/atom+xml" href="{escape(site_url)}feed.xml"/>',
        f"<id>{escape(site_url)}</id>",
        f"<updated>{updated}</updated>",
        "<author><name>Veille AO Formation (Jalonel)</name></author>",
        f"<rights>{escape(ATTRIBUTION)}</rights>",
    ]
    for t in items:
        upd = _pub_dt(t) if not t.updated else datetime.fromisoformat(t.updated).replace(hour=6, tzinfo=PARIS)
        out += [
            "<entry>",
            f"<title>{escape(t.title)}</title>",
            f'<link rel="alternate" href="{escape(t.url)}"/>',
            f"<id>urn:veille-ao-formation:{escape(t.id)}</id>",
            f"<published>{_pub_dt(t).isoformat()}</published>",
            f"<updated>{upd.isoformat()}</updated>",
            *(f'<category term="{escape(r)}"/>' for r in t.regions),
            f'<content type="html">{escape(_entry_html(t))}</content>',
            "</entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def to_rss(items: list[Notice], now: datetime, site_url: str) -> str:
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "<title>Appels d'offres formation ouverts – Veille AO Formation</title>",
        f"<link>{escape(site_url)}</link>",
        f'<atom:link href="{escape(site_url)}rss.xml" rel="self" type="application/rss+xml"/>',
        "<description>Appels d'offres publics français ouverts pour des prestations de formation "
        "(BOAMP + TED), mis à jour deux fois par jour.</description>",
        "<language>fr-fr</language>",
        f"<lastBuildDate>{format_datetime(now.astimezone(timezone.utc))}</lastBuildDate>",
        f"<copyright>{escape(ATTRIBUTION)}</copyright>",
    ]
    for t in items:
        out += [
            "<item>",
            f"<title>{escape(t.title)}</title>",
            f"<link>{escape(t.url)}</link>",
            f'<guid isPermaLink="false">urn:veille-ao-formation:{escape(t.id)}</guid>',
            f"<pubDate>{format_datetime(_pub_dt(t))}</pubDate>",
            *(f"<category>{escape(r)}</category>" for r in t.regions),
            f"<description>{escape(_entry_html(t))}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>"]
    return "\n".join(out) + "\n"


MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fmt_date(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def fmt_deadline(deadline: str) -> str:
    """'2026-11-03T12:00' -> '3 nov. 2026 à 12h00'."""
    date_part = fmt_date(deadline)
    if "T" in deadline and deadline[11:16] != "23:59":
        return f"{date_part} à {deadline[11:13]}h{deadline[14:16]}"
    return date_part


__all__ = ["to_csv", "to_ics", "to_json", "to_atom", "to_rss", "fmt_date", "fmt_deadline",
           "ATTRIBUTION", "CPV_LABELS"]
