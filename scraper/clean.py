"""
KaspiPulse — scraper/clean.py

Отдельный модуль очистки недельного среза. Реализует 7 стадий из PROJECT_PLAN.md
(раздел 7). НЕ трогает parse.py и НЕ меняет методологию estimate.py.

Что делает:
  - читает свежий срез из data/weekly/*.csv.gz (верхний уровень, без подпапки clean/);
  - прогоняет 7 стадий очистки;
  - пишет очищенный срез в data/weekly/clean/YYYY-MM-DD.csv.gz;
  - пишет построчный лог отброшенного/изменённого в
    data/_logs/cleaning_report_YYYY-MM-DD.csv.

Стадии (PROJECT_PLAN, раздел 7):
  1. Null/мусор          — отсев товаров без обязательных полей.
  2. Дедупликация SKU    — один product_id = одна строка (мин. цена,
                            склейка best_merchant через запятую).
  3. Диапазоны цен       — широкие пороги 5 ≤ price ≤ 5 000 000; отсечь и залогировать.
  4. Стабильность ID     — матчинг с предыдущим недельным срезом по product_id
                            (флаг seen_before: новый товар или вернувшийся).
  5. Сброс отзывов       — review_reset оставлен в estimate.py (по плану),
                            здесь не дублируется; считаем только delta для стадии 6.
  6. Выбросы продаж      — аномально большой Δreviews помечается outlier=True,
                            НЕ удаляется (для прозрачности).
  7. Логирование         — что отброшено/изменено и почему → cleaning_report.

Запуск:
    python -m scraper.clean                       # последний срез из data/weekly/
    python -m scraper.clean data/weekly/2026-06-01.csv.gz
"""

import glob
import os
import sys

import pandas as pd

from . import config

# ---------------------------------------------------------------------------
# Параметры очистки
# ---------------------------------------------------------------------------
PRICE_MIN = 5            # ₸ — нижний порог (дешёвые расходники легитимны)
PRICE_MAX = 5_000_000    # ₸ — верхний порог
REQUIRED_FIELDS = ["product_id", "price"]  # обязательные поля (как в parse.py)

# Детекция выбросов прироста отзывов (метод IQR)
OUTLIER_IQR_K = 3.0      # множитель IQR для верхней границы
OUTLIER_MIN_POINTS = 8   # минимум точек, иначе детекцию пропускаем

CLEAN_DIR = os.path.join(config.WEEKLY_DIR, "clean")
LOGS_DIR = "data/_logs"


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def _latest_weekly():
    """Самый свежий срез верхнего уровня data/weekly/ (без подпапки clean/)."""
    files = sorted(glob.glob(os.path.join(config.WEEKLY_DIR, "*.csv.gz")))
    if not files:
        sys.exit(f"Нет срезов в {config.WEEKLY_DIR}. Сначала запусти scraper.parse")
    return files[-1]


def _previous_weekly(current_path):
    """Предыдущий недельный срез (для матчинга ID и расчёта delta)."""
    files = sorted(glob.glob(os.path.join(config.WEEKLY_DIR, "*.csv.gz")))
    cur = os.path.abspath(current_path)
    files = [f for f in files if os.path.abspath(f) != cur]
    return files[-1] if files else None


class Report:
    """Накопитель построчного лога очистки."""

    def __init__(self):
        self.rows = []

    def add(self, df_rows, stage, action, reason, detail=""):
        """Записать затронутые строки (DataFrame или одна строка-Series)."""
        if isinstance(df_rows, pd.Series):
            df_rows = df_rows.to_frame().T
        for _, r in df_rows.iterrows():
            self.rows.append({
                "stage": stage,
                "action": action,         # dropped | merged | flagged
                "reason": reason,
                "product_id": r.get("product_id", ""),
                "name": str(r.get("name", ""))[:80],
                "price": r.get("price", ""),
                "reviews_count": r.get("reviews_count", ""),
                "detail": detail,
            })

    def to_frame(self):
        cols = ["stage", "action", "reason", "product_id", "name",
                "price", "reviews_count", "detail"]
        if not self.rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self.rows)[cols]


