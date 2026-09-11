#!/usr/bin/env python3
"""
Мониторинг цен на авиабилеты из нескольких источников с уведомлениями в Telegram.

Запуск:
    python monitor.py               обычный прогон (учитывает интервал и паузу)
    python monitor.py --force       проверить и уведомить, игнорируя пороги
    python monitor.py --dry-run     ничего не отправлять, всё в консоль
    python monitor.py --no-bot      не забирать команды из Telegram
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import bot
import settings as settings_module
import tg
from providers import PROVIDERS, get as get_provider
from providers.base import ProviderError, log

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
HISTORY_PATH = ROOT / "history.json"
MAX_POINTS_PER_ROUTE = 2000


# ---------------------------------------------------------------- конфиг

def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"Нет файла {CONFIG_PATH}")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg.setdefault("routes", [])
    for route in cfg["routes"]:
        for field in ("origin", "destination", "departure_at"):
            if not route.get(field):
                sys.exit(f"В маршруте {route.get('name', '?')} не задано поле {field}")
    return cfg


def route_key(route):
    return "{}-{}-{}-{}".format(route["origin"], route["destination"],
                                route["departure_at"],
                                route.get("return_at") or "oneway")


def load_history():
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("history.json повреждён, начинаю с нуля")
    return {"routes": {}, "meta": {}}


def save_history(history):
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")


# ---------------------------------------------------------------- фильтры

def matches(offer, route):
    """Подходит ли предложение под ограничения маршрута."""
    airlines = [a.upper() for a in (route.get("airlines") or [])]
    if airlines and (offer.airline or "").upper() not in airlines:
        return False

    exclude = [a.upper() for a in (route.get("exclude_airlines") or [])]
    if exclude and (offer.airline or "").upper() in exclude:
        return False

    max_transfers = route.get("max_transfers")
    if max_transfers is not None and offer.transfers is not None:
        transfers = max(offer.transfers, (offer.raw or {}).get("return_transfers") or 0)
        if transfers > max_transfers:
            return False

    max_duration = route.get("max_duration_minutes")
    if max_duration is not None and offer.duration and offer.duration > max_duration:
        return False

    return True


# ---------------------------------------------------------------- сообщения

def format_message(route, best, per_source, previous, best_ever, currency, reason):
    cur = currency.upper()
    title = route.get("name") or f"{route['origin']} → {route['destination']}"
    source_title = PROVIDERS.get(best.source, {}).get("title", best.source)

    lines = [reason, "", f"<b>{tg.escape(title)}</b>",
             f"💶 <b>{best.price:g} {cur}</b> · {tg.escape(source_title)}"]

    if previous is not None:
        delta = best.price - previous
        sign = "↓" if delta < 0 else "↑"
        pct = abs(delta) / previous * 100 if previous else 0
        lines.append(f"{sign} {abs(delta):g} {cur} ({pct:.1f}%) к прошлому уведомлению")
    if best_ever is not None:
        lines.append(f"Минимум за всё время: {best_ever:g} {cur}")

    lines.append("")
    airline = best.airline or "?"
    lines.append(f"✈️ {tg.escape(airline)} "
                 f"{tg.escape(best.origin_airport)}→{tg.escape(best.destination_airport)}")
    if best.departure_at:
        lines.append(f"Вылет: {tg.escape(best.departure_at[:16].replace('T', ' '))}")
    if best.return_at:
        lines.append(f"Обратно: {tg.escape(best.return_at[:16].replace('T', ' '))}")
    if best.transfers is not None:
        lines.append(f"Пересадок: {best.transfers}" +
                     (" (прямой)" if best.transfers == 0 else ""))

    others = {s: p for s, p in per_source.items() if s != best.source}
    if others:
        lines.append("")
        lines.append("Для сравнения: " + ", ".join(
            f"{PROVIDERS.get(s, {}).get('title', s)} {p:g}"
            for s, p in sorted(others.items())))

    if best.link:
        lines.append("")
        lines.append(f'<a href="{tg.escape(best.link)}">Открыть</a>')
    return "\n".join(lines)


def decide(price, state, cfg_settings, force):
    last = state.get("last_notified_price")
    if force:
        return True, "🔔 Текущая цена (ручной запрос)"
    if last is None:
        return True, "🆕 Слежение запущено, стартовая цена"

    max_price = cfg_settings.get("max_price")
    if max_price is not None and price <= max_price and price < last:
        return True, f"🎯 Цена ниже порога {max_price:g}"

    drop = cfg_settings.get("drop_percent") or 0
    if drop and price <= last * (1 - drop / 100):
        return True, f"📉 Цена упала более чем на {drop:g}%"

    rise = cfg_settings.get("rise_percent")
    if rise and price >= last * (1 + rise / 100):
        return True, f"📈 Цена выросла более чем на {rise:g}%"

    return False, ""


# ---------------------------------------------------------------- сбор цен

def collect_offers(route, cfg, enabled_sources):
    """Опрашивает все включённые источники. Возвращает (предложения, ошибки)."""
    offers, errors = [], []
    for name in enabled_sources:
        if route.get("sources") and name not in route["sources"]:
            continue
        try:
            provider = get_provider(name)
        except ProviderError as e:
            errors.append((name, str(e)))
            continue

        try:
            found = provider["fetch"](route, cfg)
        except ProviderError as e:
            log(f"  {name}: {e}")
            errors.append((name, str(e)))
            continue
        except Exception as e:
            log(f"  {name}: непредвиденная ошибка — {e}")
            errors.append((name, str(e)))
            continue

        suitable = [o for o in found if matches(o, route)]
        log(f"  {name}: подходит под фильтры {len(suitable)} из {len(found)}")
        offers.extend(suitable)
        time.sleep(1)
    return offers, errors


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-bot", action="store_true")
    args = parser.parse_args()

    tg.DRY_RUN = args.dry_run

    cfg = load_config()
    history = load_history()
    history.setdefault("meta", {})
    history.setdefault("routes", {})
    cfg_settings = settings_module.load(cfg)

    # 1. Команды из Telegram.
    #
    # Два пути. Если настроен вебхук, команда приходит в параметрах запуска
    # workflow — опрашивать getUpdates бессмысленно, Telegram при активном
    # вебхуке его отключает. Без вебхука работает обычный опрос.
    force_from_bot = False
    webhook_command = os.environ.get("TELEGRAM_COMMAND", "").strip()

    if not args.no_bot:
        if webhook_command:
            log("команда получена через вебхук")
            changed, force_from_bot = bot.handle_webhook_command(
                webhook_command, os.environ.get("TELEGRAM_COMMAND_CHAT"),
                cfg_settings, history, cfg)
        else:
            changed, force_from_bot = bot.poll(cfg_settings, history, cfg)
        if changed:
            settings_module.save(cfg_settings)
            log("настройки обновлены из Telegram")

    force = args.force or force_from_bot

    # 2. Пауза.
    if cfg_settings.get("paused") and not force:
        log("слежение на паузе (/resume — возобновить)")
        save_history(history)
        return 0

    # 3. Интервал: workflow запускается чаще, чем нужно проверять цены.
    interval = int(cfg_settings.get("check_interval_minutes") or 60)
    last_check = history["meta"].get("last_check_at")
    if last_check and not force:
        try:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_check)
            # Допуск в 90 секунд: cron GitHub плавает и без него каждый второй
            # запуск отсекался бы как «слишком рано».
            if elapsed < timedelta(minutes=interval) - timedelta(seconds=90):
                left = timedelta(minutes=interval) - elapsed
                log(f"пропускаю: интервал {interval} мин, "
                    f"следующая проверка через {int(left.total_seconds() // 60)} мин")
                save_history(history)
                return 0
        except ValueError:
            pass

    enabled = [n for n, on in (cfg_settings.get("sources") or {}).items() if on]
    if not enabled:
        log("все источники выключены")
        save_history(history)
        return 0
    log(f"источники: {', '.join(enabled)}")

    # Маршруты из config.yaml плюс добавленные через /add.
    routes = bot.all_routes(cfg_settings, cfg)
    if not routes:
        log("маршрутов нет — добавьте через /add в Telegram или в config.yaml")
        save_history(history)
        return 0

    currency = cfg.get("currency", "eur")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    failures = 0

    for route in routes:
        key = route_key(route)
        name = bot.route_title(route)
        state = history["routes"].setdefault(key, {"points": []})
        state["name"] = name
        log(f"Проверяю: {name}")

        offers, errors = collect_offers(route, cfg, enabled)
        failures += len(errors)

        if not offers:
            log("  подходящих предложений не найдено")
            continue

        best = min(offers, key=lambda o: o.price)
        per_source = {}
        for o in offers:
            if o.source not in per_source or o.price < per_source[o.source]:
                per_source[o.source] = o.price

        best_ever = state.get("best_price")
        log(f"  лучшая цена: {best.price:g} {currency.upper()} "
            f"({best.source}, {best.airline or '?'}), минимум за всё время: {best_ever}")

        point = {"ts": now, "price": best.price, "source": best.source,
                 "airline": best.airline, "departure_at": best.departure_at,
                 "transfers": best.transfers, "per_source": per_source}
        state["points"] = (state.get("points", []) + [point])[-MAX_POINTS_PER_ROUTE:]
        if best_ever is None or best.price < best_ever:
            state["best_price"] = best.price

        should_notify, reason = decide(best.price, state, cfg_settings, force)
        if should_notify:
            msg = format_message(route, best, per_source,
                                 state.get("last_notified_price"), best_ever,
                                 currency, reason)
            try:
                if tg.send(msg) and not args.dry_run:
                    state["last_notified_price"] = best.price
                    state["last_notified_at"] = now
                log("  уведомление отправлено")
            except Exception as e:
                log(f"  не удалось отправить в Telegram: {e}")
                failures += 1
        else:
            log("  существенных изменений нет")

    history["meta"]["last_check_at"] = now
    save_history(history)
    log("Готово")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
