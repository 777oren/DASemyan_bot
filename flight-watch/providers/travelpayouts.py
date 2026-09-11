"""Источник: Travelpayouts (Aviasales) Data API. Кэш цен за последние 48 часов."""

import os
import urllib.parse

from .base import Offer, ProviderError, http_get_json, log

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
AVIASALES_BASE = "https://www.aviasales.com"

NAME = "travelpayouts"
NEEDS_BROWSER = False


def fetch(route, cfg):
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        raise ProviderError("не задана переменная TRAVELPAYOUTS_TOKEN")

    currency = cfg.get("currency", "eur")
    params = {
        "origin": route["origin"],
        "destination": route["destination"],
        "departure_at": route["departure_at"],
        "currency": currency,
        "market": cfg.get("market", "ru"),
        "sorting": "price",
        "direct": str(bool(route.get("direct", False))).lower(),
        "one_way": str(bool(route.get("one_way", False))).lower(),
        # Берём с запасом: фильтр по авиакомпаниям применяется уже локально,
        # поэтому нужно достаточно строк, чтобы нужный перевозчик в них попал.
        "limit": 200,
        "page": 1,
    }
    if route.get("return_at") and not route.get("one_way"):
        params["return_at"] = route["return_at"]

    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    data = http_get_json(url, {"X-Access-Token": token, "Accept-Encoding": "identity"})
    if not data.get("success"):
        raise ProviderError(f"API вернул ошибку: {data.get('error')}")

    offers = []
    for o in data.get("data") or []:
        offers.append(Offer(
            source=NAME,
            price=o["price"],
            currency=currency,
            airline=o.get("airline", ""),
            origin_airport=o.get("origin_airport", ""),
            destination_airport=o.get("destination_airport", ""),
            departure_at=o.get("departure_at", ""),
            return_at=o.get("return_at", ""),
            transfers=o.get("transfers"),
            duration=o.get("duration"),
            link=(AVIASALES_BASE + o["link"]) if o.get("link") else "",
            raw=o,
        ))
    log(f"  travelpayouts: получено {len(offers)} предложений")
    return offers
