-- KaspiPulse — BigQuery VIEW: products_timeseries
-- Точный перенос pipeline/estimate.py (методология review-velocity).
-- НЕ меняет методологию: REVIEW_RATE = 0.02, нормировка ×7/days_diff,
-- est_sales = delta_reviews_weekly / REVIEW_RATE, est_revenue = est_sales * price,
-- review_reset обнуляет отрицательный прирост.
--
-- Соответствие estimate.py по краевым случаям:
--   • первый срез товара (нет предыдущего): delta_reviews = NULL, est_* = NULL,
--     review_reset = FALSE;
--   • сброс счётчика (стало меньше): delta_reviews = 0, est_* = 0, review_reset = TRUE.
--
-- Сравнение идёт с НЕПОСРЕДСТВЕННО предыдущим срезом товара (LAG 1) — как в
-- estimate.py. Для сглаживания дневного шума см. 04_products_timeseries_7d.sql.
--
-- Развёртывание:
--   bq query --use_legacy_sql=false --project_id=YOUR_PROJECT < sql/02_products_timeseries.sql

CREATE OR REPLACE VIEW `kaspipulse.products_timeseries` AS
WITH ordered AS (
  SELECT
    *,
    LAG(reviews_count) OVER w AS prev_reviews,
    LAG(captured_at)   OVER w AS prev_captured
  FROM `kaspipulse.snapshots`
  WINDOW w AS (PARTITION BY product_id ORDER BY captured_at)
),
calc AS (
  SELECT
    *,
    DATE_DIFF(captured_at, prev_captured, DAY) AS days_diff,
    reviews_count - prev_reviews               AS raw_delta
  FROM ordered
),
clamped AS (
  SELECT
    *,
    -- NULL для первого среза товара; 0 при сбросе; иначе прирост (как estimate.py)
    IF(raw_delta IS NULL, NULL, GREATEST(raw_delta, 0)) AS delta_reviews,
    COALESCE(raw_delta < 0, FALSE)                      AS review_reset
  FROM calc
)
SELECT
  captured_at,
  product_id,
  name,
  brand,
  price,
  rating,
  reviews_count,
  delta_reviews,
  review_reset,
  days_diff,
  -- нормировка к недельной скорости: delta / (days_diff / 7)
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0)             AS delta_reviews_weekly,
  -- оценка продаж: delta_reviews_weekly / REVIEW_RATE (0.02)
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0) / 0.02      AS est_sales,
  -- оценочная выручка: est_sales * price
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0) / 0.02 * price AS est_revenue,
  best_merchant,
  category_id,
  position,
  url
FROM clamped;
