"""
Обработка команд, присланных боту в Telegram.

GitHub Actions не может держать бота онлайн постоянно, поэтому команды
не обрабатываются мгновенно: при каждом запуске скрипт забирает накопившиеся
сообщения через getUpdates, применяет их и отвечает. Задержка — до одного
интервала между запусками workflow.
"""

import re
from datetime import date, datetime

import menu
import tg
from providers import PROVIDERS, airports
from providers.base import log

DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
CODE_RE = re.compile(r"^[A-Za-z]{3}$")

HELP = """<b>Команды</b>

/status — текущие настройки и последние цены
/check — проверить цены прямо сейчас

<b>Маршруты</b>
/routes — список отслеживаемых маршрутов
/add AMS SAW 2027-02-14 2027-02-28 — добавить маршрут
/add AMS SAW 2027-02-14 — в одну сторону
/del 2 — удалить маршрут номер 2
/find амстердам — найти код аэропорта по названию

В /add после дат можно дописать:
  PC,VF — следить только за этими авиакомпаниями
  direct — только прямые рейсы
  "Стамбул весной" — своё название маршрута

/drop 7 — уведомлять при падении цены на 7%
/max 350 — уведомлять при любой цене ниже 350
/max off — убрать абсолютный порог
/rise 15 — предупреждать о росте на 15%
/rise off — не предупреждать о росте

/freq 60 — проверять цены раз в 60 минут
/pause — приостановить слежение
/resume — возобновить

/source pegasus on — включить источник
/source ajet off — выключить источник

/help — эта справка

Настройки применяются при следующем запуске."""


def _fmt_settings(settings, cfg):
    cur = (cfg.get("currency") or "eur").upper()
    lines = ["<b>Настройки</b>"]
    lines.append(f"Падение для уведомления: {settings['drop_percent']}%")
    lines.append("Абсолютный порог: " +
                 (f"{settings['max_price']} {cur}" if settings["max_price"] is not None
                  else "не задан"))
    lines.append("Предупреждать о росте: " +
                 (f"{settings['rise_percent']}%" if settings["rise_percent"]
                  else "нет"))
    lines.append(f"Интервал проверки: {settings['check_interval_minutes']} мин")
    lines.append(f"Маршрутов: {len(all_routes(settings, cfg))} (/routes)")
    lines.append("Слежение: " + ("⏸ на паузе" if settings["paused"] else "▶️ активно"))

    lines.append("")
    lines.append("<b>Источники</b>")
    for name, meta in PROVIDERS.items():
        mark = "✅" if settings["sources"].get(name) else "⬜"
        lines.append(f"{mark} {name} — {meta['title']}")
    return "\n".join(lines)


def _fmt_prices(history, cfg):
    cur = (cfg.get("currency") or "eur").upper()
    routes = history.get("routes") or {}
    if not routes:
        return "\n\n<b>Цены</b>\nДанных пока нет."

    lines = ["", "<b>Последние цены</b>"]
    for key, state in routes.items():
        points = state.get("points") or []
        if not points:
            continue
        last = points[-1]
        best = state.get("best_price")
        name = state.get("name") or key
        line = f"{tg.escape(name)}: <b>{last['price']} {cur}</b>"
        if best is not None and best < last["price"]:
            line += f" (минимум {best})"
        if last.get("source"):
            line += f" · {last['source']}"
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------ маршруты

def all_routes(settings, cfg):
    """Маршруты из config.yaml (несъёмные) + добавленные ботом."""
    fixed = [dict(r, _locked=True) for r in (cfg.get("routes") or [])]
    added = [dict(r, _locked=False) for r in (settings.get("routes") or [])]
    return fixed + added


def route_title(route):
    if route.get("name"):
        return route["name"]
    arrow = f"{route['origin']} → {route['destination']}"
    dates = route["departure_at"]
    if route.get("return_at"):
        dates += f" — {route['return_at']}"
    return f"{arrow}, {dates}"



