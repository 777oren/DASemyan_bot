"""
Справочник аэропортов и городов.

Данные берутся из открытых файлов Travelpayouts (токен не нужен):
    https://api.travelpayouts.com/data/ru/airports.json
    https://api.travelpayouts.com/data/ru/cities.json

Файлы весят по несколько мегабайт, поэтому при первом обращении они
скачиваются, обрезаются до нужных полей и сохраняются в airports-db.json.
Дальше используется локальная копия — она коммитится в репозиторий вместе
с историей, так что повторных скачиваний в Actions не происходит.

Если скачать не удалось, бот не падает: он просто перестаёт подсказывать
названия и проверяет коды по формату.
"""

import json
from pathlib import Path

from .base import http_get_json, log

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "airports-db.json"
AIRPORTS_URL = "https://api.travelpayouts.com/data/{lang}/airports.json"
CITIES_URL = "https://api.travelpayouts.com/data/{lang}/cities.json"

_db = None


def _build(lang="ru"):
    """Скачивает справочники и оставляет только нужные поля."""
    log("скачиваю справочник аэропортов (один раз)…")
    entries = {}

    cities = http_get_json(CITIES_URL.format(lang=lang),
                           {"Accept-Encoding": "identity"}, attempts=2)
    for c in cities:
        code = (c.get("code") or "").upper()
        if len(code) != 3 or not c.get("has_flightable_airport", True):
            continue
        entries[code] = {
            "name": c.get("name") or code,
            "country": c.get("country_code") or "",
            "kind": "city",
        }

    airports = http_get_json(AIRPORTS_URL.format(lang=lang),
                             {"Accept-Encoding": "identity"}, attempts=2)
    for a in airports:
        code = (a.get("code") or "").upper()
        if len(code) != 3 or not a.get("flightable", True):
            continue
        if a.get("iata_type") not in (None, "airport"):
            continue
        if code in entries and entries[code]["kind"] == "city":
            continue        # код города важнее одноимённого аэропорта
        entries[code] = {
            "name": a.get("name") or code,
            "country": a.get("country_code") or "",
            "city": (a.get("city_code") or "").upper(),
            "kind": "airport",
        }

    log(f"справочник готов: {len(entries)} записей")
    return entries


def db():
    """Возвращает справочник или None, если он недоступен."""
    global _db
    if _db is not None:
        return _db or None

    if DB_PATH.exists():
        try:
            _db = json.loads(DB_PATH.read_text(encoding="utf-8"))
            return _db
        except json.JSONDecodeError:
            pass

    try:
        _db = _build()
        DB_PATH.write_text(json.dumps(_db, ensure_ascii=False, sort_keys=True),
                           encoding="utf-8")
    except Exception as e:
        log(f"не удалось загрузить справочник аэропортов: {e}")
        _db = {}
    return _db or None


def is_valid_code(code):
    """True — код существует. None — проверить не смогли (справочника нет)."""
    data = db()
    if data is None:
        return None
    return code.upper() in data


def describe(code):
    """«AMS — Амстердам (NL)» либо просто код, если справочника нет."""
    code = code.upper()
    data = db()
    if not data or code not in data:
        return code
    e = data[code]
    suffix = "город" if e["kind"] == "city" else "аэропорт"
    country = f", {e['country']}" if e.get("country") else ""
    return f"{code} — {e['name']} ({suffix}{country})"


def search(query, limit=8):
    """Поиск кода по названию города или аэропорта."""
    data = db()
    if not data:
        return []
    q = query.strip().lower()
    if not q:
        return []

    exact, starts, contains = [], [], []
    for code, e in data.items():
        name = e["name"].lower()
        if name == q:
            exact.append(code)
        elif name.startswith(q):
            starts.append(code)
        elif q in name:
            contains.append(code)

    # Города показываем раньше аэропортов — по коду города находится больше рейсов.
    def rank(code):
        return (0 if data[code]["kind"] == "city" else 1, data[code]["name"])

    result = sorted(exact, key=rank) + sorted(starts, key=rank) + sorted(contains, key=rank)
    return result[:limit]
