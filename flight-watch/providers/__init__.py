"""Реестр источников цен."""

from functools import partial

from . import airline_site, travelpayouts
from .base import Offer, ProviderError, log  # noqa: F401  (реэкспорт)

PROVIDERS = {
    "travelpayouts": {
        "fetch": travelpayouts.fetch,
        "needs_browser": False,
        "title": "Aviasales (кэш)",
    },
    "pegasus": {
        "fetch": partial(airline_site.fetch, site_name="pegasus"),
        "needs_browser": True,
        "title": "Pegasus Airlines",
    },
    "ajet": {
        "fetch": partial(airline_site.fetch, site_name="ajet"),
        "needs_browser": True,
        "title": "AJet",
    },
}


def get(name):
    if name not in PROVIDERS:
        raise ProviderError(f"неизвестный источник: {name}")
    return PROVIDERS[name]