# ---------------------------------------------------------------------------
# Стадии очистки
# ---------------------------------------------------------------------------
def stage1_nulls(df, report):
    """Стадия 1: отсев строк без обязательных полей."""
    mask_bad = pd.Series(False, index=df.index)
    for fld in REQUIRED_FIELDS:
        if fld not in df.columns:
            sys.exit(f"[clean] в срезе нет обязательной колонки {fld!r}")
        mask_bad |= df[fld].isna() | (df[fld].astype(str).str.strip() == "")
    bad = df[mask_bad]
    if len(bad):
        report.add(bad, stage="1_nulls", action="dropped",
                   reason="missing_required_field")
    kept = df[~mask_bad].copy()
    print(f"[clean] стадия 1 (null/мусор): отброшено {len(bad)}, осталось {len(kept)}")
    return kept


def stage2_dedup(df, report):
    """Стадия 2: дедуп по product_id (мин. цена, склейка best_merchant)."""
    df = df.copy()
    df["product_id"] = df["product_id"].astype(str).str.strip()

    dup_mask = df.duplicated("product_id", keep=False)
    n_dup_ids = df.loc[dup_mask, "product_id"].nunique()
    if n_dup_ids == 0:
        print(f"[clean] стадия 2 (дедуп): дублей нет, осталось {len(df)}")
        return df.reset_index(drop=True)

    out_rows = []
    for pid, g in df.groupby("product_id", sort=False):
        if len(g) == 1:
            out_rows.append(g.iloc[0])
            continue
        # каноническая строка = строка с минимальной ценой
        idx = g["price"].idxmin()
        row = g.loc[idx].copy()
        merchants = sorted({
            str(m).strip() for m in g.get("best_merchant", pd.Series(dtype=str))
            if str(m).strip() and str(m).strip().lower() != "nan"
        })
        merged_merchant = ", ".join(merchants)
        row["best_merchant"] = merged_merchant
        row["price"] = g["price"].min()
        out_rows.append(row)
        report.add(g, stage="2_dedup", action="merged",
                   reason="duplicate_sku",
                   detail=f"{len(g)} строк → 1; min_price={g['price'].min()}; "
                          f"best_merchant='{merged_merchant}'")

    result = pd.DataFrame(out_rows).reset_index(drop=True)
    print(f"[clean] стадия 2 (дедуп): {n_dup_ids} дублирующихся SKU схлопнуто, "
          f"осталось {len(result)}")
    return result


def stage3_price_range(df, report):
    """Стадия 3: отсечь цены вне широких порогов 5 ≤ price ≤ 5 000 000."""
    price = pd.to_numeric(df["price"], errors="coerce")
    out_of_range = (price < PRICE_MIN) | (price > PRICE_MAX) | price.isna()
    bad = df[out_of_range]
    if len(bad):
        report.add(bad, stage="3_price_range", action="dropped",
                   reason="price_out_of_range",
                   detail=f"допустимо [{PRICE_MIN}, {PRICE_MAX}] ₸")
    kept = df[~out_of_range].copy()
    print(f"[clean] стадия 3 (диапазон цен): отброшено {len(bad)}, осталось {len(kept)} "
          f"(пороги {PRICE_MIN}..{PRICE_MAX} ₸)")
    return kept


def stage4_id_stability(df, prev_df, report):
    """Стадия 4: матчинг с предыдущим срезом по product_id (флаг seen_before)."""
    df = df.copy()
    if prev_df is None:
        df["seen_before"] = False
        print("[clean] стадия 4 (стабильность ID): предыдущего среза нет — "
              "все товары новые (seen_before=False)")
        return df
    prev_ids = set(prev_df["product_id"].astype(str).str.strip())
    df["seen_before"] = df["product_id"].astype(str).str.strip().isin(prev_ids)
    n_seen = int(df["seen_before"].sum())
    n_new = len(df) - n_seen
    print(f"[clean] стадия 4 (стабильность ID): вернувшихся {n_seen}, новых {n_new}")
    return df


