# ТЗ: парсер категорії «Риба та морепродукти» з zakaz.ua (етап 1)

> **Для Claude Code.** Прочитай цей файл повністю перед тим, як писати код.
> Працюй етапами з розділу 10, після кожного етапу запускай тести.
> Реалізуй **лише** те, що описано в розділі 3 «Робимо». Усе з розділу 3 «Не робимо»
> залиш як заглушки з `NotImplementedError` і коментарем `# TODO(unverified): ...`.
> Якщо щось у цьому ТЗ суперечить реальним даним (тестам або smoke-check), не вигадуй
> обхідний шлях: зупинись і опиши розбіжність.

---

## 0. Контекст

Це частина дослідження ринку риби в Україні. Перший етап: зібрати повний перелік
товарів категорії «Риба та морепродукти» на zakaz.ua для Києва, з категоріями,
характеристиками та цінами, і далі регулярно (щодня або щотижня) оновлювати базу.

zakaz.ua є агрегатором: один товар може продаватися в кількох мережах (NOVUS, Auchan,
METRO, …) і в різних магазинах цих мереж за різними цінами.

Код запускається на комп'ютері користувача (macOS), Python 3.11+.

---

## 1. Принципи

1. **Тільки перевірене.** Кожен парсер має тест на реальному збереженому файлі з
   `tests/fixtures/`. Якщо поведінку не можна перевірити на фікстурі, її немає в обсязі робіт.
2. **Бережливо до сайту.** Один потік, пауза 1,5–3 с між запитами (випадкова), без
   паралельності. Сайт працює за Cloudflare.
3. **Не обходимо захист.** Якщо сайт повертає 403/429/503 або сторінку перевірки Cloudflare
   (`Just a moment`, `cf-chl`, `challenge-platform`), запуск **негайно зупиняється** з
   зрозумілим повідомленням. Жодних обхідних бібліотек, підміни TLS-відбитків, копіювання
   cookie з браузера чи ротації проксі. Причина: умови користування zakaz.ua прямо
   забороняють втручання в технічні засоби захисту.
4. **Зберігаємо факти, а не контент.** Назви, ціни, вага, бренд, виробник, країна, склад і
   КБЖУ зберігаємо. Фото тільки як URL, файли не завантажуємо. Маркетингові описи
   за замовчуванням не зберігаємо (див. прапорець у розділі 6.4).
5. **Відтворюваність.** Кожен запуск має `run_id`; сирі відповіді зберігаються стиснутими,
   щоб при зміні логіки парсингу не качати сайт заново.
6. **Ідемпотентність.** Повторний запуск того самого кроку не створює дублікатів у
   довідкових таблицях; знімки цін додаються з новим `run_id`.

---

## 2. Що ми вже точно знаємо (перевірено на фікстурах)

### 2.1 Сторінки списку (категорії)

- URL: `https://zakaz.ua/uk/kyiv/<slug>/`, далі `?page=2`, `?page=3`, …
- На сторінці 40 карток. **Віджет пагінації бреше**: він показує 61 сторінку, тобто
  розраховує 25 на сторінку (1502/25). Насправді сторінок близько 38 (1502/40). Тому
  зупиняємось за фактом: коли на сторінці 0 карток або немає жодного нового `product_id`.
- Загальна кількість: `[data-marker="Products"] .ProductBox__head p` → `"1502 товари"`.
- Підкатегорії рівня 2 (посилання `.CategoriesSlider__item a` на сторінці
  `fish-and-seafood-zakaz`): `fresh-fish-zakaz`, `frozen-fish-zakaz`, `seafood-zakaz`,
  `fish-paste-zakaz`, `crab-sticks-zakaz`, `preserves-zakaz`, `laminaria-zakaz`,
  `prepared-fish-zakaz`, `caviar-zakaz`.

Селектори картки (BeautifulSoup, `soup.select`):

| Поле | Селектор / правило |
|---|---|
| картка | `[data-marker="Products List"] a[href*="/uk/products/"]`, у якої є нащадок `[data-marker="CatalogProductTile"]` |
| url | атрибут `href` посилання |
| title | атрибут `title` посилання |
| ціна мін | `.PricesRange__start` (текст `"199.00"`) |
| ціна макс | `.PricesRange__end`, якщо є; інакше дорівнює мін |
| вага/одиниця | `[data-marker="Weight"]` (`"за 1 кг"`, `"250 г"`, …) |
| акція | наявність `[data-marker="Offer"]` |
| фото | `img[src]` всередині картки |

