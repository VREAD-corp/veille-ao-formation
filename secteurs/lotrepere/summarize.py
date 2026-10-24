"""Pluggable summaries — OFF by default.

No LLM is used today (no dedicated API key yet). The site shows the buyer's own
description excerpt instead. To enable summaries later, implement a class with
a `summarize(notice) -> str` method, register it in `SUMMARIZERS`, and set the
environment variable LOTREPERE_SUMMARIZER to its name in the workflow (with its
key stored as a GitHub Actions secret, never in the repo). Summaries are stored
in `Notice.summary` and are shown, labelled as automatic, next to the official
text.
"""

from __future__ import annotations

import os
from typing import Protocol

from .model import Notice


class Summarizer(Protocol):
    name: str

    def summarize(self, notice: Notice) -> str: ...


class NoSummary:
    """Default: no generated text at all."""

    name = "none"

    def summarize(self, notice: Notice) -> str:
        return ""


SUMMARIZERS: dict[str, type] = {"none": NoSummary}


def get_summarizer(name: str | None = None) -> Summarizer:
    name = (name or os.environ.get("LOTREPERE_SUMMARIZER") or "none").strip().lower()
    if name not in SUMMARIZERS:
        raise ValueError(f"Unknown summarizer {name!r}; available: {sorted(SUMMARIZERS)}")
    return SUMMARIZERS[name]()


def apply(notices: list[Notice], summarizer: Summarizer) -> int:
    """Fill missing summaries; return how many were written."""
    if summarizer.name == "none":
        return 0
    done = 0
    for n in notices:
        if not n.summary:
            n.summary = summarizer.summarize(n).strip()
            done += bool(n.summary)
    return done
