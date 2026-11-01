"""Словник даних пакета знімка (SPEC v2, 9.5): таблиця, колонка, тип, джерело, опис, приклад.

Єдине джерело для data_dictionary.csv, DATA_DICTIONARY.md пакета й аркуша XLSX. Тест перевіряє, що колонки
кожної експортованої таблиці збігаються зі словником. Попередній словник (етап 1) лишається в dictionary.py.
"""

# рядок: таблиця | колонка | тип | джерело | опис | приклад
_RAW = """
products|product_id|text|сторінка|Ідентифікатор товару на zakaz.ua, ключ. Хвіст URL після останнього «--»: 14 цифр із провідним 0 (штрихкод) або id мережі + 14 цифр.|04820185100281
products|id_type|text|похідне|ean (штрихкод), chain (власний/ваговий товар мережі), other (не розпізнано).|ean
products|ean13|text|похідне|Штрихкод EAN-13 (product_id без провідного 0). Лише для id_type = ean.|4820185100281
products|ean13_valid|integer|похідне|1, якщо контрольна цифра EAN-13 сходиться. Невалідні (≈6%, переважно 800…) це, імовірно, внутрішні коди.|1
products|gs1_prefix|text|похідне|Перші 3 цифри ean13, лише коли ean13_valid = 1.|482
products|gs1_member_org|text|похідне|Організація/країна, де власник штрихкоду зареєстрував його в GS1. НЕ країна походження риби. 200–299 = in-store.|Україна
products|chain_id_from_id|text|похідне|Мережа з префікса product_id (лише для id_type = chain).|novus
products|title|text|сторінка|Назва товару, як на сайті.|Форель Norven філе-шматок 180г
products|brand|text|сторінка|Торгова марка («Без ТМ» = марки немає). Порожнє = на сайті не вказано.|Norven
products|producer|text|сторінка|Виробник; часто порожній.|ТОВ "Юніверсал Фіш Компані"
products|country|text|сторінка|Країна-виробник (за словами продавця). Не обов'язково країна походження сировини.|Україна
products|species|text|сторінка|Вид риби/морепродукту. Порожній приблизно в третині товарів.|форель
products|weight_text|text|сторінка|Вага/одиниця з картки: «за 1 кг», «250 г». Може бути порожнім.|180 г
products|unit_kind|text|похідне|per_kg (ціна вже за кг), pack (за упаковку), unknown.|pack
products|pack_qty|number|похідне|Розмір упаковки числом.|180
products|pack_unit|text|похідне|Одиниця розміру: г, кг, мл, л, шт.|г
products|pack_g|number|похідне|Маса упаковки в грамах (лише для г і кг); для мл, л, шт порожньо.|180
products|composition|text|сторінка|Склад з етикетки.|Форель, сіль кухонна.
products|kcal_100g|number|сторінка|Калорійність на 100 г, ккал.|202
products|protein_100g|number|сторінка|Білки на 100 г, г.|22.5
products|fat_100g|number|сторінка|Жири на 100 г, г.|12.5
products|carbs_100g|number|сторінка|Вуглеводи на 100 г, г.|0
products|cat_l1_slug|text|сторінка|Категорія рівня 1 з хлібних крихт картки (slug).|fish-and-seafood-zakaz
products|cat_l1_name|text|довідник|Назва категорії рівня 1.|Риба та морепродукти
products|cat_l2_slug|text|сторінка|Категорія рівня 2 (slug).|prepared-fish-zakaz
products|cat_l2_name|text|довідник|Назва категорії рівня 2.|Риба приготована
products|cat_l3_slug|text|сторінка|Категорія рівня 3 (slug); може бути порожньою.|low-salted-fish-zakaz
products|cat_l3_name|text|довідник|Назва категорії рівня 3.|Солена риба
products|scopes|text|похідне|Розділи знімка, де товар знайдено (core, canned, snacks, frozen, semi), через «; ».|core; frozen
products|fish_keyword_match|integer|похідне|1, якщо назва містить основу з fish_keywords. Евристика для відсіву нерибних товарів, не класифікація.|1
products|first_source|text|похідне|aggregator (каталог міста) або storefront (товар лише на вітрині мережі).|aggregator
products|n_cities|integer|похідне|У скількох містах знімка товар є в каталозі.|9
products|cities|text|похідне|Міста (city_slug) через «; ».|kyiv; lviv
products|n_chains_storefront|integer|похідне|На скількох вітринах мереж товар є.|3
products|chains_storefront|text|похідне|Мережі (chain_id) через «; ».|metro; novus
products|meta_price_min_uah|number|сторінка|Мінімальна ціна з meta description картки, грн (діапазон по всіх магазинах, які бачить сайт).|179.00
products|meta_price_max_uah|number|сторінка|Максимальна ціна з meta description, грн.|450.36
products|meta_chains|text|сторінка|Мережі, де товар є за meta description (назви як на сайті, зіставлені з chains), через «; ».|Auchan; NOVUS
products|details_status|text|похідне|Результат завантаження картки: ok, empty, not_found, error. Порожнє = картку не завантажували.|ok
products|details_scraped_at|datetime|похідне|Коли завантажено картку (UTC, ISO 8601).|2026-11-05T12:00:00Z
products|product_url|text|сторінка|Посилання на картку товару на zakaz.ua.|https://zakaz.ua/uk/products/file-forel-norven-180g--04820185100281/
products|image_url|text|сторінка|Посилання на фото (файли не завантажувались).|https://img3.zakaz.ua/…
product_attributes|product_id|text|сторінка|Товар, зв'язок із products.product_id.|04820185100281
product_attributes|attr_key|text|сторінка|Ключ характеристики на сайті (tm, pr, country, genus_species, method…), включно з невідомими.|method
product_attributes|attr_title|text|сторінка|Назва характеристики українською.|Метод
product_attributes|attr_value|text|сторінка|Значення характеристики.|консервована
city_offers|snapshot_id|text|похідне|Знімок.|2026-11-05
city_offers|city_slug|text|сторінка|Місто (slug з URL zakaz.ua).|lviv
city_offers|city_name|text|сторінка|Назва міста з заголовка сторінки.|Львів
city_offers|product_id|text|сторінка|Товар.|04820185100281
city_offers|scopes|text|похідне|Розділи, де товар є в каталозі цього міста, через «; ».|core
city_offers|categories|text|сторінка|Категорії каталогу міста (slug), де знайдено товар, через «; ».|fish-and-seafood-zakaz; prepared-fish-zakaz
city_offers|price_min_uah|number|сторінка|Мінімальна ціна серед магазинів, які сайт показує гостю цього міста, грн. Може містити магазини інших міст (див. supply_hint).|179.00
city_offers|price_max_uah|number|сторінка|Максимальна ціна серед цих магазинів, грн. Дорівнює мінімальній, якщо діапазону немає.|450.36
city_offers|price_min_per_kg_uah|number|похідне|Мінімальна ціна за кг, грн. Для per_kg це сама ціна; для г і кг ціна/маса; для шт, мл, л порожньо.|994.44
city_offers|price_max_per_kg_uah|number|похідне|Максимальна ціна за кг, грн.|2501.99
city_offers|is_promo|integer|сторінка|1, якщо хоча б в одному магазині діє акція (позначка на картці).|1
city_offers|n_listing_rows|integer|похідне|Скільки рядків каталогу склалося в цей запис (товар у кількох категоріях).|2
city_offers|price_conflict|integer|похідне|1, якщо рядки одного товару в одному місті мають різні ціни.|0
city_offers|scraped_at_first|datetime|похідне|Перша дата зняття ціни (UTC).|2026-11-05T10:00:00Z
city_offers|scraped_at_last|datetime|похідне|Остання дата зняття ціни (UTC).|2026-11-05T10:20:00Z
city_offers|chain_has_store_in_city|integer|похідне|Для товарів мережі (id_type = chain): 1/0, чи має мережа магазини в місті. Порожньо, якщо довідника мережі немає.|1
city_offers|in_national_set|integer|похідне|1, якщо товар належить «національному набору» (є в усіх містах-еталонах). Порожньо, якщо набір не визначено.|0
city_offers|local_carrier_chains|text|похідне|Мережі, які продають товар (вітрина, meta, префікс id) і мають магазини в місті, через «; ».|novus
city_offers|supply_hint|text|похідне|Підказка: local, remote, national, local_or_national, unknown. ПІДКАЗКА, а не факт: сайт не вказує, з якого магазину ціна.|local
chain_offers|snapshot_id|text|похідне|Знімок.|2026-11-05
chain_offers|chain_id|text|довідник|Мережа.|novus
chain_offers|chain_name|text|довідник|Назва мережі.|NOVUS
chain_offers|product_id|text|сторінка|Товар.|04820185100281
chain_offers|scopes|text|похідне|Розділи, де товар є у вітрині мережі, через «; ».|core
chain_offers|categories|text|сторінка|Категорії вітрини (slug), де знайдено товар.|fish-and-seafood-novus
chain_offers|price_uah|number|сторінка|Поточна ціна на вітрині мережі, грн (контекст магазину за замовчуванням; сайт не вказує, якого).|179.00
chain_offers|old_price_uah|number|сторінка|Стара ціна (до знижки), грн.|409.00
chain_offers|discount_percent|integer|сторінка|Знижка, %.|56
chain_offers|discount_until|date|похідне|Дата завершення акції (з «до DD.MM» та року знімка).|2026-11-02
chain_offers|price_per_kg_uah|number|похідне|Ціна за кг, грн (правила як у city_offers).|994.44
chain_offers|scraped_at|datetime|похідне|Коли зняли ціну (UTC).|2026-11-05T11:00:00Z
city_chain_presence|snapshot_id|text|похідне|Знімок.|2026-11-05
city_chain_presence|city_slug|text|сторінка|Місто.|lviv
city_chain_presence|city_name|text|сторінка|Назва міста.|Львів
city_chain_presence|chain_id|text|довідник|Мережа.|novus
city_chain_presence|chain_name|text|довідник|Назва мережі.|NOVUS
city_chain_presence|n_stores_in_city|integer|сторінка|Скільки магазинів мережі є в місті за довідником магазинів.|0
city_chain_presence|n_chain_id_products_in_listing|integer|похідне|Скільки товарів із id цієї мережі є в каталозі міста.|8
city_chain_presence|n_listing_products_carried|integer|похідне|Скільки товарів каталогу міста є у вітрині цієї мережі.|25
city_chain_presence|presence|text|похідне|local (є магазини), remote (магазинів немає, але товари є в каталозі), absent.|remote
stores|chain_id|text|довідник|Мережа.|novus
stores|chain_name|text|довідник|Назва мережі.|NOVUS
stores|city_slug|text|сторінка|Місто (slug).|kyiv
stores|city_name|text|сторінка|Назва міста.|Київ
stores|address|text|сторінка|Адреса.|вул. Соборна, 2
stores|hours_text|text|сторінка|Графік роботи як на сайті.|08:00-22:00 пн-нд
stores|services|text|сторінка|Послуги зі сторінки магазину через «; ». Порожньо, якщо сторінку магазину не завантажували.|Парковка; Жива риба
stores|has_live_fish|integer|похідне|1, якщо серед послуг є «Жива риба»; порожньо, якщо послуг не знаємо.|1
stores|store_url|text|сторінка|Сторінка магазину на вітрині мережі. Числового ID магазину і координат у HTML немає.|https://novus.zakaz.ua/uk/stores/kyiv/vul-soborna-2/
stores|last_seen_snapshot|text|похідне|Останній знімок, де магазин був у списку.|2026-11-05
cities|city_slug|text|сторінка|Місто (slug).|lviv
cities|city_name|text|сторінка|Назва міста.|Львів
cities|source|text|похідне|selector (зі списку міст), manual (додано вручну).|selector
cities|is_valid|integer|похідне|1/0 для перевірених міст; порожньо для міст зі списку.|
cities|is_baseline|integer|похідне|1, якщо місто використано як еталон «національного набору».|0
cities|site_total_core|integer|сторінка|«NNN товарів» на сайті, розділ core (риба та морепродукти).|758
cities|site_total_canned|integer|сторінка|«NNN товарів» на сайті, розділ canned (рибна консервація).|229
cities|site_total_snacks|integer|сторінка|«NNN товарів», розділ snacks (рибні снеки).|40
cities|site_total_frozen|integer|сторінка|«NNN товарів», розділ frozen (риба заморожена).|120
cities|site_total_semi|integer|сторінка|«NNN товарів», розділ semi (напівфабрикати; рахується все, у знімку лише рибні).|300
cities|seen_core|integer|похідне|Унікальних товарів зібрано, розділ core.|755
cities|seen_canned|integer|похідне|Унікальних товарів зібрано, розділ canned.|229
cities|seen_snacks|integer|похідне|Унікальних товарів зібрано, розділ snacks.|40
cities|seen_frozen|integer|похідне|Унікальних товарів зібрано, розділ frozen.|120
cities|seen_semi|integer|похідне|Унікальних товарів зібрано, розділ semi (лише рядки з рибними назвами).|15
cities|n_chains_with_stores|integer|похідне|Скільки мереж мають магазини в місті.|3
cities|chains_with_stores|text|похідне|Мережі з магазинами в місті через «; ».|metro; novus
chains|chain_id|text|довідник|Мережа, ключ.|novus
chains|name|text|довідник|Назва мережі.|NOVUS
chains|hostname|text|довідник|Вітрина мережі на zakaz.ua.|novus.zakaz.ua
chains|storefront_status|text|похідне|ok, unavailable, robots_disallowed, excluded.|ok
chains|n_products_storefront|integer|похідне|Скільки рибних товарів зібрано з вітрини.|120
chains|n_stores|integer|похідне|Скільки магазинів у довіднику.|81
chains|cities_with_stores|text|похідне|Міста (slug) із магазинами через «; ».|kyiv; lviv
categories|source|text|похідне|aggregator або storefront:<мережа>.|aggregator
categories|slug|text|сторінка|Ідентифікатор категорії в URL.|canned-fish-zakaz
categories|name|text|сторінка|Назва категорії.|Рибна консервація
categories|level|integer|похідне|Рівень у дереві (1 верхній).|1
categories|parent_slug|text|сторінка|Батьківська категорія.|tins-jars-cooking-zakaz
categories|scope|text|похідне|Розділ знімка; порожньо, якщо категорію знайдено лише на картках товарів.|canned
flat_city_offers|city_name|text|сторінка|Місто.|Львів
flat_city_offers|title|text|сторінка|Назва товару.|Форель Norven філе-шматок 180г
flat_city_offers|brand|text|сторінка|Торгова марка.|Norven
flat_city_offers|producer|text|сторінка|Виробник.|ТОВ "Юніверсал Фіш Компані"
flat_city_offers|country|text|сторінка|Країна-виробник.|Україна
flat_city_offers|species|text|сторінка|Вид.|форель
flat_city_offers|cat_l2_name|text|довідник|Категорія рівня 2.|Риба приготована
flat_city_offers|cat_l3_name|text|довідник|Категорія рівня 3.|Солена риба
flat_city_offers|scopes|text|похідне|Розділи знімка.|core
flat_city_offers|weight_text|text|сторінка|Вага/одиниця.|180 г
flat_city_offers|price_min_uah|number|сторінка|Мінімальна ціна, грн.|179.00
flat_city_offers|price_max_uah|number|сторінка|Максимальна ціна, грн.|450.36
flat_city_offers|price_min_per_kg_uah|number|похідне|Мінімальна ціна за кг, грн.|994.44
flat_city_offers|price_max_per_kg_uah|number|похідне|Максимальна ціна за кг, грн.|2501.99
flat_city_offers|is_promo|integer|сторінка|1 = є акція.|1
flat_city_offers|supply_hint|text|похідне|Підказка про постачання (див. city_offers).|local
flat_city_offers|fish_keyword_match|integer|похідне|1 = назва схожа на рибну.|1
flat_city_offers|ean13|text|похідне|Штрихкод.|4820185100281
flat_city_offers|product_id|text|сторінка|Ідентифікатор товару.|04820185100281
flat_city_offers|product_url|text|сторінка|Посилання на товар.|https://zakaz.ua/uk/products/…
flat_chain_offers|chain_name|text|довідник|Мережа.|NOVUS
flat_chain_offers|title|text|сторінка|Назва товару.|Форель Norven філе-шматок 180г
flat_chain_offers|brand|text|сторінка|Торгова марка.|Norven
flat_chain_offers|producer|text|сторінка|Виробник.|ТОВ "Юніверсал Фіш Компані"
flat_chain_offers|country|text|сторінка|Країна-виробник.|Україна
flat_chain_offers|species|text|сторінка|Вид.|форель
flat_chain_offers|cat_l2_name|text|довідник|Категорія рівня 2.|Риба приготована
flat_chain_offers|scopes|text|похідне|Розділи знімка.|core
flat_chain_offers|weight_text|text|сторінка|Вага/одиниця.|180 г
flat_chain_offers|price_uah|number|сторінка|Ціна на вітрині, грн.|179.00
flat_chain_offers|old_price_uah|number|сторінка|Стара ціна, грн.|409.00
flat_chain_offers|discount_percent|integer|сторінка|Знижка, %.|56
flat_chain_offers|discount_until|date|похідне|Дата завершення акції.|2026-11-02
flat_chain_offers|price_per_kg_uah|number|похідне|Ціна за кг, грн.|994.44
flat_chain_offers|fish_keyword_match|integer|похідне|1 = назва схожа на рибну.|1
flat_chain_offers|ean13|text|похідне|Штрихкод.|4820185100281
flat_chain_offers|product_id|text|сторінка|Ідентифікатор товару.|04820185100281
flat_chain_offers|product_url|text|сторінка|Посилання на товар.|https://zakaz.ua/uk/products/…
snapshot_meta|key|text|похідне|Назва параметра знімка.|prices_window_start
snapshot_meta|value|text|похідне|Значення: вікна збору (UTC), к-сть запитів, міст, мереж, магазинів, версія коду, конфіг без email, підсумок robots.|2026-11-05T10:00:00Z
"""