def _plural(n, one, few, many):
    if 11 <= n % 100 <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _fmt_brief(settings, cfg):
    """
    Компактная сводка состояния. Подклеивается к ответу на любую команду,
    которая что-то изменила, чтобы не приходилось спрашивать /status следом.
    """
    cur = (cfg.get("currency") or "eur").upper()
    n = len(all_routes(settings, cfg))

    drop = f"📉 {settings['drop_percent']:g}%"
    top = (f"🎯 {settings['max_price']:g} {cur}"
           if settings.get("max_price") is not None else "🎯 порога нет")
    rise = (f"📈 {settings['rise_percent']:g}%"
            if settings.get("rise_percent") else "📈 не следим")

    routes = f"📋 {n} " + _plural(n, "маршрут", "маршрута", "маршрутов")
    state = "⏸ пауза" if settings.get("paused") else "▶️ активно"
    interval = f"⏱ {settings['check_interval_minutes']} мин"

    on = [PROVIDERS[k]["title"] for k, v in (settings.get("sources") or {}).items()
          if v and k in PROVIDERS]
    sources = ", ".join(on) if on else "нет включённых"

    return ("———\n"
            f"{drop} · {top} · {rise}\n"
            f"{interval} · {routes} · {state}\n"
            f"Источники: {tg.escape(sources)}")


def _fmt_routes(settings, cfg):
    routes = all_routes(settings, cfg)
    if not routes:
        return ("Маршрутов пока нет.\n\n"
                "Добавьте: <code>/add AMS SAW 2027-02-14 2027-02-28</code>\n"
                "откуда · куда · туда · обратно\n\n"
                "Не знаете код аэропорта — отправьте <code>/find стамбул</code>")

    lines = ["<b>Отслеживаемые маршруты</b>", ""]
    for i, r in enumerate(routes, 1):
        lock = " 🔒" if r.get("_locked") else ""
        lines.append(f"<b>{i}.</b> {tg.escape(route_title(r))}{lock}")
        detail = [airports.describe(r["origin"]), "→", airports.describe(r["destination"])]
        lines.append("    " + tg.escape(" ".join(detail)))
        extra = []
        if r.get("airlines"):
            extra.append("только " + ", ".join(r["airlines"]))
        if r.get("direct"):
            extra.append("прямые")
        if r.get("one_way"):
            extra.append("в одну сторону")
        if extra:
            lines.append("    " + tg.escape("; ".join(extra)))
        lines.append("")

    if any(r.get("_locked") for r in routes):
        lines.append("🔒 — задан в config.yaml, из чата не удаляется")
    lines.append("Удалить: /del 2 · Добавить: /add AMS SAW 2027-02-14 2027-02-28")
    return "\n".join(lines)


def _check_date(value):
    """Проверяет формат и что дата ещё не прошла."""
    if not DATE_RE.match(value):
        return "нужен формат ГГГГ-ММ-ДД или ГГГГ-ММ"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d" if len(value) == 10 else "%Y-%m").date()
    except ValueError:
        return f"такой даты не существует: {value}"
    last_day = parsed if len(value) == 10 else parsed.replace(day=28)
    if last_day < date.today():
        return f"дата {value} уже прошла"
    return None


def parse_add(args, settings):
    """Разбирает аргументы /add. Возвращает (маршрут, текст_ошибки)."""
    if len(args) < 3:
        return None, ("Нужно минимум три значения: откуда, куда, дата вылета.\n\n"
                      "Пример: /add AMS SAW 2027-02-14 2027-02-28\n"
                      "Не знаете код — спросите: /find амстердам")

    origin, destination = args[0].upper(), args[1].upper()
    for code in (origin, destination):
        if not CODE_RE.match(code):
            return None, (f"«{tg.escape(code)}» не похож на IATA-код — нужны три буквы.\n"
                          "Найти код: /find название_города")
        valid = airports.is_valid_code(code)
        if valid is False:
            hints = airports.search(code)
            extra = ("\n\nВозможно, вы имели в виду:\n" +
                     "\n".join(airports.describe(c) for c in hints)) if hints else ""
            return None, f"Код {code} в справочнике не найден.{tg.escape(extra)}"
    if origin == destination:
        return None, "Аэропорт вылета и прилёта совпадают."

    rest = list(args[2:])
    dates = []
    while rest and DATE_RE.match(rest[0]) and len(dates) < 2:
        dates.append(rest.pop(0))
    if not dates:
        return None, "Не вижу даты вылета. Формат: 2027-02-14 или 2027-02"

    for d in dates:
        err = _check_date(d)
        if err:
            return None, err.capitalize()
    if len(dates) == 2 and dates[1] < dates[0]:
        return None, "Дата возврата раньше даты вылета."

    route = {
        "origin": origin,
        "destination": destination,
        "departure_at": dates[0],
        "one_way": len(dates) == 1,
    }
    if len(dates) == 2:
        route["return_at"] = dates[1]

    # Остаток: флаги, коды авиакомпаний, название в кавычках.
    name_parts = []
    for token in rest:
        low = token.lower()
        if low in ("direct", "прямой", "прямые"):
            route["direct"] = True
        elif low in ("oneway", "туда"):
            route["one_way"] = True
            route.pop("return_at", None)
        elif re.fullmatch(r"[A-Za-z0-9]{2}(,[A-Za-z0-9]{2})*", token):
            route["airlines"] = [a.upper() for a in token.split(",")]
        else:
            name_parts.append(token)

    if name_parts:
        route["name"] = " ".join(name_parts).strip('"«»')

    existing = all_routes(settings, {"routes": []})
    for r in settings.get("routes") or []:
        if (r["origin"] == origin and r["destination"] == destination
                and r["departure_at"] == route["departure_at"]
                and r.get("return_at") == route.get("return_at")):
            return None, "Такой маршрут уже отслеживается. /routes — список."
    del existing

    return route, None


