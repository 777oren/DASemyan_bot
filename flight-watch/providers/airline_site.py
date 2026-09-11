"""
Источник: сайт авиакомпании напрямую (Pegasus, AJet).

ВАЖНО, прочитайте перед включением:

Ни у Pegasus, ни у AJet нет публичного API. Этот модуль открывает их сайт
в настоящем браузере (Playwright), перехватывает ответы их внутренних
JSON-эндпоинтов и достаёт из них цены. Такой подход надёжнее CSS-селекторов:
вёрстку сайты меняют часто, а структуру внутренних ответов — редко.

Тем не менее это неофициальный доступ, и он может отвалиться:
  * у обеих авиакомпаний стоит антибот-защита, и IP-адреса GitHub Actions
    (диапазоны Azure) блокируются ею чаще всего;
  * URL поиска и формат ответов могут поменяться без предупреждения.

Поэтому источники выключены по умолчанию. Перед включением запустите
    python calibrate.py pegasus
у себя на компьютере — утилита откроет браузер, запишет, какой именно
эндпоинт отдаёт цены, и подставит параметры в config.yaml.
"""

import re
from datetime import datetime

from .base import Offer, ProviderError, log

NEEDS_BROWSER = True

PRICE_KEYS = ("price", "amount", "fare", "totalprice", "total", "value", "cost")
CURRENCY_KEYS = ("currency", "currencycode", "curr")
DATE_KEYS = ("date", "departuredate", "departure", "flightdate", "departuretime")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ------------------------------------------------------------ разбор JSON

def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        cleaned = re.sub(r"[^\d.]", "", cleaned)
        if cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "", cleaned.count(".") - 1)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def _pick(d, keys):
    """Ищет в словаре первое значение по ключу, похожему на один из keys."""
    for k, v in d.items():
        kl = k.lower().replace("_", "")
        if any(kl == key or kl.endswith(key) for key in keys):
            yield k, v


def extract_offers_from_json(payload, source, min_price=1, max_price=100000):
    """
    Рекурсивно обходит произвольный JSON и собирает объекты,
    в которых есть похожее на цену число. Чистая функция — тестируется офлайн.
    """
    found = []

    def walk(node, inherited_date=""):
        if isinstance(node, list):
            for item in node:
                walk(item, inherited_date)
            return
        if not isinstance(node, dict):
            return

        date = inherited_date
        for _, v in _pick(node, DATE_KEYS):
            if isinstance(v, str) and DATE_RE.search(v):
                date = v
                break

        price = None
        for _, v in _pick(node, PRICE_KEYS):
            if isinstance(v, dict):
                for _, inner in _pick(v, PRICE_KEYS):
                    price = _num(inner)
                    if price:
                        break
            else:
                price = _num(v)
            if price is not None and min_price <= price <= max_price:
                break
            price = None

        if price is not None:
            currency = ""
            for _, v in _pick(node, CURRENCY_KEYS):
                if isinstance(v, str) and 2 <= len(v) <= 4:
                    currency = v.upper()
                    break
            found.append(Offer(
                source=source,
                price=price,
                currency=currency,
                departure_at=date,
                transfers=0,
                raw=node if len(str(node)) < 2000 else {},
            ))

        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v, date)

    walk(payload)
    return found


def build_url(template, route):
    """Подставляет параметры маршрута в шаблон URL из config.yaml."""
    dep = route["departure_at"]
    ret = route.get("return_at") or ""
    if len(dep) == 7:  # YYYY-MM — сайты авиакомпаний требуют конкретный день
        dep = dep + "-01"
    if len(ret) == 7:
        ret = ret + "-01"
    return (template
            .replace("{origin}", route["origin"])
            .replace("{destination}", route["destination"])
            .replace("{departure_date}", dep)
            .replace("{return_date}", ret)
            .replace("{adults}", str(route.get("adults", 1))))


# ------------------------------------------------------------ браузер

def fetch(route, cfg, site_name):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ProviderError(
            "не установлен playwright. Выполните: "
            "pip install playwright && playwright install chromium")

    site = (cfg.get("scrapers") or {}).get(site_name) or {}
    template = site.get("url_template")
    if not template:
        raise ProviderError(
            f"для источника {site_name} не задан url_template в config.yaml. "
            f"Запустите: python calibrate.py {site_name}")

    url = build_url(template, route)
    match = (site.get("json_url_contains") or "").lower()
    wait_ms = int(site.get("wait_ms", 15000))
    airline_code = site.get("airline_code", "")

    captured = []
    offers = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        context = browser.new_context(
            locale=site.get("locale", "en-US"),
            timezone_id=site.get("timezone", "Europe/Istanbul"),
            user_agent=site.get("user_agent",
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/125.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        def on_response(response):
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    return
                if match and match not in response.url.lower():
                    return
                captured.append((response.url, response.json()))
            except Exception:
                pass

        page.on("response", on_response)

        try:
            log(f"  {site_name}: открываю {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            title = (page.title() or "").lower()
            if any(w in title for w in ("access denied", "attention required", "just a moment")):
                raise ProviderError("сайт показал страницу антибот-защиты")
        finally:
            browser.close()

    log(f"  {site_name}: перехвачено JSON-ответов: {len(captured)}")
    for _, payload in captured:
        offers.extend(extract_offers_from_json(
            payload, site_name,
            min_price=float(site.get("min_price", 10)),
            max_price=float(site.get("max_price", 100000)),
        ))

    if not offers:
        raise ProviderError(
            "цены не найдены. Либо сайт заблокировал запрос, либо изменил "
            f"формат — перекалибруйте: python calibrate.py {site_name}")

    for o in offers:
        o.airline = o.airline or airline_code
        o.currency = o.currency or cfg.get("currency", "eur")
        o.origin_airport = o.origin_airport or route["origin"]
        o.destination_airport = o.destination_airport or route["destination"]
        o.link = o.link or url

    log(f"  {site_name}: извлечено {len(offers)} цен")
    return offers