Не використовуй класи виду `css-xxxxxx`: це хеші, вони змінюються при кожному релізі сайту.
Стабільні тільки `data-marker` та BEM-класи (`PricesRange__start`, `ProductAbout__…`).

Діапазон `мін—макс` у списку означає мінімальну та максимальну ціну серед магазинів.
Розбивку по магазинах список не дає.

### 2.2 Ідентифікатор товару: `product_id`

`product_id` це хвіст URL після **останнього** `--`:
`/uk/products/riba-oseledets-maistri-smaku--novus02877258000000/` → `novus02877258000000`.

API сайту (поле `mainEan` у GraphQL) використовує той самий рядок як головний ID товару,
тож це первинний ключ.

Похідні поля:
- `id_type = "ean"`, якщо `^\d{14}$`. Тоді `ean13 = product_id[1:]` (провідний 0 + EAN-13).
  Перевіряти контрольну цифру EAN-13 і зберігати результат у `ean13_valid`.
- `id_type = "chain"`, якщо `^([a-z]+)(\d{14})$` і група 1 є `id` мережі з таблиці `chains`.
  Тоді `chain_id = група 1`. Це вагові або власні товари однієї мережі.
- інакше `id_type = "other"` (логувати).

Slug (частина до `--`) **не** є ключем: він генерується з назви і може змінитися.

### 2.3 Сторінка товару

URL: `https://zakaz.ua/uk/products/<slug>--<product_id>/`.

| Поле | Селектор / правило |
|---|---|
| назва | `h1` |
| хлібні крихти | `a[data-marker^="Breadcrumb Category"]`; `Category 0` → головна (пропустити), `1` → рівень 1, `2` → рівень 2, `3` → рівень 3. Slug = останній сегмент шляху `href` (`/uk/kyiv/low-salted-fish-zakaz/` → `low-salted-fish-zakaz`) |
| характеристики | кожен `li[data-marker^="Taxon "]`: ключ = текст після `"Taxon "` (`tm`, `pr`, `weight`, `shelf_life`, `trange`, `country`, `genus_species`, можуть бути й інші), назва = `.ProductAbout__entryTitle`, значення = `.ProductAbout__entryValue` |
| склад | `.ProductAbout__item`, у якого `.ProductAbout__itemTitle` = `"Склад"`; значення = текст блоку без заголовка |
| КБЖУ | `.ProductAbout__item`, у якого заголовок починається з `"Харчові властивості"`; пари `li` → `.ProductAbout__entryTitle` / `.ProductAbout__entryValue` (`"Калорійність"` → `"242.00ккал"`, `"Білки"` → `"17.70г"`, `"Жири"`, `"Вуглеводи"`, якщо є) |
| опис (опційно) | `[data-marker="About product"] .Truncate__content` |

Невідомі ключі `Taxon` зберігати в загальній таблиці атрибутів, а не відкидати.

**Важливе застереження.** Фікстура сторінки товару це DOM, який зберіг браузер **після**
виконання JavaScript. Сервер без браузера може віддавати інший HTML. Відомо, що блок цін
по мережах (`[data-marker="Available Retails"]`) підвантажується JavaScript-ом і в
серверному HTML його **немає** (перевірено `curl`). Чи є в серверному HTML хлібні крихти й
`Taxon`, ще **не перевірено**: на це є smoke-check (розділ 7).

### 2.4 GraphQL API (`https://stores-api.zakaz.ua/graphql`): тільки формат відповідей

Ми маємо **відповіді** трьох запитів, але не маємо повних запитів для товарів. Тому в цьому
етапі пишемо тільки **парсери відповідей** і таблиці під них. Жодних мережевих запитів до API.

1. `chains` (фікстура `graphql_chains.json`): 21 мережа, поля `id`, `name`, `logo`, `hostname`.
   `id` збігаються з префіксами `product_id` (`novus`, `metro`, `kharkiv`, `megamarket`, …).
