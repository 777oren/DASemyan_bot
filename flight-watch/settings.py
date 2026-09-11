"""
Настройки, которые можно менять из Telegram.

config.yaml   — маршруты и стартовые значения, правится руками в репозитории.
settings.json — то, что бот меняет по команде. Имеет приоритет над config.yaml.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
SETTINGS_PATH = ROOT / "settings.json"

DEFAULTS = {
    "drop_percent": 5.0,        # уведомить при падении на столько %
    "max_price": None,          # абсолютный порог; None — выключен
    "rise_percent": 15.0,       # уведомить при росте на столько %; None — выключен
    "check_interval_minutes": 60,   # как часто реально проверять цены
    "paused": False,            # слежение приостановлено
    "sources": {                # какие источники опрашивать
        "travelpayouts": True,
        "pegasus": False,
        "ajet": False,
    },
    "routes": [],               # маршруты, добавленные через бота (/add)
}


def load(config=None):
    """Собирает итоговые настройки: DEFAULTS ← config.yaml ← settings.json."""
    settings = json.loads(json.dumps(DEFAULTS))

    if config:
        for key in ("drop_percent", "max_price", "rise_percent",
                    "check_interval_minutes", "paused"):
            if key in (config.get("alerts") or {}):
                settings[key] = config["alerts"][key]
            elif key in config:
                settings[key] = config[key]
        for name, enabled in (config.get("sources") or {}).items():
            settings["sources"][name] = bool(enabled)

    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            sources = saved.pop("sources", None)
            settings.update(saved)
            if sources:
                settings["sources"].update(sources)
        except json.JSONDecodeError:
            pass

    settings.setdefault("routes", [])
    return settings


def save(settings):
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