FIELDS_V2: list[tuple[str, str, str, str, str, str]] = [
    tuple(part.strip() for part in line.split("|")) for line in _RAW.strip().splitlines()]
assert all(len(f) == 6 for f in FIELDS_V2), [f for f in FIELDS_V2 if len(f) != 6]

TABLE_ORDER = list(dict.fromkeys(f[0] for f in FIELDS_V2))

NOTES_V2 = [
    "Один знімок = одна дата збору; усі таблиці стосуються одного snapshot_id.",
    "Ціни в гривнях (2 знаки), час у UTC ISO 8601. Порожнє значення означає «на сайті цього немає» або «не обчислюється», а не нуль.",
    "Ціни міста (city_offers) це діапазон по магазинах, які сайт показує гостю без адреси; вони можуть містити магазини інших міст.",
    "Ціни по окремих магазинах не зібрані.",
]


def render_markdown_v2() -> str:
    lines = ["# Словник даних пакета знімка zakaz_fish", "",
             "Згенеровано з `zakaz_fish/dictionary_v2.py`, не редагуйте вручну.", "", "## Загальні примітки", ""]
    lines += [f"- {n}" for n in NOTES_V2]
    for t in TABLE_ORDER:
        lines += ["", f"## Таблиця `{t}`", "", "| Колонка | Тип | Джерело | Опис | Приклад |", "|---|---|---|---|---|"]
        lines += [f"| `{c}` | {ty} | {src} | {d.replace('|', '/')} | {ex.replace('|', '/')} |"
                  for tt, c, ty, src, d, ex in FIELDS_V2 if tt == t]
    return "\n".join(lines) + "\n"
