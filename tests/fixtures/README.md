# Фікстури

Збережено 20.10.2026 з браузера Chrome (Київ, без вказаної адреси доставки).

| Файл | Що це |
|---|---|
| `listing_fish_and_seafood_p1.html` | Сторінка 1 `https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/`. Видобуто з MHTML (quoted-printable → UTF-8). **DOM після JavaScript**, скриптів немає. |
| `product_novus02877258000000.html` | Сторінка товару `https://zakaz.ua/uk/products/riba-oseledets-maistri-smaku--novus02877258000000/`. Те саме: DOM після JavaScript. |
| `graphql_chains.json` | Відповідь `stores-api.zakaz.ua/graphql`, операція `UserRetailChainsBase` (мережі, кошики, акції доставки). |
| `graphql_delivery_schedules.json` | Відповідь з `deliverySchedules.nearest` (найближчий магазин кожної мережі + слот). |
| `graphql_uber_product_novus02877258000000.json` | Відповідь з `uberProducts.details` для оселедця Матіас (ціни по магазинах). Запит (payload) **невідомий**. |

Серверний HTML (те, що отримає `httpx`) може відрізнятися від цих файлів: див. smoke-check у SPEC.md.
