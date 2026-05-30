-- KaspiPulse — BigQuery VIEW: products_timeseries_7d  (РЕКОМЕНДУЕМЫЙ для дневного крона)
-- Та же методология review-velocity, что и в 02_products_timeseries.sql
-- (REVIEW_RATE = 0.02, нормировка ×7/days_diff, формулы est_*), НО база сравнения —
-- срез примерно 7 дней назад, а не вчерашний.
--
-- Зачем: крон ежедневный, дневные приросты отзывов крошечные и шумные
-- (у большинства товаров +0/день). Сравнение «вчера→сегодня» с days_diff=1
-- раздувает один случайный отзыв до ~350 est_sales/неделю. Сравнение с
-- ближайшим срезом ≥7 дней назад даёт устойчивую недельную скорость.
-- Меняется ТОЛЬКО база сравнения; коэффициент и формулы идентичны 02.
--
-- Развёртывание:
--   bq query --use_legacy_sql=false --project_id=YOUR_PROJECT < sql/04_products_timeseries_7d.sql

CREATE OR REPLACE VIEW `kaspipulse.products_timeseries_7d` AS
WITH base AS (
  SELECT * FROM `kaspipulse.snapshots`
),
joined AS (
  SELECT
    cur.*,
    prev.reviews_count AS prev_reviews,
    prev.captured_at   AS prev_captured
  FROM base AS cur
  LEFT JOIN base AS prev
    ON prev.product_id = cur.product_id
   AND prev.captured_at <= DATE_SUB(cur.captured_at, INTERVAL 7 DAY)
  -- для каждого текущего среза берём ближайший срез, отстоящий минимум на 7 дней
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cur.product_id, cur.captured_at
    ORDER BY prev.captured_at DESC
  ) = 1
),
calc AS (
  SELECT
    *,
    DATE_DIFF(captured_at, prev_captured, DAY) AS days_diff,
    reviews_count - prev_reviews               AS raw_delta
  FROM joined
),
clamped AS (
  SELECT
    *,
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
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0)             AS delta_reviews_weekly,
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0) / 0.02      AS est_sales,
  SAFE_DIVIDE(delta_reviews, days_diff / 7.0) / 0.02 * price AS est_revenue,
  best_merchant,
  category_id,
  position,
  url
FROM clamped;
