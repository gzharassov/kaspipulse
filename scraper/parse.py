"""
KaspiPulse — parse.py
Превращает сырой JSON (из fetch.py) в структурированную таблицу товаров
и сохраняет недельный срез в data/weekly/YYYY-MM-DD.csv.gz.

Имена полей ПОДТВЕРЖДЕНЫ через DevTools Preview на реальной карточке Kaspi:
    id              -> product_id   (устойчивый ID для матчинга между неделями)
    title           -> name
    brand           -> brand
    unitPrice       -> price        (₸; есть ещё unitSalePrice = цена со скидкой)
    rating          -> rating       (0..5)
    reviewsQuantity -> reviews_count  <- ключ к review-velocity (оценка продаж)
    stock           -> stock        <- бонус: дельта остатков = Метод 2 оценки продаж
    bestMerchant    -> best_merchant
    shopLink        -> url          (относительная ссылка, достраиваем до полной)
    categoryId      -> category_id

Запуск:
    python -m scraper.parse                         # последний файл из data/raw/
    python -m scraper.parse data/raw/raw_home_2026-06-01.json
"""

import glob
import gzip
import os
import sys
import csv
import json
from datetime import datetime, timezone

from . import config
from .fetch import _extract_cards


def _safe(card, *keys, default=None):
    """Достать значение по первому из возможных ключей."""
    for k in keys:
        if isinstance(card, dict) and card.get(k) not in (None, ""):
            return card[k]
    return default


def _to_number(val):
    """Аккуратно привести к числу (Kaspi иногда отдаёт строки с пробелами)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    try:
        return float(str(val).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return None


def parse_card(card, position):
    """Распарсить одну сырую карточку Kaspi в плоскую строку."""
    product_id = _safe(card, "id", "configSku")
    name = _safe(card, "title", "shortNameText", default="")
    brand = _safe(card, "brand", default="")

    # Цена: основная — unitPrice; unitSalePrice = со скидкой (на будущее).
    price = _safe(card, "unitPrice", "unitSalePrice", default=None)

    rating = _safe(card, "rating", default=None)
    reviews_count = _safe(card, "reviewsQuantity", default=0)
    stock = _safe(card, "stock", default=None)
    best_merchant = _safe(card, "bestMerchant", default="")
    category_id = _safe(card, "categoryId", default="")

    # Ссылка на карточку: shopLink относительный -> достраиваем до полного URL.
    url = _safe(card, "shopLink", default="")
    if url and url.startswith("/"):
        url = "https://kaspi.kz/shop" + url

    return {
        "product_id": product_id,
        "name": str(name).strip(),
        "brand": str(brand).strip(),
        "price": _to_number(price),
        "rating": _to_number(rating),
        "reviews_count": int(_to_number(reviews_count) or 0),
        "stock": _to_number(stock),
        "best_merchant": str(best_merchant).strip(),
        "category_id": str(category_id).strip(),
        "position": position,
        "url": url,
    }


def parse_pages(pages):
    """Распарсить все страницы, проставляя сквозную позицию в выдаче."""
    rows = []
    position = 0
    for page in pages:
        for card in _extract_cards(page):
            position += 1
            row = parse_card(card, position)
            if row["product_id"] and row["price"]:  # минимальный фильтр валидности
                rows.append(row)
    return rows


FIELDS = ["product_id", "name", "brand", "price", "rating", "reviews_count",
          "stock", "best_merchant", "category_id", "position", "url", "captured_at"]


def save_weekly(rows):
    """Сохранить чистый недельный срез в gzip-CSV с датой сбора."""
    os.makedirs(config.WEEKLY_DIR, exist_ok=True)
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(config.WEEKLY_DIR, f"{captured_at}.csv.gz")

    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for r in rows:
            r["captured_at"] = captured_at
            writer.writerow(r)

    print(f"[parse] недельный срез сохранён: {path} ({len(rows)} товаров)")
    return path


def _latest_raw():
    files = sorted(glob.glob(os.path.join(config.RAW_DIR, "raw_*.json")))
    if not files:
        sys.exit("Нет сырых файлов в data/raw/. Сначала запусти scraper.fetch")
    return files[-1]


def main():
    raw_path = sys.argv[1] if len(sys.argv) > 1 else _latest_raw()
    print(f"=== KaspiPulse parse: {raw_path} ===")

    with open(raw_path, encoding="utf-8") as f:
        pages = json.load(f)

    rows = parse_pages(pages)
    print(f"[parse] валидных товаров: {len(rows)}")
    if rows:
        s = rows[0]
        print(f"[parse] пример: {s['name'][:40]!r} | {s['price']} ₸ | "
              f"рейтинг {s['rating']} | отзывов {s['reviews_count']} | остаток {s['stock']}")

    save_weekly(rows)


if __name__ == "__main__":
    main()
