# zakaz_fish: парсер категорії «Риба та морепродукти» з zakaz.ua (етап 1)

Збирає перелік товарів категорії для Києва (категорії, характеристики, ціни зі сторінок
списку) у SQLite і експортує в CSV/XLSX. Повне ТЗ: [SPEC.md](SPEC.md).

Принципи: один потік, пауза 1,5–3 с, **жодного обходу захисту**. На 403/429/503 або сторінці
Cloudflare (`Just a moment`, `cf-chl`, `challenge-platform`) запуск негайно зупиняється, зібране
зберігається.

## Встановлення

Потрібен Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config.example.toml config.toml   # за потреби відредагуйте
```

## Конфіг (`config.toml`)

| Ключ | За замовчуванням | Значення |
|---|---|---|
| `city` | `kyiv` | місто в URL (параметр, не константа) |
| `lang` | `uk` | мова в URL |
| `root_category` | `fish-and-seafood-zakaz` | батьківська категорія |
| `db_path`, `raw_dir` | `data/zakaz_fish.sqlite`, `data/raw` | база та стиснуті сирі відповіді |
| `delay_min_s`, `delay_max_s` | 1.5, 3.0 | випадкова пауза між запитами |
| `timeout_s` | 30 | таймаут запиту |
| `max_pages_per_category` | 100 | запобіжник пагінації |
| `user_agent`, `contact_email` | `KSEA-FishMarketResearch/0.1`, порожньо | email додається як `(+mailto:...)`; заповніть свій |
| `store_descriptions` | `false` | зберігати маркетингові описи товарів |

Без `config.toml` використовуються значення за замовчуванням. Інший файл: `python -m zakaz_fish --config шлях.toml <команда>`.

## Перший запуск

```bash
python -m zakaz_fish init-db
python -m zakaz_fish seed --chains tests/fixtures/graphql_chains.json \
                          --stores tests/fixtures/graphql_delivery_schedules.json
python -m pytest                                  # офлайн, мережа не потрібна
python -m zakaz_fish smoke-check                  # рівно 3 запити до живого сайту
python -m zakaz_fish crawl-listings
python -m zakaz_fish crawl-details --mode new --limit 20
python -m zakaz_fish stats
python -m zakaz_fish export                       # -> data/out/
```

Рекомендований порядок: `init-db` → `seed` → `pytest` → `smoke-check` → `crawl-listings` →
`crawl-details --mode new --limit 20` → `stats` → далі `daily`.

`crawl-*` і `daily` відмовляються працювати, якщо останній `smoke-check` не OK; обхід: `--force`.
Відповіді `smoke-check` зберігаються в `tests/fixtures/live/` (їх можна додати як фікстури).
Код завершення: 0 ок, 1 помилка/FAIL, 2 зупинка через захист сайту.

## Команди

| Команда | Дія |
|---|---|
| `init-db` | створює схему (повторний запуск безпечний) |
| `seed` | заповнює `chains` і `stores` з фікстур (`city` магазинів заповнюється вручну і не перезаписується) |
| `smoke-check` | 3 запити, звіт «перевірка → OK/FAIL → що знайдено» |
| `crawl-listings` | обхід батьківської категорії та підкатегорій, `listing_prices`, `last_seen`, `is_active` |
| `crawl-details --mode new\|all\|stale:<днів> [--limit N]` | сторінки товарів: характеристики, склад, КБЖУ, хлібні крихти |
| `daily` | `crawl-listings` + `crawl-details --mode new` в одному run |
| `export` | `data/out/products.csv`, `listing_prices_latest.csv`, `zakaz_fish.xlsx` (CSV: UTF-8 з BOM) |
| `stats` | кількість товарів за `id_type`, категоріями, мережами |

`is_active = 0` ставиться лише після запуску `crawl-listings`, що завершився `ok` для всіх категорій.

## Розклад

Автоматично не встановлюється, нижче лише приклади. Шляхи замініть на свої.

**cron** (`crontab -e`, щодня о 07:30):

```cron
30 7 * * * cd /Users/USER/KSEA_fish_parser && .venv/bin/python -m zakaz_fish daily >> data/daily.log 2>&1
```

**launchd** (`~/Library/LaunchAgents/ua.ksea.zakaz-fish.plist`, потім
`launchctl load ~/Library/LaunchAgents/ua.ksea.zakaz-fish.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ua.ksea.zakaz-fish</string>
  <key>WorkingDirectory</key><string>/Users/USER/KSEA_fish_parser</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/USER/KSEA_fish_parser/.venv/bin/python</string>
    <string>-m</string><string>zakaz_fish</string><string>daily</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/Users/USER/KSEA_fish_parser/data/daily.log</string>
  <key>StandardErrorPath</key><string>/Users/USER/KSEA_fish_parser/data/daily.log</string>
</dict>
</plist>
```

## TODO(unverified): не реалізовано, бо не перевірено

- Запити до `stores-api.zakaz.ua/graphql`: `StorePriceSource.fetch` кидає `NotImplementedError`.
  Невідомо, як передаються магазини (змінні чи `X-Store-Ids` / `X-Chain-Ids` / `X-Delivery-Type`)
  і чи працює запит без cookie. Парсер відповіді `uberProducts` є, таблиця `store_prices` порожня.
- Next.js RSC-запити, Playwright / headless-браузер.
- Блок `Available Retails` (у серверному HTML його немає).
- Автоматичне визначення міста магазину (`stores.city` заповнюється вручну).
- Категорії рівня 3 зі сторінок рівня 2 (беруться тільки з хлібних крихт).
- Інші міста, окрім Києва (`city` у конфігу вже параметр, але інші міста не перевірялися).
- Чи містить серверний HTML сторінки товару хлібні крихти та `Taxon`: з'ясує `smoke-check`.
- УКТЗЕД і нормалізація виду / форми / обробки; завантаження зображень.
