# zakaz_fish: парсер категорії «Риба та морепродукти» з zakaz.ua (етап 1)

Збирає перелік товарів категорії для Києва (категорії, характеристики, ціни зі сторінок
списку) у SQLite і експортує в CSV/XLSX. Повне ТЗ: [SPEC.md](SPEC.md).

Принципи: один потік, пауза 1,5–3 с, **жодного обходу захисту**. На 403/429/503 або сторінці
Cloudflare (`Just a moment`, `cf-chl`, `challenge-platform`) запуск негайно зупиняється, зібране
зберігається.

## v2: повний знімок (12 міст, мережі, магазини)

Повне ТЗ: [zakaz_fish_spec/SPEC_v2_multicity.md](zakaz_fish_spec/SPEC_v2_multicity.md) (локальна копія, не в git).
Команди етапу 1 (`crawl-*`, `daily`, `export`, `stats`, `smoke-check`) працюють як раніше.

**Що збирається.** Каталог агрегатора для міст зі списку × 5 розділів (риба, консервація, снеки, заморожене,
напівфабрикати з фільтром за рибними назвами), картки товарів, каталоги вітрин мереж (ціна, стара ціна, знижка),
довідник магазинів (з послугою «жива риба»), перевірка інших міст (лише звіт). Далі пакет даних для аналітиків.
Лише HTML-сторінки, без cookies, один потік; `robots.txt` перевіряється для кожного хоста;
`stores-api.zakaz.ua` не використовується взагалі (його robots.txt забороняє доступ).
Плитки «Немає в наявності» (без ціни) не зберігаються.

### Встановлення

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[export,dev]'
python -m pytest                       # офлайн
```

### Команди

```bash
python -m zakaz_fish --config CFG snapshot create [--id 2026-11-05]   # створити знімок і стартові задачі
python -m zakaz_fish --config CFG snapshot run    [--id ID] [--max-requests N] [--stages a,b]
python -m zakaz_fish --config CFG snapshot status [--id ID]           # прогрес, ETA, помилки за годину
python -m zakaz_fish --config CFG snapshot retry-failed [--id ID]
python -m zakaz_fish --config CFG snapshot export [--id ID]           # пакет -> out_dir/exports/
python -m zakaz_fish --config CFG snapshot backup
python -m zakaz_fish --config CFG sample [--preset v2|none] [--add NAME=URL ...]   # живі зразки, <= 40 запитів
```

Усі запити є задачами в постійній черзі (SQLite, WAL): `snapshot run` можна зупинити (Ctrl+C, `kill -9`, перезапуск
контейнера) і запустити знову, він продовжить без дублювання. `snapshot status` можна запускати паралельно з `run`;
`data/status.json` оновлюється щохвилини. Лог: `data/logs/` (stdout), бекап бази після кожного етапу: `data/backups/`.

### Пілот на Mac

```bash
cp config.pilot.example.toml config.pilot.toml      # впишіть contact_email
python -m zakaz_fish --config config.pilot.toml snapshot create
python -m zakaz_fish --config config.pilot.toml snapshot run     # ~500-700 запитів, ~30 хв
python -m zakaz_fish --config config.pilot.toml snapshot export  # -> data/pilot_out/exports/zakaz_fish_<дата>/
```

Відкрийте XLSX і `qa_report.md`. Дивіться передусім: частку товарів «лише на вітрині» і схожість
Івано-Франківська з Кривим Рогом (розмір «національного набору»).

### Сервер (LXC Debian 12 / Ubuntu 24.04, 1 vCPU, 1 ГБ RAM, 5 ГБ диска)

```bash
apt install -y python3-venv git sqlite3
useradd -r -m -d /opt/zakaz_fish zakaz
sudo -u zakaz git clone <repo> /opt/zakaz_fish/app
cd /opt/zakaz_fish/app && sudo -u zakaz python3 -m venv .venv
sudo -u zakaz .venv/bin/pip install -e '.[export]'
sudo -u zakaz cp config.snapshot.example.toml config.toml        # впишіть contact_email, перевірте міста
sudo -u zakaz .venv/bin/python -m zakaz_fish --config config.toml init-db
sudo -u zakaz .venv/bin/python -m zakaz_fish --config config.toml snapshot create
cp deploy/zakaz-fish-snapshot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now zakaz-fish-snapshot
```

Моніторинг: `snapshot status`, `journalctl -u zakaz-fish-snapshot -f`, `data/status.json`.
Після завершення: `snapshot export`, архів `data/out/exports/zakaz_fish_<дата>.zip` скопіювати на Mac (`scp`).
Не запускайте збір на Mac і на сервері одночасно, якщо в них спільна зовнішня IP-адреса.

### Коди виходу `snapshot run`

| Код | Значення | systemd |
|---|---|---|
| 0 | черга вичерпана (`done` / `done_with_failures`) | завершення |
| 1 | неочікувана помилка програми | перезапуск через 15 хв, продовжує з черги |
| 2 | блокування: 403 або сторінка перевірки Cloudflare | **не** перезапускається |
| 3 | запобіжник (8 помилок із 100 запитів, 3 рази за 3 год), три 429 за годину або 5 неочікуваних сторінок поспіль | **не** перезапускається |
| 4 | robots.txt забороняє потрібні адреси | **не** перезапускається |
| 130 | Ctrl+C (знімок `stopped`, черга зберігається) | |

### Що робити після блокування (код 2)

Нічого не змінювати щонайменше добу. Потім запустити знову з меншою швидкістю (збільшити `delay_min_s` /
`delay_max_s` у `[rate]`). Змінювати IP, щоб обійти блокування, **не можна**; проксі, ротація IP, браузерний
User-Agent, копіювання cookie з браузера заборонені. Код 3: подивитися `snapshot status` і `http_log`, лише потім
`snapshot run` знову. Код 4: перевірити `https://zakaz.ua/robots.txt`, не обходити.

