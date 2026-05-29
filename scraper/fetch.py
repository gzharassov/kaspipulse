"""
KaspiPulse — fetch.py
Вежливый сбор СЫРЫХ данных по нише с витрины Kaspi.kz.

Эндпоинт подтверждён через DevTools:
  https://kaspi.kz/yml/product-view/pl/results
Товары приходят JSON-ом по пути: response["data"]["data"] -> список карточек.

Если сбор перестал работать — открой DevTools (F12) -> Network -> Fetch/XHR,
перелистни страницу категории, найди запрос "results?page=..." и сверь параметры.

Запуск:
    python -m scraper.fetch --test     # 2 страницы
    python -m scraper.fetch            # полный сбор по config.MAX_PAGES
"""

import argparse
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

from . import config

KASPI_RESULTS_URL = "https://kaspi.kz/yml/product-view/pl/results"


def _headers():
    """Заголовки обычного браузера. Без обхода авторизации/капчи."""
    return {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://kaspi.kz/shop/c/{config.CATEGORY_CODE.lower()}/?c={config.CITY_ID}",
        "X-KS-City": config.CITY_ID,
    }


def _build_q():
    """Собрать параметр q в формате Kaspi: :category:<код>:availableInZones:<зона>."""
    return f":category:{config.CATEGORY_CODE}:availableInZones:{config.AVAILABLE_ZONE}"


def _polite_sleep():
    lo, hi = config.REQUEST_DELAY_SEC
    time.sleep(random.uniform(lo, hi))


def _request_with_retries(params):
    last_err = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(
                KASPI_RESULTS_URL,
                headers=_headers(),
                params=params,
                timeout=config.TIMEOUT_SEC,
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            last_err = err
            wait = config.RETRY_BACKOFF_SEC * attempt
            print(f"  [retry {attempt}/{config.MAX_RETRIES}] {err} -> ждём {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Не удалось получить страницу после ретраев: {last_err}")


def fetch_page(page_index):
    """Получить одну страницу выдачи (JSON). page_index с 0."""
    params = {
        "page": page_index,
        "q": _build_q(),
        "sort": config.SORT,
        "ui": "d",
        "i": "-1",
        "c": config.CITY_ID,
        "requestId": uuid.uuid4().hex,
    }
    return _request_with_retries(params)


def _extract_cards(page_json):
    """
    Достать список карточек. Подтверждённый путь: data -> data -> [ ... ].
    Оставлены запасные варианты на случай изменения структуры.
    """
    if not isinstance(page_json, dict):
        return []
    node = page_json.get("data")
    # Основной случай: {"data": {"data": [...]}}
    if isinstance(node, dict) and isinstance(node.get("data"), list):
        return node["data"]
    # Запасные варианты:
    if isinstance(node, list):
        return node
    for key in ("products", "cards", "items"):
        if isinstance(page_json.get(key), list):
            return page_json[key]
    return []


def fetch_all(max_pages):
    pages = []
    for page in range(max_pages):
        print(f"[fetch] страница {page + 1}/{max_pages} ...")
        data = fetch_page(page)
        pages.append(data)
        cards = _extract_cards(data)
        print(f"  карточек на странице: {len(cards)}")
        if not cards:
            print("  пусто — останавливаюсь")
            break
        _polite_sleep()
    return pages


def save_raw(pages):
    os.makedirs(config.RAW_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(config.RAW_DIR, f"raw_{config.NICHE_SLUG}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False)
    print(f"[fetch] сырьё сохранено: {path} ({len(pages)} страниц)")
    return path


def main():
    parser = argparse.ArgumentParser(description="KaspiPulse fetcher")
    parser.add_argument("--test", action="store_true", help="тест: только 2 страницы")
    args = parser.parse_args()

    max_pages = 2 if args.test else config.MAX_PAGES
    print(f"=== KaspiPulse fetch: ниша '{config.NICHE_NAME}', "
          f"q='{_build_q()}', город {config.CITY_ID}, до {max_pages} страниц ===")

    pages = fetch_all(max_pages)
    total = sum(len(_extract_cards(p)) for p in pages)
    print(f"[fetch] собрано карточек (сырьём): {total}")
    save_raw(pages)


if __name__ == "__main__":
    main()
