from .parse_graphql import StorePrice


class StorePriceSource:
    """Джерело цін по магазинах (GraphQL uberProducts)."""

    def fetch(self, product_ids: list[str]) -> list[StorePrice]:
        # TODO(unverified): запити до stores-api.zakaz.ua/graphql не реалізовано. Невідомо, як
        # передається список магазинів (змінні чи заголовки X-Store-Ids / X-Chain-Ids /
        # X-Delivery-Type) і чи працює запит без cookie. Парсер відповіді: parse_graphql.parse_uber_product.
        raise NotImplementedError(
            "TODO(unverified): запити до stores-api.zakaz.ua/graphql не реалізовано; "
            "невідомі повний запит uberProducts, спосіб передачі магазинів і потреба в cookie")
