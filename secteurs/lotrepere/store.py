"""Persistent notice store (data/notices.json) and de-duplication."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .model import NICHE_SLUGS, Notice, classify_niches, descriptor_only_false_positive

SOURCE_PRIORITY = {"BOAMP": 0, "TED": 1}


def load(path: Path) -> dict[str, Notice]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {d["id"]: Notice.from_dict(d) for d in raw.get("notices", [])}


def save(path: Path, store: dict[str, Notice]) -> bool:
    """Write the store; return True if the file content changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    notices = sorted(store.values(), key=lambda n: (n.published, n.id))
    text = json.dumps({"notices": [n.to_dict() for n in notices]}, ensure_ascii=False, indent=1) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def merge(store: dict[str, Notice], fresh: Iterable[Notice]) -> int:
    """Insert or replace notices by id; return how many ids were new."""
    added = 0
    for n in fresh:
        if n.id not in store:
            added += 1
        store[n.id] = n
    return added


def tenders(store: dict[str, Notice]) -> list[Notice]:
    """One record per procedure.

    Notices of the same procedure (BOAMP initial + rectificatif, the TED copy of
    a BOAMP notice) share a procedure id (eForms ContractFolderID). Older BOAMP
    formats link a rectificatif to the notice it replaces via `supersedes`.
    The canonical record is the latest notice, BOAMP preferred; it carries the
    first publication date and links to the other copies.
    """
    superseded = {sid for n in store.values() for sid in n.supersedes}
    groups: dict[str, list[Notice]] = defaultdict(list)
    for n in store.values():
        groups[n.group_key].append(n)

    out: list[Notice] = []
    for members in groups.values():
        live = [m for m in members if m.id not in superseded] or members
        live.sort(key=lambda m: (m.published, -SOURCE_PRIORITY.get(m.source, 9)), reverse=True)
        latest = live[0]
        boamp = [m for m in live if m.source == "BOAMP"]
        canon = copy.deepcopy(boamp[0] if boamp else latest)
        canon.updated = max(m.published for m in members)
        canon.published = min(m.published for m in members)
        if latest.status == "annule":
            canon.status = "annule"
        dated = [m for m in live if m.deadline]
        if dated:
            canon.deadline = max(dated, key=lambda m: m.published).deadline
        if not canon.regions:
            canon.regions = sorted({r for m in members for r in m.regions})
        if not canon.estimated_value:
            canon.estimated_value = next((m.estimated_value for m in live if m.estimated_value), None)
        canon.match = sorted({r for m in members for r in m.match})
        canon.cpv = list(dict.fromkeys(c for m in members for c in m.cpv))
        # Re-apply the current niche rules to the merged record, so a rule tightened after a notice was stored
        # takes effect without a refetch. Notices stored without descriptors keep the niches found at fetch time.
        if all(m.source == "TED" or m.descriptors for m in members):
            canon.descriptors = sorted({d for m in members for d in m.descriptors})
            found = classify_niches(title=canon.title, extra_text=" ".join(canon.lots), cpv=canon.cpv,
                                    descriptors=canon.descriptors, contract_types=canon.contract_types)
            canon.niches = [k for k in NICHE_SLUGS if k in found]
        else:
            canon.niches = [k for k in NICHE_SLUGS if any(k in m.niches for m in members)]
        for m in sorted(members, key=lambda m: m.published):
            if m.url != canon.url:
                canon.alt_urls[f"{m.source} {m.source_id}"] = m.url
        out.append(canon)
    # Superseded notices without a procedure id may sit in their own group. Notices stored before a
    # relevance rule was tightened are dropped here too (the store keeps them; the site never shows them).
    return [t for t in out if t.id not in superseded and t.niches
            and not descriptor_only_false_positive(t.match, t.cpv)]


def open_tenders(items: Iterable[Notice], now: datetime) -> list[Notice]:
    res = [t for t in items if t.is_open(now)]
    res.sort(key=lambda t: (t.deadline or "", t.id))
    return res
