# BigQuery SQL — аналитический слой KaspiPulse

Этот каталог — **источник правды** для аналитики (раньше это были
`pipeline/estimate.py` и `pipeline/aggregate.py`, теперь они оставлены как
референс). GitHub хранит публичный raw/clean-датасет, BigQuery считает метрики,
Looker Studio рисует дашборды.

## Объекты

| Файл | Объект | Что делает | Заменяет |
|---|---|---|---|
| `01_snapshots.sql` | TABLE `snapshots` | хранит очищенные срезы (выход `clean.py`), партиц. по `captured_at` | — |
| `02_products_timeseries.sql` | VIEW `products_timeseries` | review-velocity, `est_sales`/`est_revenue` (сравнение с предыдущим срезом) | `estimate.py` |
| `03_niche_summary.sql` | VIEW `niche_summary` | сводка по нише + `composite_score` | `aggregate.py` |
| `04_products_timeseries_7d.sql` | VIEW `products_timeseries_7d` | то же, что 02, но база сравнения ~7 дней назад (сглаживание дневного шума) | — |

Методология не менялась: `REVIEW_RATE = 0.02`, нормировка `×7/days_diff`,
`est_revenue = est_sales × price`, веса композита `0.40/0.20/0.20/0.20`.

## Настройка GCP с нуля (один раз, ручные действия владельца)

1. **Проект.** https://console.cloud.google.com → создать проект (напр.
   `kaspipulse`). Запомнить **PROJECT_ID**.
2. **API.** APIs & Services → включить **BigQuery API**.
3. **Dataset.** BigQuery → создать dataset `kaspipulse` в локации
   **`europe-west3`** (Франкфурт). Таблица, запросы и Looker Studio должны быть в
   этой же локации.
4. **Сервис-аккаунт.** IAM & Admin → Service Accounts → создать
   `kaspipulse-loader`. Роли на проект: **BigQuery Data Editor** +
   **BigQuery Job User**.
5. **Ключ.** У сервис-аккаунта → Keys → Add key → **JSON** → скачать.
6. **GitHub.** repo → Settings → Secrets and variables → Actions:
   - **Secret** `GCP_SA_KEY` = всё содержимое JSON-ключа;
   - **Variable** `GCP_PROJECT_ID` = PROJECT_ID;
   - **Variable** `BQ_DATASET` = `kaspipulse`.

Пока `GCP_SA_KEY` не задан, workflow пропускает шаги BigQuery (остаётся зелёным).
Бесплатного тарифа BigQuery (10 ГБ хранения, 1 ТБ запросов/мес) хватает с запасом.

## Развёртывание (один раз, после создания dataset `kaspipulse`)

```bash
PROJECT=YOUR_PROJECT_ID
for f in sql/01_snapshots.sql sql/02_products_timeseries.sql \
         sql/03_niche_summary.sql sql/04_products_timeseries_7d.sql; do
  bq query --use_legacy_sql=false --project_id="$PROJECT" < "$f"
done
```

Имена объектов заданы как `kaspipulse.<...>` (без проекта) — проект берётся из
`--project_id`. Если dataset назван иначе — заменить префикс во всех файлах.

## Загрузка данных (делает GitHub Actions ежедневно)

```bash
DAY=$(date -u +%Y-%m-%d)
bq load --replace --source_format=CSV --skip_leading_rows=1 --autodetect \
  "kaspipulse.snapshots\$${DAY//-/}" \
  "data/weekly/clean/${DAY}.csv.gz"
```
Декоратор партиции `snapshots$YYYYMMDD` делает повторный запуск в тот же день
идемпотентным (партиция перезаписывается, дублей нет).
