"""
KaspiPulse — pipeline/aggregate.py

[РЕФЕРЕНС] Источник правды для аналитики перенесён в BigQuery SQL:
    sql/03_niche_summary.sql (точный перенос этой логики).
Модуль оставлен как проверенная референсная реализация и для парити-чека.
В ежедневном пайплайне больше не вызывается.

Сводка по нише и композитный скоринг (PROJECT_PLAN.md, раздел 8).
Читает временной ряд из data/derived/products_timeseries.csv.gz и для КАЖДОЙ
недели считает по нише агрегаты + единый composite_score «горячести» ниши.
Сохраняет в data/derived/niche_summary.csv.gz.

НЕ меняет методологию estimate.py (REVIEW_RATE, velocity, нормировку по дням).
Использует уже посчитанные estimate.py колонки (est_revenue, delta_reviews_weekly).

Метрики на неделю (раздел 8):
  - total_est_revenue   : суммарная оценочная выручка ниши.
  - total_products      : число активных SKU.
  - avg_price, median_price.
  - top10_revenue_share : доля выручки топ-10 товаров (индекс концентрации).
  - composite_score     : нормированная композиция четырёх сигналов —
        delta_reviews_weekly (основной сигнал, вес 0.4),
        reviews_count        (популярность,        вес 0.2),
        (5 − rating)         (возможность ниши,     вес 0.2),
        обратная цена 1/price (доступность,         вес 0.2).
    Каждый сигнал нормируется min-max в [0,1] ВНУТРИ недели (по товарам),
    итог — среднее по товарам, диапазон [0,1].

Запуск:
    python -m pipeline.aggregate
"""

import os
import sys

import pandas as pd

# импорт настроек проекта (NICHE_NAME, пути) — как в estimate.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import config  # noqa: E402

DERIVED_DIR = "data/derived"
TIMESERIES_PATH = os.path.join(DERIVED_DIR, "products_timeseries.csv.gz")
SUMMARY_PATH = os.path.join(DERIVED_DIR, "niche_summary.csv.gz")

# Веса композитного скоринга (сумма = 1.0). delta_reviews_weekly — основной сигнал.
W_VELOCITY = 0.40
W_REVIEWS = 0.20
W_QUALITY = 0.20   # (5 − rating): низкий рейтинг конкурентов = возможность ниши
W_CHEAP = 0.20     # обратная цена: дешевле → доступнее

TOP_N = 10         # для top10_revenue_share


def _minmax(s):
    """Min-max нормировка в [0,1]. Если все значения равны — нейтральные 0.5."""
    s = pd.to_numeric(s, errors="coerce").astype(float)
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(0.5, index=s.index)
    return (s - lo) / (hi - lo)


def _composite_score(week_df):
    """
    Композитный скор «горячести» ниши за неделю.
    Нормируем 4 сигнала по товарам внутри недели и берём взвешенное среднее,
    затем усредняем по товарам. Результат в [0,1].
    """
    velocity = _minmax(week_df["delta_reviews_weekly"].fillna(0)) \
        if "delta_reviews_weekly" in week_df else pd.Series(0.5, index=week_df.index)
    reviews = _minmax(week_df["reviews_count"].fillna(0))
    quality = _minmax((5 - pd.to_numeric(week_df["rating"], errors="coerce")).fillna(0))
    inv_price = 1.0 / pd.to_numeric(week_df["price"], errors="coerce").replace(0, pd.NA)
    cheap = _minmax(inv_price.fillna(0))

    per_product = (W_VELOCITY * velocity + W_REVIEWS * reviews +
                   W_QUALITY * quality + W_CHEAP * cheap)
    return float(per_product.mean())


def _top_n_revenue_share(week_df, n=TOP_N):
    """Доля оценочной выручки топ-N товаров (индекс концентрации)."""
    rev = pd.to_numeric(week_df["est_revenue"], errors="coerce").fillna(0)
    total = rev.sum()
    if total <= 0:
        return float("nan")   # первая неделя без динамики — выручки ещё нет
    top = rev.sort_values(ascending=False).head(n).sum()
    return float(top / total)


def aggregate(ts):
    """Свести временной ряд в сводку по нише: одна строка на неделю."""
    ts = ts.copy()
    ts["captured_at"] = pd.to_datetime(ts["captured_at"])

    rows = []
    for week, g in ts.groupby("captured_at", sort=True):
        # одна строка на товар внутри недели (защита от дублей в ряду)
        gw = g.drop_duplicates("product_id", keep="last")

        price = pd.to_numeric(gw["price"], errors="coerce")
        est_rev = pd.to_numeric(gw["est_revenue"], errors="coerce") \
            if "est_revenue" in gw else pd.Series(dtype=float)

        rows.append({
            "captured_at": week.strftime("%Y-%m-%d"),
            "niche": config.NICHE_NAME,
            "niche_slug": config.NICHE_SLUG,
            "total_products": int(gw["product_id"].nunique()),
            "total_est_revenue": float(est_rev.sum()) if len(est_rev) else 0.0,
            "avg_price": float(price.mean()),
            "median_price": float(price.median()),
            "top10_revenue_share": _top_n_revenue_share(gw),
            "composite_score": _composite_score(gw),
        })

    return pd.DataFrame(rows)


def save_summary(df):
    os.makedirs(DERIVED_DIR, exist_ok=True)
    df.to_csv(SUMMARY_PATH, index=False, compression="gzip")
    print(f"[aggregate] сводка по нише сохранена: {SUMMARY_PATH} ({len(df)} недель)")
    return SUMMARY_PATH


def print_summary(df):
    if df.empty:
        print("[aggregate] нет данных для сводки.")
        return
    latest = df.iloc[-1]
    print("\n" + "=" * 60)
    print(f"СВОДКА ПО НИШЕ '{config.NICHE_NAME}' на {latest['captured_at']}")
    print("=" * 60)
    print(f"Активных SKU             : {latest['total_products']}")
    print(f"Средняя цена             : {latest['avg_price']:,.0f} ₸")
    print(f"Медианная цена           : {latest['median_price']:,.0f} ₸")
    print(f"Оценочная выручка ниши   : {latest['total_est_revenue']:,.0f} ₸")
    share = latest["top10_revenue_share"]
    print(f"Доля выручки топ-10      : "
          f"{share:.1%}" if pd.notna(share) else "Доля выручки топ-10      : н/д (нет динамики)")
    print(f"Композитный скор ниши    : {latest['composite_score']:.3f} (из 1.000)")
    print("\n[!] Выручка — ОЦЕНОЧНАЯ (est), не фактическая.")


def main():
    print(f"=== KaspiPulse aggregate: ниша '{config.NICHE_NAME}' ===")
    if not os.path.exists(TIMESERIES_PATH):
        sys.exit(f"Нет {TIMESERIES_PATH}. Сначала запусти pipeline.estimate")

    ts = pd.read_csv(TIMESERIES_PATH, dtype={"product_id": str})
    print(f"[aggregate] временной ряд: {len(ts)} строк")

    summary = aggregate(ts)
    save_summary(summary)
    print_summary(summary)


if __name__ == "__main__":
    main()
