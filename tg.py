"""Минимальная обёртка над Telegram Bot API (без зависимостей)."""

import json
import os
import urllib.parse
import urllib.request

from providers.base import log

API = "https://api.telegram.org/bot{token}/{method}"
DRY_RUN = False


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def chat_id():
    # .strip() не для красоты: при копировании chat_id в секрет GitHub
    # в конец часто попадает пробел или перенос строки, и тогда сравнение
    # с отправителем не совпадает, а команды молча игнорируются.
    return str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


def configured():
    return bool(_token() and chat_id())


def _call(method, params, timeout=40):
    url = API.format(token=_token(), method=method)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def escape(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(text, keyboard=None):
    if DRY_RUN:
        mark = " + клавиатура" if keyboard else ""
        print(f"\n--- TELEGRAM (dry-run){mark} ---\n" + text + "\n--------------------------\n")
        return True
    if not configured():
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — отправка пропущена")
        return False
    params = {
        "chat_id": chat_id(),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        params["reply_markup"] = json.dumps(keyboard)
    result = _call("sendMessage", params)
    if not result.get("ok"):
        log(f"Telegram вернул ошибку: {result}")
        return False
    return True


def get_updates(offset=0):
    """Забирает сообщения, пришедшие боту с прошлого запуска."""
    if DRY_RUN or not configured():
        return []
    result = _call("getUpdates", {
        "offset": offset,
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    })
    if not result.get("ok"):
        log(f"getUpdates вернул ошибку: {result}")
        return []
    return result.get("result", [])