### Наступний знімок

`snapshot create --id <нова дата>` (нова база не потрібна; свіжі картки товарів, молодші за
`details_max_age_days`, не перезбираються). Міста з `discovered_cities.csv` можна внести вручну в `cities_extra`.

### Ручна перевірка способів доставки (≈15 хв, користувач)

У браузері у Львові порівняти кількість товарів рибної категорії для гостя без адреси, для «Самовивіз → Львів»,
для «Кур'єр» з адресою у Львові і для «Нова пошта»; подивитися, чи зникають товари NOVUS; перевірити, чи є фільтр
«Мережа/Магазин» і чи змінює він URL. Результати вписати в README пакета (розділ «Контекст міста»).

### Рішення, прийняті за замовчуванням

- Плитки «Немає в наявності» не зберігаються (лічильник `out_of_stock` у `city_category_stats`).
- Для розділу `semi` корінь із фільтром `fish_keywords`; у підрозділи (їх 18) не спускаємось, бо корінь їх містить.
- Порожня категорія малого міста: відповідь 200 з H1 і 0 карток (не редірект і не 404), позначається `not_found`.

## Встановлення (етап 1)

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
| `db_path`, `raw_dir`, `out_dir` | `data/zakaz_fish.sqlite`, `data/raw`, `data/out` | база, стиснуті сирі відповіді, каталог експорту |
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
python -m zakaz_fish export                       # -> out_dir (data/out)
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
| `export` | `<out_dir>/products.csv`, `listing_prices_latest.csv`, `zakaz_fish.xlsx` (CSV: UTF-8 з BOM) |
| `stats` | кількість товарів за `id_type`, категоріями, мережами |

`products.details_status`: `ok`, `empty` (сторінка без хлібних крихт і Taxon), `not_found` (HTTP 404; `--mode new` і `stale` такі товари не вибирають), `error`. `details_scraped_at` ставиться лише при `ok`. 5 порожніх сторінок поспіль зупиняють run (`stopped`). Ctrl+C позначає run `stopped` з причиною `interrupted`, зібране зберігається.

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

- Запити до `stores-api.zakaz.ua/graphql`: `StorePriceSource.fetch` кидає `NotImplementedError`, бо robots.txt
  API забороняє доступ (`Disallow: /`); лише з письмового дозволу zakaz.ua. Парсер відповіді `uberProducts`
  є, таблиця `store_prices` порожня.
- v2: назви категорій вітрин для розділів `snacks`, `frozen`, `semi` («Рибні снеки», «Риба та морепродукти заморожені»,
  «Напівфабрикати…») і батьківські «Чипси та снеки» / «Снеки» перевірені лише за ТЗ. Підтверджено тільки
  «Риба та морепродукти» (NOVUS, METRO, Таврія В), «Консерви» → «Рибна консервація» і «Заморозка» на NOVUS.
  Якщо категорії не знайдено, це видно в `qa_report.md`; виправляйте через `[storefront_overrides.<мережа>]`.
- v2: розмітку вітрин перевірено на novus, metro, tavriav; для інших мереж вона може відрізнятися.
- v2: таблицю `zakaz_fish/data/gs1_prefixes.csv` складено за переліком префіксів GS1; звірте з gs1.org перед публікацією.
- v2: поведінка вітрин за останньою сторінкою (редірект чи порожня сторінка) перевірена лише на моках.
- Next.js RSC-запити, Playwright / headless-браузер.
- Блок `Available Retails` (у серверному HTML його немає).
- Автоматичне визначення міста магазину (`stores.city` заповнюється вручну).
- Категорії рівня 3 зі сторінок рівня 2 (беруться тільки з хлібних крихт).
- Інші міста, окрім Києва (`city` у конфігу вже параметр, але інші міста не перевірялися).
- Чи містить серверний HTML сторінки товару хлібні крихти та `Taxon`: з'ясує `smoke-check`.
- УКТЗЕД і нормалізація виду / форми / обробки; завантаження зображень.
