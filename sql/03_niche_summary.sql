-- KaspiPulse — BigQuery VIEW: niche_summary
-- Точный перенос pipeline/aggregate.py (сводка по нише + композитный скоринг).
-- Веса композита 0.40/0.20/0.20/0.20; min-max нормировка сигналов ВНУТРИ недели,
-- нейтраль 0.5 при равных значениях (как _minmax в aggregate.py).
-- median_price — точный (PERCENTILE_CONT), как pandas .median().
-- top10_revenue_share = NULL, если выручки ещё нет (первый срез без динамики).
--
-- Развёртывание:
--   bq query --use_legacy_sql=false --project_id=YOUR_PROJECT < sql/03_niche_summary.sql

CREATE OR REPLACE VIEW `kaspipulse.niche_summary` AS
WITH ts AS (
  SELECT
    captured_at,
    product_id,
    price,
    est_revenue,
    -- сигналы композита (с fillna(0), как в aggregate.py)
    COALESCE(delta_reviews_weekly, 0)   AS v_raw,   -- основной сигнал (velocity)
    CAST(reviews_count AS FLOAT64)      AS r_raw,   -- популярность
    COALESCE(5 - rating, 0)             AS q_raw,   -- возможность (низкий рейтинг)
    COALESCE(SAFE_DIVIDE(1, price), 0)  AS c_raw    -- доступность (обратная цена)
  FROM `kaspipulse.products_timeseries`
),
bounds AS (
  SELECT
    *,
    MIN(v_raw) OVER w AS v_min, MAX(v_raw) OVER w AS v_max,
    MIN(r_raw) OVER w AS r_min, MAX(r_raw) OVER w AS r_max,
    MIN(q_raw) OVER w AS q_min, MAX(q_raw) OVER w AS q_max,
    MIN(c_raw) OVER w AS c_min, MAX(c_raw) OVER w AS c_max
  FROM ts
  WINDOW w AS (PARTITION BY captured_at)
),
norm AS (
  SELECT
    captured_at, product_id, price, est_revenue,
    IF(v_max = v_min, 0.5, (v_raw - v_min) / (v_max - v_min)) AS n_v,
    IF(r_max = r_min, 0.5, (r_raw - r_min) / (r_max - r_min)) AS n_r,
    IF(q_max = q_min, 0.5, (q_raw - q_min) / (q_max - q_min)) AS n_q,
    IF(c_max = c_min, 0.5, (c_raw - c_min) / (c_max - c_min)) AS n_c
  FROM bounds
),
per_product AS (
  SELECT
    captured_at, product_id, price, est_revenue,
    0.40 * n_v + 0.20 * n_r + 0.20 * n_q + 0.20 * n_c AS composite_product,
    ROW_NUMBER() OVER (
      PARTITION BY captured_at ORDER BY IFNULL(est_revenue, 0) DESC
    ) AS rev_rank
  FROM norm
),
med AS (
  SELECT DISTINCT
    captured_at,
    PERCENTILE_CONT(price, 0.5) OVER (PARTITION BY captured_at) AS median_price
  FROM per_product
)
SELECT
  p.captured_at,
  'Товары для дома' AS niche,        -- = config.NICHE_NAME
  'home'            AS niche_slug,   -- = config.NICHE_SLUG
  COUNT(DISTINCT p.product_id)                 AS total_products,
  COALESCE(SUM(p.est_revenue), 0)              AS total_est_revenue,
  AVG(p.price)                                 AS avg_price,
  ANY_VALUE(m.median_price)                    AS median_price,
  SAFE_DIVIDE(
    SUM(IF(p.rev_rank <= 10, IFNULL(p.est_revenue, 0), 0)),
    NULLIF(SUM(IFNULL(p.est_revenue, 0)), 0)
  )                                            AS top10_revenue_share,
  AVG(p.composite_product)                     AS composite_score
FROM per_product p
JOIN med m USING (captured_at)
GROUP BY p.captured_at
ORDER BY p.captured_at;
