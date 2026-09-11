#!/usr/bin/env python3
"""
Калибровка скрапера сайта авиакомпании.

Запускать на своём компьютере, не в GitHub Actions:

    pip install playwright && playwright install chromium
    python calibrate.py pegasus

Откроется настоящий браузер. Сделайте в нём обычный поиск билетов руками —
выберите города, даты, нажмите «Найти». Утилита запишет все JSON-ответы сайта,
определит, в каком из них лежат цены, и напечатает готовый блок для config.yaml.

Зачем это нужно: внутренние эндпоинты Pegasus и AJet нигде не задокументированы
и время от времени меняются. Калибровка занимает минуту и избавляет от угадывания.
"""

import json
import sys
from pathlib import Path

from providers.airline_site import extract_offers_from_json

SITES = {
    "pegasus": {"start_url": "https://www.flypgs.com/en", "airline_code": "PC"},
    "ajet": {"start_url": "https://www.ajet.com/en", "airline_code": "VF"},
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SITES:
        sys.exit(f"Использование: python calibrate.py [{' | '.join(SITES)}]")

    name = sys.argv[1]
    site = SITES[name]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Нет playwright. Выполните:\n"
                 "  pip install playwright\n  playwright install chromium")

    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        page = context.new_page()

        def on_response(response):
            try:
                if "json" not in (response.headers.get("content-type") or "").lower():
                    return
                captured.append({"url": response.url, "body": response.json()})
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(site["start_url"])

        print("\n" + "=" * 70)
        print(f"Браузер открыт на {site['start_url']}")
        print("Сделайте обычный поиск билетов руками, дождитесь списка цен,")
        print("затем вернитесь сюда и нажмите Enter.")
        print("=" * 70 + "\n")
        input()

        final_url = page.url
        browser.close()

    print(f"Перехвачено JSON-ответов: {len(captured)}\n")

    hits = []
    for item in captured:
        offers = extract_offers_from_json(item["body"], name)
        if len(offers) >= 3:      # единичное совпадение — обычно случайное число
            prices = sorted(o.price for o in offers)
            hits.append((len(offers), item["url"], prices[:5]))

    if not hits:
        print("Цены ни в одном ответе не нашлись. Возможные причины:")
        print("  — поиск не был доведён до списка рейсов;")
        print("  — сайт отдаёт цены не в JSON, а внутри HTML.")
        Path(f"calibration-{name}-raw.json").write_text(
            json.dumps([c["url"] for c in captured], indent=2), encoding="utf-8")
        print(f"\nСписок всех перехваченных URL: calibration-{name}-raw.json")
        return 1

    hits.sort(reverse=True)
    print("Ответы, в которых нашлись цены (сверху — самый вероятный):\n")
    for count, url, sample in hits[:5]:
        print(f"  {count:>4} цен  {url[:110]}")
        print(f"           примеры: {sample}")

    best_url = hits[0][1]
    # Ищем устойчивый кусок пути, по которому потом опознавать нужный ответ.
    path = best_url.split("?")[0]
    marker = "/".join(path.split("/")[-2:])

    print("\n" + "=" * 70)
    print("Вставьте в config.yaml, в блок scrapers:\n")
    print(f"  {name}:")
    print(f"    airline_code: {site['airline_code']}")
    print(f"    url_template: \"{final_url}\"")
    print(f"    json_url_contains: \"{marker}\"")
    print(f"    wait_ms: 15000")
    print("\nВ url_template замените подставленные значения на плейсхолдеры:")
    print("  {origin} {destination} {departure_date} {return_date} {adults}")
    print("Даты в URL обычно в формате YYYY-MM-DD.")
    print("=" * 70)

    Path(f"calibration-{name}.json").write_text(
        json.dumps({"final_url": final_url,
                    "candidates": [{"url": u, "count": c} for c, u, _ in hits]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПодробности сохранены в calibration-{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