2. `deliverySchedules.nearest` (фікстура `graphql_delivery_schedules.json`): 21 запис
   `chainId` + `storeId` + `slot.id`. Частина після `|` в `slot.id` є назвою зони
   (`Mostytsky`, `Obolon-All`, `Odesa-suvorovskii-B2`, …). Частина магазинів **не в Києві**
   (Одеса, Харків, Вінниця, Рівне…): для мереж, яких немає в Києві, сайт підставляє
   найближчий магазин з іншого міста. Місто з цих даних **не визначається автоматично**.
3. `uberProducts.details` (фікстура `graphql_uber_product_novus02877258000000.json`):
   ціни по магазинах для одного товару:
   ```json
   {"mainEan": "novus02877258000000", "presentInChains": ["novus"],
    "stores": [{"chainId": "novus", "storeId": "48201031", "available": true,
                "price": 19900, "fullPrice": 26900, "discountPercent": 26,
                "discountDateTo": "2026-10-28", "ean": "novus02877258000000"}]}
   ```
   `price` і `fullPrice` записані в **копійках** (19900 = 199.00 ₴, поточна; 26900 = 269.00 ₴, без знижки).

---

## 3. Обсяг робіт

### Робимо

- Структура проєкту, конфіг, CLI, SQLite-схема (усі таблиці з розділу 5, зокрема ті,
  що поки заповнюються лише з фікстур).
- HTTP-клієнт з паузами, повторами та аварійною зупинкою (розділ 6.1).
- Парсер сторінки списку + обхід пагінації всіх 9 підкатегорій і батьківської категорії.
- Парсер сторінки товару + обхід сторінок товарів (усіх або тільки нових).
- Парсери GraphQL-відповідей `chains`, `deliverySchedules`, `uberProducts` (тільки з файлів).
- Заповнення `chains` і `stores` з фікстур (команда `seed`).
- Оновлення `first_seen` / `last_seen` / `is_active`.
- Експорт у CSV і XLSX.
- Smoke-check на живому сайті перед масовим запуском.
- Тести на фікстурах, README з інструкцією та прикладом cron/launchd.

### Не робимо (залишити заглушки `NotImplementedError` + `TODO(unverified)`)

- **Запити до `stores-api.zakaz.ua/graphql`.** Невідомо, як передається список магазинів
  (змінні чи заголовки `X-Store-Ids` / `X-Chain-Ids` / `X-Delivery-Type`) і чи працює
  запит без cookie. Підготувати інтерфейс `StorePriceSource.fetch(product_ids) -> list[StorePrice]`
  з методом-заглушкою. Парсер відповіді вже є (розділ 6.5).
- Next.js RSC-запити (`?_rsc=…`).
- Playwright / headless-браузер.
- Парсинг блоку `Available Retails` (у серверному HTML його немає).
- Автоматичне визначення міста магазину.
- Обхід категорій рівня 3 зі сторінок рівня 2 (рівень 3 беремо тільки з хлібних крихт).
- Інші міста, окрім Києва (але `city` має бути параметром, а не константою в коді).
- Класифікація за УКТЗЕД, нормалізація виду / форми / обробки: це наступний етап.
- Завантаження зображень.
- Автоматичне встановлення розкладу (cron/launchd): лише приклад у README.

---

## 4. Структура проєкту

```
zakaz_fish/
├── pyproject.toml            # залежності: httpx, beautifulsoup4, lxml, openpyxl, pytest
├── config.example.toml
├── README.md
├── zakaz_fish/
│   ├── __init__.py
│   ├── __main__.py           # CLI (argparse)
│   ├── config.py             # читання config.toml (tomllib)
│   ├── http.py               # клієнт, паузи, повтори, аварійна зупинка
│   ├── ids.py                # product_id, id_type, EAN-13
│   ├── parse_listing.py
│   ├── parse_product.py
│   ├── parse_graphql.py      # chains, deliverySchedules, uberProducts
│   ├── prices.py             # "199.00" -> 19900, розбір ваги/одиниці
│   ├── db.py                 # схема, міграції, upsert
│   ├── crawl.py              # обхід списків і сторінок товарів
│   ├── sources.py            # StorePriceSource (заглушка)
│   ├── smoke.py
│   └── export.py
├── data/                     # у .gitignore
│   ├── zakaz_fish.sqlite
│   ├── raw/<run_id>/listing/<slug>_p<N>.html.gz
│   ├── raw/<run_id>/product/<product_id>.html.gz
│   └── out/
└── tests/
    ├── fixtures/             # див. tests/fixtures/README.md
    └── test_*.py
```

