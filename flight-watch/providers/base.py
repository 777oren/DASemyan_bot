"""Общие типы и утилиты для источников цен."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


@dataclass
class Offer:
    """Нормализованное предложение — одинаковое для всех источников."""
    source: str
    price: float
    currency: str
    airline: str = ""
    origin_airport: str = ""
    destination_airport: str = ""
    departure_at: str = ""
    return_at: str = ""
    transfers: Optional[int] = None
    duration: Optional[int] = None
    link: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self):
        d = asdict(self)
        d.pop("raw", None)
        return d


class ProviderError(Exception):
    pass


def http_get_json(url, headers=None, attempts=3, timeout=30):
    """GET с повторами на 429 и 5xx."""
    headers = headers or {}
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            log(f"  HTTP {e.code}: {body}")
            if not (e.code == 429 or 500 <= e.code < 600) or attempt == attempts:
                raise ProviderError(f"HTTP {e.code}") from e
        except urllib.error.URLError as e:
            log(f"  сетевая ошибка: {e.reason}")
            if attempt == attempts:
                raise ProviderError(str(e.reason)) from e
        time.sleep(5 * attempt)
