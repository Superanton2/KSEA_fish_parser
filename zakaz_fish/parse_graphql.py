"""Парсери ВІДПОВІДЕЙ stores-api.zakaz.ua/graphql. Запитів до API тут немає."""
from dataclasses import dataclass


@dataclass
class Chain:
    chain_id: str
    name: str | None
    hostname: str | None
    logo_url: str | None


@dataclass
class StoreRef:
    chain_id: str
    store_id: str
    zone_hint: str | None


@dataclass
class StorePrice:
    chain_id: str
    store_id: str
    available: bool
    price_kop: int | None
    full_price_kop: int | None
    discount_percent: int | None
    discount_date_to: str | None
    store_ean: str | None


def parse_chains(data: dict) -> list[Chain]:
    return [Chain(chain_id=c["id"], name=c.get("name"), hostname=c.get("hostname"),
                  logo_url=c.get("logo"))
            for c in data["data"]["chains"]]


def parse_delivery_schedules(data: dict) -> list[StoreRef]:
    refs = []
    for n in data["data"]["deliverySchedules"]["nearest"]:
        slot_id = (n.get("slot") or {}).get("id") or ""
        zone = slot_id.split("|", 1)[1] if "|" in slot_id else None
        refs.append(StoreRef(chain_id=n["chainId"], store_id=n["storeId"], zone_hint=zone))
    return refs


def parse_uber_product(data: dict) -> tuple[str, list[str], list[StorePrice]]:
    details = data["data"]["uberProducts"]["details"]
    stores = [StorePrice(
        chain_id=s["chainId"],
        store_id=s["storeId"],
        available=bool(s.get("available")),
        price_kop=s.get("price"),
        full_price_kop=s.get("fullPrice"),
        discount_percent=s.get("discountPercent"),
        discount_date_to=s.get("discountDateTo"),
        store_ean=s.get("ean"),
    ) for s in details.get("stores") or []]
    return details["mainEan"], list(details.get("presentInChains") or []), stores
