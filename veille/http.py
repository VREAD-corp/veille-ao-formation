"""Tiny HTTP helper (stdlib only) with retries."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

USER_AGENT = "veille-ao-formation/1.0 (+https://github.com/VREAD-corp/veille-ao-formation)"

# Signature used by fetchers so tests can inject a fake transport.
HttpJson = Callable[[str, dict | None, dict | None], Any]


def http_json(url: str, params: dict | None = None, body: dict | None = None,
              *, retries: int = 3, timeout: int = 60) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            last = exc
            if 400 <= exc.code < 500 and exc.code != 429:
                detail = exc.read().decode(errors="replace")[:500]
                raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"Request failed after {retries} attempts: {url}: {last}")
