"""Sales gate of the paid alerts (prepaid periods sold through octave-pay-gas, the Apps Script backend).

`sales.config.json` (repo root; SALES_CONFIG overrides the path for test builds) holds SALES_OPEN, the
catalog ids and prices (cents, the backend's Config.js is the authority and the release check compares
them), the VAT mode, the Apps Script /exec endpoint and the live publishable key. With SALES_OPEN=false the
build emits no buy UI at all. quotesEnabled (default false) adds the quote / bank-transfer form (checkout/devis.js)
to the order page; the backend has its own QUOTES_ENABLED flag. Same rules as the kit sites' scripts/checkout-pages.mjs loadSalesConfig().
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT_RE = re.compile(r"^https://script\.google\.com/macros/s/[A-Za-z0-9_-]{20,}/exec$")
TEST_ENDPOINT_RE = re.compile(r"^https?://[^\s\"'<>]+/exec$")
TERMS_VERSION = "cgv-pro-alertes-2026-10-13"
TERMS_DATE = "13 octobre 2026"
PRODUCTS = {  # id -> months
    "veille-ao-3m": 3,
    "veille-ao-12m": 12,
}


@dataclass(frozen=True)
class Sales:
    open: bool
    products: dict
    vat: str
    endpoint: str = ""
    publishable_key: str = ""
    quotes_enabled: bool = False

    def months(self, pid: str) -> int:
        return PRODUCTS[pid]

    def endpoint_origin(self) -> str:
        if not self.endpoint:
            return ""
        u = urlparse(self.endpoint)
        o = f"{u.scheme}://{u.netloc}"
        return "" if o == "https://script.google.com" else " " + o

    def checkout_csp(self) -> str:
        return ("default-src 'self'; script-src 'self' https://js.stripe.com https://*.js.stripe.com; "
                "frame-src https://js.stripe.com https://*.js.stripe.com https://hooks.stripe.com https://link.com https://*.link.com; "
                f"connect-src 'self' https://script.google.com https://script.googleusercontent.com{self.endpoint_origin()} "
                "https://api.stripe.com https://link.com https://*.link.com; img-src 'self' data: https://*.stripe.com https://*.link.com; "
                "style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")

    def order_csp(self) -> str:
        return ("default-src 'self'; script-src 'self'; "
                f"connect-src 'self' https://script.google.com https://script.googleusercontent.com{self.endpoint_origin()}; "
                "style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'none'")


def eur(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


def eur_short(cents: int) -> str:
    """'49 €' for whole euros (titles, buttons), else the full '49,50 €'."""
    return f"{cents // 100} €" if cents % 100 == 0 else eur(cents)


def schema_price(cents: int) -> str:
    """schema.org Offer.price: '49.00'."""
    return f"{cents // 100}.{cents % 100:02d}"


def load(path: str | os.PathLike | None = None) -> Sales:
    file = Path(path or os.environ.get("SALES_CONFIG") or ROOT / "sales.config.json")
    c = json.loads(file.read_text(encoding="utf-8"))

    def fail(msg: str):
        raise ValueError(f"{file}: {msg}")

    if not isinstance(c.get("SALES_OPEN"), bool):
        fail("SALES_OPEN must be true or false")
    if not isinstance(c.get("quotesEnabled", False), bool):
        fail("quotesEnabled must be true or false")
    if c.get("VAT") not in ("standard20", "franchise293B"):
        fail("VAT must be 'standard20' or 'franchise293B'")
    products = c.get("products")
    if not isinstance(products, dict) or set(products) != set(PRODUCTS):
        fail(f"products must list exactly {sorted(PRODUCTS)}")
    for pid, amount in products.items():
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            fail(f"{pid}: amount must be the price charged, in cents")
    endpoint, pk = c.get("endpoint") or "", c.get("publishableKey") or ""
    if c["SALES_OPEN"]:
        test = os.environ.get("SALES_ALLOW_TEST") == "1"
        if not ENDPOINT_RE.match(endpoint) and not (test and TEST_ENDPOINT_RE.match(endpoint)):
            fail("SALES_OPEN=true needs endpoint = https://script.google.com/macros/s/<deployment id>/exec")
        if not re.fullmatch(r"pk_live_[A-Za-z0-9]+", pk) and not (test and re.fullmatch(r"pk_test_[A-Za-z0-9]+", pk)):
            fail("SALES_OPEN=true needs a live publishable key (pk_live_…)")
    return Sales(open=c["SALES_OPEN"], products=dict(products), vat=c["VAT"], endpoint=endpoint, publishable_key=pk,
                 quotes_enabled=c.get("quotesEnabled", False))
