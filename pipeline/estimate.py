"""
KaspiPulse — pipeline/estimate.py

Ядро методологии (см. PROJECT_PLAN.md, раздел 2).

Что делает:
  1. Склеивает ВСЕ недельные срезы из data/weekly/*.csv.gz в один временной ряд
     (по колонке captured_at).
  2. Для каждого товара считает прирост отзывов между соседними неделями (Δreviews).
  3. Переводит Δreviews в оценочные продажи: est_sales = Δreviews / REVIEW_RATE.
  4. Считает оценочную недельную выручку: est_revenue = est_sales * price.
  5. Сохраняет результат в data/derived/products_timeseries.csv.gz.

Работает и на ОДНОМ срезе (тогда динамики ещё нет — выводит только статику),
и автоматически считает velocity, когда срезов становится 2+.

Запуск:
    python -m pipeline.estimate
"""

import glob
import os
import sys

import pandas as pd

# импорт настроек проекта (REVIEW_RATE, пути)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import config  # noqa: E402

DERIVED_DIR = "data/derived"
WEEKLY_GLOB = os.path.join(config.WEEKLY_DIR, "*.csv.gz")


def load_all_weeks():
    """
    Прочитать все недельные срезы и склеить в одну таблицу.
    Это и есть ответ на вопрос 'как собрать отчёт из отдельных файлов' —
    glob находит все файлы, pandas склеивает их в один временной ряд.
    """
    files = sorted(glob.glob(WEEKLY_GLOB))
    if not files:
        sys.exit(f"Нет срезов в {config.WEEKLY_DIR}. Сначала собери данные (scraper.fetch + parse).")

    print(f"[estimate] найдено срезов: {len(files)}")
    frames = []
    for f in files:
        df = pd.read_csv(f)
        frames.append(df)
        print(f"  {os.path.basename(f)}: {len(df)} товаров")

    all_weeks = pd.concat(frames, ignore_index=True)
    all_weeks["captured_at"] = pd.to_datetime(all_weeks["captured_at"])
    return all_weeks, len(files)


def compute_velocity(all_weeks):
    """
    Посчитать недельную динамику по каждому товару.

    Для каждого product_id сортируем по дате и берём прирост отзывов
    относительно ПРЕДЫДУЩЕГО среза этого же товара. Прирост нормируется
    на интервал в днях (если между срезами 14 дней — делим пополам,
    чтобы получить недельную скорость).
    """
    all_weeks = all_weeks.sort_values(["product_id", "captured_at"]).copy()

    # прирост отзывов и интервал между срезами (внутри каждого товара)
    grp = all_weeks.groupby("product_id")
    all_weeks["prev_reviews"]  = grp["reviews_count"].shift(1)
    all_weeks["prev_captured"] = grp["captured_at"].shift(1)
    all_weeks["delta_reviews"] = all_weeks["reviews_count"] - all_weeks["prev_reviews"]
    all_weeks["days_diff"] = (
        all_weeks["captured_at"] - all_weeks["prev_captured"]
    ).dt.days

    # защита от сброса счётчика отзывов (если стало меньше — считаем 0, флаг)
    all_weeks["review_reset"] = all_weeks["delta_reviews"] < 0
    all_weeks.loc[all_weeks["review_reset"], "delta_reviews"] = 0

    # НОРМИРОВКА: если между срезами не 7 дней — масштабируем к недельной скорости.
    # Пример: 14 дней между срезами и +60 отзывов -> 30 отзывов/неделю.
    # Это критично для корректной оценки продаж, иначе при пропуске недели
    # est_sales будет завышен в 2 раза.
    weeks_factor = all_weeks["days_diff"] / 7.0
    all_weeks["delta_reviews_weekly"] = all_weeks["delta_reviews"] / weeks_factor

    # оценка продаж и выручки по методу review-velocity (на недельной скорости)
    rate = config.REVIEW_RATE
    all_weeks["est_sales"]   = all_weeks["delta_reviews_weekly"] / rate
    all_weeks["est_revenue"] = all_weeks["est_sales"] * all_weeks["price"]

    return all_weeks


def save_derived(df):
    os.makedirs(DERIVED_DIR, exist_ok=True)
    path = os.path.join(DERIVED_DIR, "products_timeseries.csv.gz")
    cols = ["captured_at", "product_id", "name", "brand", "price", "rating",
            "reviews_count", "delta_reviews", "days_diff", "delta_reviews_weekly",
            "est_sales", "est_revenue", "review_reset",
            "best_merchant", "category_id", "position", "url"]
    cols = [c for c in cols if c in df.columns]
    df[cols].to_csv(path, index=False, compression="gzip")
    print(f"[estimate] временной ряд сохранён: {path}")
    return path


def print_summary(df, n_weeks):
    """Краткий отчёт в консоль — чтобы сразу видеть результат глазами."""
    latest_date = df["captured_at"].max()
    latest = df[df["captured_at"] == latest_date]

    print("\n" + "=" * 60)
    print(f"СВОДКА на {latest_date.date()} (ниша: {config.NICHE_NAME})")
    print("=" * 60)
    print(f"Товаров в последнем срезе : {len(latest)}")
    print(f"Средняя цена              : {latest['price'].mean():,.0f} ₸")
    print(f"Суммарно отзывов          : {int(latest['reviews_count'].sum()):,}")

    if n_weeks < 2:
        print("\n[i] Пока только ОДИН срез — динамику продаж посчитать не из чего.")
        print("    Собери ещё хотя бы один срез через неделю, и появятся оценки продаж.")
        print("\nТОП-5 товаров по АБСОЛЮТНОМУ числу отзывов (что вообще популярно):")
        top = latest.sort_values("reviews_count", ascending=False).head(5)
        for _, r in top.iterrows():
            print(f"  • {r['name'][:50]:50}  {int(r['reviews_count']):>7,} отзывов  {r['price']:>8,.0f} ₸")
        return

    # есть динамика
    est_week_revenue = latest["est_revenue"].sum()
    print(f"\nОЦЕНОЧНАЯ выручка ниши за неделю : {est_week_revenue:,.0f} ₸")
    print(f"(метод review-velocity, review_rate = {config.REVIEW_RATE:.0%})")

    print("\nТОП-5 товаров по ОЦЕНОЧНЫМ продажам за неделю:")
    top = latest.sort_values("est_sales", ascending=False).head(5)
    for _, r in top.iterrows():
        print(f"  • {r['name'][:45]:45}  +{int(r['delta_reviews']):>4} отз  "
              f"≈{int(r['est_sales']):>5} прод  ≈{r['est_revenue']:>10,.0f} ₸")
    print("\n[!] Все продажи и выручка — ОЦЕНОЧНЫЕ, не фактические.")


def main():
    print(f"=== KaspiPulse estimate: ниша '{config.NICHE_NAME}' ===")
    all_weeks, n_weeks = load_all_weeks()
    result = compute_velocity(all_weeks)
    save_derived(result)
    print_summary(result, n_weeks)


if __name__ == "__main__":
    main()