def _parse_number(arg, allow_off=False):
    if allow_off and arg.lower() in ("off", "выкл", "нет", "0"):
        return None, True
    try:
        return float(arg.replace(",", ".")), True
    except ValueError:
        return None, False


def handle(text, settings, history, cfg):
    """Возвращает (ответ, настройки_изменились, запустить_проверку, клавиатура)."""
    # Нажатие кнопки приходит обычным текстом — переводим его в команду
    # либо показываем подменю.
    command, keyboard, canned = menu.resolve(text)
    if canned is not None:
        return canned, False, False, keyboard
    if command:
        text = command

    parts = text.strip().split()
    if not parts:
        return None, False, False, None

    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd in ("/start", "/help"):
        return HELP, False, False, menu.reply_keyboard(menu.MAIN)

    if cmd == "/status":
        return _fmt_settings(settings, cfg) + "\n" + _fmt_prices(history, cfg), False, False, None

    if cmd == "/check":
        return "Проверяю цены…", False, True, None

    if cmd == "/routes":
        return _fmt_routes(settings, cfg), False, False, None

    if cmd == "/find":
        if not args:
            return "Пример: /find амстердам", False, False, None
        hits = airports.search(" ".join(args))
        if not hits:
            data = airports.db()
            if data is None:
                return ("Справочник аэропортов недоступен — не удалось его скачать. "
                        "Код можно указать вручную, он проверяется по формату."), False, False, None
            return "Ничего не нашлось. Попробуйте другое написание.", False, False, None
        lines = ["<b>Найдено</b>", ""]
        lines += [tg.escape(airports.describe(c)) for c in hits]
        lines.append("")
        lines.append("Коды городов обычно дают больше вариантов, чем коды аэропортов.")
        return "\n".join(lines), False, False, None

    if cmd == "/add":
        route, error = parse_add(args, settings)
        if error:
            return error, False, False, None
        settings.setdefault("routes", []).append(route)
        reply = [f"✅ Маршрут добавлен: <b>{tg.escape(route_title(route))}</b>", ""]
        reply.append(tg.escape(airports.describe(route["origin"])))
        reply.append(tg.escape(airports.describe(route["destination"])))
        if route.get("airlines"):
            reply.append("Только авиакомпании: " + ", ".join(route["airlines"]))
        reply.append("")
        reply.append("Проверю его при следующем запуске. /check — прямо сейчас.")
        return "\n".join(reply), True, False, None

    if cmd in ("/del", "/delete", "/rm"):
        routes = all_routes(settings, cfg)
        if not args:
            return _fmt_routes(settings, cfg) + "\n\nУкажите номер: /del 2", False, False, None
        try:
            index = int(args[0])
        except ValueError:
            return "Нужен номер маршрута из /routes. Пример: /del 2", False, False, None
        if not 1 <= index <= len(routes):
            return f"Нет маршрута с номером {index}. Всего их {len(routes)}.", False, False, None

        target = routes[index - 1]
        if target.get("_locked"):
            return ("🔒 Этот маршрут задан в config.yaml, из чата его не удалить — "
                    "уберите его из файла в репозитории."), False, False, None

        added = settings.get("routes") or []
        position = index - 1 - (len(routes) - len(added))
        removed = added.pop(position)
        return f"🗑 Удалён: <b>{tg.escape(route_title(removed))}</b>", True, False, None

    if cmd == "/drop":
        if not args:
            return f"Сейчас: {settings['drop_percent']:g}%. Пример: /drop 7", False, False, None
        value, ok = _parse_number(args[0])
        if not ok or not 0 < value <= 100:
            return "Нужно число от 0 до 100. Пример: /drop 7", False, False, None
        settings["drop_percent"] = value
        return f"✅ Уведомлять при падении на {value:g}%", True, False, None

    if cmd == "/max":
        if not args:
            return "Пример: /max 350 или /max off", False, False, None
        value, ok = _parse_number(args[0], allow_off=True)
        if not ok:
            return "Нужно число или off. Пример: /max 350", False, False, None
        settings["max_price"] = value
        return ("✅ Абсолютный порог убран" if value is None
                else f"✅ Уведомлять при цене ниже {value:g}"), True, False, None

    if cmd == "/rise":
        if not args:
            return "Пример: /rise 15 или /rise off", False, False, None
        value, ok = _parse_number(args[0], allow_off=True)
        if not ok:
            return "Нужно число или off. Пример: /rise 15", False, False, None
        settings["rise_percent"] = value
        return ("✅ Предупреждения о росте выключены" if value is None
                else f"✅ Предупреждать о росте на {value:g}%"), True, False, None

    if cmd == "/freq":
        if not args:
            return (f"Сейчас: раз в {settings['check_interval_minutes']} мин. "
                    "Пример: /freq 180"), False, False, None
        value, ok = _parse_number(args[0])
        if not ok or value < 1:
            return "Нужно число минут. Пример: /freq 180", False, False, None
        settings["check_interval_minutes"] = int(value)
        note = ""
        if value < 30:
            note = ("\n\n⚠️ Реальная частота ограничена расписанием workflow "
                    "(по умолчанию раз в 30 минут). Чтобы проверять чаще, "
                    "поменяйте cron в .github/workflows/monitor.yml")
        return f"✅ Проверка раз в {int(value)} мин{note}", True, False, None

    if cmd == "/pause":
        settings["paused"] = True
        return "⏸ Слежение приостановлено. /resume — возобновить", True, False, None

    if cmd == "/resume":
        settings["paused"] = False
        return "▶️ Слежение возобновлено", True, False, None

    if cmd in ("/source", "/sources"):
        if len(args) < 2:
            return (_fmt_settings(settings, cfg) +
                    "\n\nПример: /source pegasus on"), False, False, None
        name = args[0].lower()
        if name not in PROVIDERS:
            return f"Неизвестный источник. Доступны: {', '.join(PROVIDERS)}", False, False, None
        on = args[1].lower() in ("on", "1", "true", "вкл", "да")
        settings["sources"][name] = on
        extra = ""
        if on and PROVIDERS[name]["needs_browser"]:
            extra = ("\n\n⚠️ Это скрапер сайта авиакомпании. Убедитесь, что "
                     f"в config.yaml заполнен блок scrapers.{name} — иначе "
                     "источник будет отваливаться с ошибкой.")
        return f"✅ Источник {name}: {'включён' if on else 'выключен'}{extra}", True, False, None

    return None, False, False, None