def stage5_6_outliers(df, prev_df, report):
    """
    Стадия 5: review_reset оставлен в estimate.py — здесь не дублируется.
    Стадия 6: пометить аномально большой Δreviews как outlier (НЕ удалять).
    Δreviews считается относительно предыдущего недельного среза, только для
    детекции выбросов; отрицательные приросты (сброс счётчика) игнорируются.
    """
    df = df.copy()
    df["outlier"] = False

    if prev_df is None:
        print("[clean] стадия 6 (выбросы): предыдущего среза нет — детекция пропущена")
        return df

    prev = prev_df[["product_id", "reviews_count"]].copy()
    prev["product_id"] = prev["product_id"].astype(str).str.strip()
    prev = prev.rename(columns={"reviews_count": "_prev_reviews"})
    prev = prev.drop_duplicates("product_id")

    pid = df["product_id"].astype(str).str.strip()
    merged = df.assign(_pid=pid).merge(
        prev, left_on="_pid", right_on="product_id", how="left",
        suffixes=("", "_p"))
    delta = pd.to_numeric(merged["reviews_count"], errors="coerce") - \
        pd.to_numeric(merged["_prev_reviews"], errors="coerce")

    # только положительные приросты у вернувшихся товаров
    positive = delta[delta > 0].dropna()
    if len(positive) < OUTLIER_MIN_POINTS:
        print(f"[clean] стадия 6 (выбросы): точек {len(positive)} < "
              f"{OUTLIER_MIN_POINTS} — детекция пропущена")
        return df

    q1, q3 = positive.quantile(0.25), positive.quantile(0.75)
    iqr = q3 - q1
    threshold = q3 + OUTLIER_IQR_K * iqr
    outlier_mask = (delta > threshold).fillna(False).to_numpy()
    df["outlier"] = outlier_mask

    flagged = df[df["outlier"]]
    if len(flagged):
        # detail с величиной прироста по каждому помеченному товару
        delta_by_idx = delta.to_numpy()
        for pos, (_, r) in enumerate(df.iterrows()):
            if df["outlier"].to_numpy()[pos]:
                report.add(r, stage="6_outliers", action="flagged",
                           reason="abnormal_delta_reviews",
                           detail=f"Δreviews={delta_by_idx[pos]:.0f} > "
                                  f"threshold={threshold:.0f} (Q3+{OUTLIER_IQR_K}·IQR)")
    print(f"[clean] стадия 6 (выбросы): порог Δreviews>{threshold:.0f}, "
          f"помечено outlier={len(flagged)} (не удалены)")
    return df


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------
def save_clean(df, captured_at):
    os.makedirs(CLEAN_DIR, exist_ok=True)
    path = os.path.join(CLEAN_DIR, f"{captured_at}.csv.gz")
    df.to_csv(path, index=False, compression="gzip")
    print(f"[clean] очищенный срез сохранён: {path} ({len(df)} товаров)")
    return path


def save_report(report, captured_at):
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"cleaning_report_{captured_at}.csv")
    report.to_frame().to_csv(path, index=False)
    print(f"[clean] отчёт очистки сохранён: {path} ({len(report.rows)} записей)")
    return path


def _captured_at_from_path(path):
    """Дата среза из имени файла YYYY-MM-DD.csv.gz."""
    base = os.path.basename(path)
    return base.split(".")[0]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else _latest_weekly()
    captured_at = _captured_at_from_path(in_path)
    print(f"=== KaspiPulse clean: {in_path} (дата {captured_at}) ===")

    # product_id читаем как строку: сохраняем точные ID (большие числа,
    # буквенно-цифровые SKU, ведущие нули) и избегаем приведения к float.
    df = pd.read_csv(in_path, dtype={"product_id": str})
    n_in = len(df)
    print(f"[clean] на входе: {n_in} строк")

    prev_path = _previous_weekly(in_path)
    prev_df = pd.read_csv(prev_path, dtype={"product_id": str}) if prev_path else None
    if prev_path:
        print(f"[clean] предыдущий срез для матчинга: {prev_path}")

    report = Report()
    df = stage1_nulls(df, report)
    df = stage2_dedup(df, report)
    df = stage3_price_range(df, report)
    df = stage4_id_stability(df, prev_df, report)
    df = stage5_6_outliers(df, prev_df, report)

    out_path = save_clean(df, captured_at)
    save_report(report, captured_at)

    print("\n" + "=" * 56)
    print(f"ИТОГ очистки {captured_at}: {n_in} → {len(df)} строк "
          f"({n_in - len(df)} удалено, {int(df['outlier'].sum())} помечено outlier)")
    print("=" * 56)
    return out_path


if __name__ == "__main__":
    main()
