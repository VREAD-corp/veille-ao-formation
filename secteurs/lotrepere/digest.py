"""Personalised digest: the selection rules and the sample e-mail of the paid alerts.

The paid alerts themselves are sent by the octave-pay-gas backend (Apps Script, VREAD-corp/octave-checkout
gas/src/Alerts.js), which applies the same rules to the public data/ouverts.json every morning. Keep both in
step: niches (at least one in common), regions + national notices (>= 10 regions or none), keywords on
title/description/buyer/lots without accents, open notices only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Iterable

from .exporters import ATTRIBUTION, fmt_date, fmt_deadline
from .model import Notice, fold


@dataclass
class Profile:
    """What a subscriber wants to receive."""

    name: str = "Exemple"
    niches: list[str] = field(default_factory=list)  # niche slugs; [] = all three
    regions: list[str] = field(default_factory=list)  # [] = all; national notices always kept
    keywords: list[str] = field(default_factory=list)  # any of them, accent-insensitive
    exclude_keywords: list[str] = field(default_factory=list)
    cpv_prefixes: list[str] = field(default_factory=list)  # e.g. ["90919", "77340"] (not offered in the paid alerts)
    min_days_left: int = 0  # skip tenders closing too soon to answer
    include_national: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


NATIONAL_THRESHOLD = 10  # a notice covering >= 10 regions is treated as national


def is_national(t: Notice) -> bool:
    return not t.regions or len(t.regions) >= NATIONAL_THRESHOLD


def matches(t: Notice, p: Profile, now: datetime) -> bool:
    if p.niches and not set(p.niches) & set(t.niches):
        return False
    if p.regions:
        in_region = bool(set(p.regions) & set(t.regions))
        if not in_region and not (p.include_national and is_national(t)):
            return False
    hay = fold(f"{t.title} {t.description} {t.buyer} {' '.join(t.lots)}")
    if p.keywords and not any(fold(k) in hay for k in p.keywords):
        return False
    if any(fold(k) in hay for k in p.exclude_keywords):
        return False
    if p.cpv_prefixes and not any(c.startswith(tuple(p.cpv_prefixes)) for c in t.cpv):
        return False
    days = t.days_left(now)
    if days is not None and days < p.min_days_left:
        return False
    return True


def select(items: Iterable[Notice], p: Profile, now: datetime, since: str | None = None) -> list[Notice]:
    """Open tenders matching the profile, first published on/after `since` (YYYY-MM-DD)."""
    res = [t for t in items if t.is_open(now) and matches(t, p, now)
           and (since is None or t.published >= since)]
    res.sort(key=lambda t: (t.deadline or "", t.id))
    return res


def render(items: list[Notice], p: Profile, now: datetime, site_url: str,
           since: str | None = None) -> tuple[str, str, str]:
    """Return (subject, plain text, HTML) for one digest."""
    period = f"depuis le {fmt_date(since)}" if since else "ouverts"
    n = len(items)
    subject = f"{n} appel{'s' if n > 1 else ''} d'offres {period} – {p.name}"
    text = [subject, ""]
    rows = []
    for t in items:
        dl = fmt_deadline(t.deadline) if t.deadline else "non précisée"
        text += [f"- {t.title}", f"  {t.buyer} · {', '.join(t.regions) or 'national / non précisé'}",
                 f"  Date limite : {dl} · {t.url}", ""]
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #e3e7ea">'
            f'<a href="{escape(t.url)}" style="color:#1f4e3d;font-weight:600">{escape(t.title)}</a><br>'
            f'<span style="color:#44525c">{escape(t.buyer)} · {escape(", ".join(t.regions) or "national / non précisé")}</span><br>'
            f'<strong>Date limite : {escape(dl)}</strong></td></tr>')
    if not items:
        text.append("Aucun nouvel appel d'offres ne correspond à vos critères sur la période.")
        rows.append("<tr><td>Aucun nouvel appel d'offres ne correspond à vos critères sur la période.</td></tr>")
    foot = (f"Vérifiez toujours l'avis officiel et le dossier de consultation. {ATTRIBUTION} "
            f"Toutes les annonces : {site_url}")
    text += ["--", foot]
    html = (
        '<!doctype html><html lang="fr"><meta charset="utf-8">'
        f"<title>{escape(subject)}</title>"
        '<body style="font-family:system-ui,sans-serif;max-width:640px;margin:auto;padding:16px;color:#14212b">'
        f'<h1 style="font-size:20px">{escape(subject)}</h1>'
        f'<table style="width:100%;border-collapse:collapse">{"".join(rows)}</table>'
        f'<p style="font-size:12px;color:#44525c;margin-top:24px">{escape(foot)}</p></body></html>'
    )
    return subject, "\n".join(text) + "\n", html