def poll(settings, history, cfg):
    """Забирает и обрабатывает накопившиеся команды. Возвращает (изменено, проверить_сейчас)."""
    meta = history.setdefault("meta", {})
    offset = meta.get("telegram_offset", 0)
    owner = tg.chat_id()

    try:
        updates = tg.get_updates(offset)
    except Exception as e:
        log(f"не удалось получить команды из Telegram: {e}")
        return False, False

    changed = force_check = False
    for upd in updates:
        meta["telegram_offset"] = upd["update_id"] + 1
        message = upd.get("message") or {}
        text = message.get("text") or ""
        sender = str((message.get("chat") or {}).get("id") or "")

        # Команды принимаем только от владельца.
        if owner and sender != owner:
            log(f"игнорирую сообщение от постороннего chat_id {sender}")
            continue
        if not text.strip():
            continue

        log(f"команда: {text}")

        # Одна сбойная команда не должна ронять весь запуск: проверка цен
        # и уведомления важнее, чем ответ на конкретное сообщение.
        try:
            reply, did_change, run_now, keyboard = handle(text, settings, history, cfg)
        except Exception as e:
            log(f"ошибка при обработке команды {text!r}: {e}")
            try:
                tg.send(f"⚠️ Не смог обработать «{tg.escape(text)}»: {tg.escape(e)}\n\n"
                        "Отправьте /help, чтобы увидеть список команд.",
                        menu.reply_keyboard(menu.MAIN))
            except Exception:
                pass
            continue

        changed = changed or did_change
        force_check = force_check or run_now

        # К ответу на команду, которая что-то поменяла, подклеиваем сводку,
        # чтобы состояние было видно сразу и не пришлось слать /status.
        if reply and (did_change or run_now):
            reply += "\n\n" + _fmt_brief(settings, cfg)

        if reply:
            try:
                tg.send(reply, keyboard)
            except Exception as e:
                log(f"не удалось ответить: {e}")

    return changed, force_check