Конфіг (`config.example.toml`):

```toml
city = "kyiv"
lang = "uk"
root_category = "fish-and-seafood-zakaz"
db_path = "data/zakaz_fish.sqlite"
raw_dir = "data/raw"
delay_min_s = 1.5
delay_max_s = 3.0
timeout_s = 30
max_pages_per_category = 100
user_agent = "KSEA-FishMarketResearch/0.1"
contact_email = ""            # заповнює користувач; додається в User-Agent як (+mailto:...)
store_descriptions = false
```

У `user_agent` не можна вживати слова, заборонені в robots.txt zakaz.ua: `wget`, `libwww`,
`fast`, `Fetch`, `HTTrack`, `WebCopier`, `Teleport`, `WebZIP`, `larbin`, `Zao`, `DOC`
та інші з того файлу.

---

## 5. Схема бази даних (SQLite)

Ціни завжди в копійках (`INTEGER`), час в ISO 8601 UTC.

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,            -- напр. 20261020T120000Z
  kind TEXT NOT NULL,                 -- listings | details | daily | seed | smoke
  city TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,               -- running | ok | stopped | failed
  stop_reason TEXT,
  stats_json TEXT
);

CREATE TABLE chains (
  chain_id TEXT PRIMARY KEY, name TEXT, hostname TEXT, logo_url TEXT
);

CREATE TABLE stores (
  store_id TEXT PRIMARY KEY,
  chain_id TEXT REFERENCES chains(chain_id),
  zone_hint TEXT,                     -- частина slot.id після '|', напр. 'Mostytsky'
  city TEXT,                          -- NULL, заповнюється вручну
  source TEXT                         -- 'delivery_schedules_fixture'
);

CREATE TABLE categories (
  slug TEXT PRIMARY KEY, name TEXT, level INTEGER, parent_slug TEXT
);

CREATE TABLE products (
  product_id TEXT PRIMARY KEY,
  id_type TEXT NOT NULL,              -- ean | chain | other
  ean13 TEXT, ean13_valid INTEGER,
  chain_id TEXT,                      -- для id_type='chain'
  url TEXT NOT NULL, slug TEXT,
  title TEXT,
  weight_text TEXT,                   -- як на сайті: 'за 1 кг', '250 г'
  unit_kind TEXT,                     -- per_kg | pack | unknown
  pack_qty REAL, pack_unit TEXT,      -- 250, 'г'
  image_url TEXT,
  brand TEXT, producer TEXT, country TEXT, species TEXT,
  shelf_life TEXT, storage_temp TEXT,
  composition TEXT,
  kcal_100g REAL, protein_100g REAL, fat_100g REAL, carbs_100g REAL,
  description TEXT,                   -- тільки якщо store_descriptions = true
  cat_l1 TEXT, cat_l2 TEXT, cat_l3 TEXT,  -- з хлібних крихт
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  details_scraped_at TEXT
);

CREATE TABLE product_categories (     -- M:N, бо товар буває в кількох категоріях
  product_id TEXT REFERENCES products(product_id),
  slug TEXT REFERENCES categories(slug),
  source TEXT NOT NULL,               -- listing | breadcrumb
  PRIMARY KEY (product_id, slug, source)
);

CREATE TABLE product_attributes (     -- усі Taxon, зокрема невідомі
  product_id TEXT REFERENCES products(product_id),
  attr_key TEXT, attr_title TEXT, attr_value TEXT,
  PRIMARY KEY (product_id, attr_key)
);

CREATE TABLE listing_prices (         -- знімок зі сторінок списку
  run_id TEXT REFERENCES runs(run_id),
  product_id TEXT REFERENCES products(product_id),
  city TEXT NOT NULL,
  category_slug TEXT NOT NULL,
  scraped_at TEXT NOT NULL,
  price_min_kop INTEGER, price_max_kop INTEGER,
  is_promo INTEGER,
  page INTEGER, position INTEGER,
  PRIMARY KEY (run_id, product_id, category_slug)
);

