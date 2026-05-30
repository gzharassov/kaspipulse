-- KaspiPulse — BigQuery: таблица сырых (очищенных) срезов.
-- Сюда GitHub Actions грузит выход scraper/clean.py по одному срезу в день.
-- Партиционирование по captured_at делает дозагрузку идемпотентной:
-- повторный bq load в партицию snapshots$YYYYMMDD заменяет данные за этот день.
--
-- Развёртывание:
--   bq query --use_legacy_sql=false --project_id=YOUR_PROJECT < sql/01_snapshots.sql
-- (dataset kaspipulse должен существовать; см. sql/README.md)

CREATE TABLE IF NOT EXISTS `kaspipulse.snapshots`
(
  product_id     STRING,
  name           STRING,
  brand          STRING,
  price          FLOAT64,
  rating         FLOAT64,
  reviews_count  INT64,
  stock          FLOAT64,
  best_merchant  STRING,
  category_id    STRING,
  position       INT64,
  url            STRING,
  captured_at    DATE,
  seen_before    BOOL,
  outlier        BOOL
)
PARTITION BY captured_at
OPTIONS (
  description = "KaspiPulse: очищенные недельные срезы ниши Kaspi.kz (выход scraper/clean.py)."
);