CREATE TABLE store_prices (           -- заповнюється з uberProducts (поки лише з фікстур/тестів)
  run_id TEXT, product_id TEXT, scraped_at TEXT,
  chain_id TEXT, store_id TEXT,
  available INTEGER,
  price_kop INTEGER, full_price_kop INTEGER,
  discount_percent INTEGER, discount_date_to TEXT,
  store_ean TEXT,
  PRIMARY KEY (run_id, product_id, store_id)
);
```

---

## 6. Модулі

### 6.1 `http.py`

- `httpx.Client(follow_redirects=True, timeout=…)`, заголовки: `User-Agent` з конфігу
  (+ `(+mailto:…)`, якщо задано), `Accept-Language: uk`, `Accept: text/html`.
- Перед кожним запитом випадкова пауза `delay_min_s..delay_max_s`.
- Повтор до 3 разів з експоненційною паузою (5, 15, 45 с) тільки для мережевих помилок,
  таймаутів і 500/502/504.
- **Аварійна зупинка** (виняток `StopCrawl`): статус 403, 429, 503 або тіло містить
  `Just a moment`, `cf-chl`, `challenge-platform`. Run позначається `stopped` з причиною,
  уже зібрані дані зберігаються.
- Кожну успішну відповідь зберігати в `raw/` (gzip) перед парсингом.
- Лічильник запитів і загальний час виводити в лог.

### 6.2 `ids.py`, `prices.py`

- `parse_product_url(url) -> (slug, product_id)`.
- `classify_id(product_id, known_chains) -> (id_type, ean13, ean13_valid, chain_id)`.
- `ean13_check(code13) -> bool`.
- `price_to_kop("199.00 ") -> 19900` (через `Decimal`, без float). Пробіли й `₴` прибрати.
- `parse_weight("за 1 кг") -> ("per_kg", 1, "кг")`; `"250 г" -> ("pack", 250, "г")`;
  також `мл`, `л`, `шт`, `кг` для фасованого; нерозпізнане → `("unknown", None, None)` + лог.

### 6.3 `parse_listing.py`

`parse_listing(html) -> ListingPage(total: int|None, tiles: list[Tile], subcategories: list[str])`.

`Tile`: `product_id, slug, url, title, price_min_kop, price_max_kop, weight_text, is_promo, image_url, position`.

### 6.4 `parse_product.py`

`parse_product(html, store_description: bool) -> ProductDetails`: назва, хлібні крихти
(список `(level, slug, name)`), атрибути (`dict key -> (title, value)`), склад, КБЖУ
(числа: `"242.00ккал" -> 242.0`, `"17.70г" -> 17.7`), опис (тільки якщо прапорець).

Відображення атрибутів у колонки `products`: `tm → brand`, `pr → producer`,
`country → country`, `genus_species → species`, `shelf_life → shelf_life`,
`trange → storage_temp`. Усі атрибути також пишуться в `product_attributes`.

### 6.5 `parse_graphql.py`

- `parse_chains(json) -> list[Chain]`.
- `parse_delivery_schedules(json) -> list[StoreRef(chain_id, store_id, zone_hint)]`.
- `parse_uber_product(json) -> (main_ean, present_in_chains, list[StorePrice])`.

Тільки читання словників, жодних запитів.

### 6.6 `crawl.py`

**`crawl_listings(city)`:**
1. Батьківська категорія `root_category` і 9 підкатегорій рівня 2. Список підкатегорій
   брати зі сторінки батьківської категорії; якщо він відрізняється від 9 відомих, писати
   в лог попередження і продовжувати з фактичним списком.
2. Для кожної категорії: сторінка 1 (без параметра), потім `?page=2…`. Стоп, коли карток 0,
   або жодного нового `product_id` у межах цієї категорії, або досягнуто `max_pages_per_category`.
3. Upsert у `products` (базові поля з картки), `product_categories(source='listing')`,
   рядок у `listing_prices`.
4. Після проходу порівняти кількість унікальних товарів батьківської категорії з `total`
   на її першій сторінці. Розбіжність понад 2% → попередження в `stats_json`.
5. Товари, що є в батьківській категорії, але в жодній підкатегорії → у лог (для контролю).

**`crawl_details(mode)`:** `mode = new | all | stale(days)`, `--limit N`. Для кожного
`product_id` завантажити сторінку товару, розпарсити, оновити `products`, `product_attributes`,
`product_categories(source='breadcrumb')`, `categories` (рівні 1–3 з хлібних крихт),
`details_scraped_at`.

**Активність:** `last_seen` оновлюється для кожного побаченого товару. `is_active = 0`
ставиться товарам, яких не бачили в цьому запуску, **тільки** якщо `crawl_listings`
завершився зі статусом `ok` для всіх категорій. Інакше можна помилково «видалити» товари
через збій мережі.

### 6.7 `smoke.py`

Див. розділ 7.

### 6.8 `export.py`

`export` → `data/out/`:
- `products.csv` (усі колонки `products`);
- `listing_prices_latest.csv` (останній `ok` запуск);
- `zakaz_fish.xlsx` з аркушами `products`, `listing_prices_latest`, `categories`, `chains`, `stores`.

CSV у UTF-8 з BOM (щоб Excel на macOS правильно показав кирилицю).

### 6.9 CLI

```
python -m zakaz_fish init-db
python -m zakaz_fish seed --chains tests/fixtures/graphql_chains.json \
                          --stores tests/fixtures/graphql_delivery_schedules.json
python -m zakaz_fish smoke-check
python -m zakaz_fish crawl-listings
python -m zakaz_fish crawl-details --mode new [--limit 20]
python -m zakaz_fish daily           # crawl-listings + crawl-details --mode new
python -m zakaz_fish export
python -m zakaz_fish stats           # к-сть товарів, по категоріях, по id_type, по мережах
```

---

## 7. Smoke-check (обов'язково перед першим масовим запуском)

Фікстури є DOM після JavaScript, а сервер може віддавати інше. Smoke-check робить
**3 запити**:

1. `https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/`: очікуємо ≥ 20 карток, у кожної є
   `product_id` і ціна; `total` розпізнано.
2. Те саме з `?page=2`: очікуємо ≥ 20 карток, і більшість `product_id` відрізняються від сторінки 1.
3. Сторінка першого товару зі списку: очікуємо ≥ 3 хлібні крихти й ≥ 3 `Taxon`.

Кожну відповідь зберегти в `tests/fixtures/live/` (щоб потім додати як фікстури) і вивести
таблицю «перевірка → OK/FAIL → що знайдено». Якщо хоч одна перевірка FAIL, `crawl-*`
відмовляються запускатися, доки не передати `--force`. Код **не намагається** «полагодити»
ситуацію сам (не вмикає браузер, не шукає інші ендпоінти).

---

## 8. Тести (pytest, тільки офлайн)

Значення нижче перевірені вручну на фікстурах.

**`listing_fish_and_seafood_p1.html`**
- `total == 1502`; карток 40; з `is_promo` 22; з діапазоном цін (`max > min`) 17.
- `id_type`: 27 `ean` (усі з валідною контрольною цифрою EAN-13) і 13 `chain`:
  `novus` 8, `metro` 2, `kharkiv` 2, `megamarket` 1.
- Перша картка: `product_id = "novus02877258000000"`,
  `title = "Оселедець Матіас норвезький без голови слабосолений"`,
  `price_min_kop = price_max_kop = 19900`, `weight_text = "за 1 кг"`, `is_promo = True`.
- Картка `04820088410548` («Оселедець Водний Світ філе слабосолене в олії 250г»):
  `price_min_kop = 8650`, `price_max_kop = 13150`, `weight_text = "250 г"` →
  `("pack", 250, "г")`.
- `subcategories` = 9 slug'ів з розділу 2.1 у тому ж порядку.

**`product_novus02877258000000.html`**
- `h1` = «Оселедець Матіас норвезький без голови слабосолений».
- Хлібні крихти: `(1, fish-and-seafood-zakaz, Риба та морепродукти)`,
  `(2, prepared-fish-zakaz, Риба приготована)`, `(3, low-salted-fish-zakaz, Солена риба)`
  (назву нормалізувати `strip()`, у джерелі є кінцевий пробіл).
- Атрибути: `tm=Майстри Смаку`, `pr=Власне виробництво`, `weight=Вагові`,
  `shelf_life=5 діб`, `trange=2...6`, `country=Україна`, `genus_species=оселедець`.
- Склад: `"оселедець, сіль кухонна харчова"`.
- КБЖУ: `kcal=242.0`, `protein=17.7`, `fat=19.5`, `carbs=None`.
- При `store_description=False` опис `None`; при `True` починається з «Що ж таке оселедець Матіас?».

**GraphQL-фікстури**
- `graphql_chains.json`: 21 мережа; `ekomarket.hostname == "eko.zakaz.ua"`.
- `graphql_delivery_schedules.json`: 21 запис; `novus → 48201031`, `zone_hint = "Mostytsky"`;
  `metro → 48215611`, `"Teremki-2"`.
- `graphql_uber_product_novus02877258000000.json`: `main_ean = "novus02877258000000"`,
  1 магазин: `chain_id="novus"`, `store_id="48201031"`, `available=True`, `price_kop=19900`,
  `full_price_kop=26900`, `discount_percent=26`, `discount_date_to="2026-10-28"`.

**Інше**
- `ids`: `ean13_check("4820088410548") is True`; зіпсована остання цифра → `False`.
- `prices`: `"199.00 " → 19900`, `"1 399.00" → 139900`, `"86.50" → 8650`.
- `db`: повторний upsert того самого товару не створює дубля; `is_active` не скидається,
  якщо run не `ok`.
- `http`: на мок-відповіді 403 і на тілі з `Just a moment` піднімається `StopCrawl`
  (використати `httpx.MockTransport`, без мережі).
- `crawl_listings` на мок-транспорті: зупинка пагінації на порожній сторінці.

---

## 9. Критерії приймання

1. `pytest` зелений без доступу до мережі.
2. `init-db`, `seed`, `stats`, `export` працюють на порожній базі та на базі після тестового прогону.
3. `smoke-check` виконує рівно 3 запити й виводить зрозумілий звіт.
4. `crawl-listings` і `crawl-details --mode new --limit 20` проходять на живому сайті
   (запускає користувач), після чого `stats` показує розумні числа.
5. У коді немає запитів до `stores-api.zakaz.ua`; `StorePriceSource.fetch` кидає
   `NotImplementedError("TODO(unverified): ...")`.
6. README містить: встановлення, конфіг, порядок першого запуску, приклад cron і launchd
   для `daily`, перелік TODO(unverified).

---

## 10. Порядок роботи

1. Скелет проєкту, `pyproject.toml`, конфіг, CLI-заглушки, `init-db` зі схемою.
2. `ids.py`, `prices.py` + тести.
3. `parse_listing.py` + тести на фікстурі.
4. `parse_product.py` + тести на фікстурі.
5. `parse_graphql.py` + `seed` + тести.
6. `http.py` + тести на `MockTransport`.
7. `crawl.py` (списки, деталі, активність) + тести на мок-транспорті.
8. `smoke.py`, `export.py`, `stats`.
9. README.

Після кожного кроку: `pytest`, короткий підсумок «що зроблено / що далі».
**Не запускай запити до живого сайту сам.** Live-запуски (`smoke-check`, `crawl-*`) робить
користувач.

---

## 11. Відкриті питання (наступні етапи, не реалізовувати)

- Повний запит `uberProducts` (operationName, query, variables, заголовки): як задаються
  магазини і чи можна запитати кілька товарів за раз.
- Як фіксувати місто / адресу / тип доставки для API (`X-Delivery-Type`, `X-Store-Ids`).
- Місто кожного `store_id` (поки вручну).
- Чи містить серверний HTML сторінки товару хлібні крихти та `Taxon` (з'ясує smoke-check).
- Категорії рівня 3 для повного дерева.
- Класифікація за УКТЗЕД (групи 03, 1604, 1605) і нормалізація: вид, форма, обробка.
- Письмова згода zakaz.ua на використання даних, якщо дослідження буде публічним.
