"""Список магазинів мережі і сторінка магазину. Перевірено на tests/fixtures/samples/*_stores_p1.html
та novus_store_detail.html (01.11.2026). Числового ID магазину і координат у HTML немає."""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

STORE_HREF_RE = re.compile(r"/uk/stores/([^/]+)/([^/]+)/?$")


@dataclass
class StoreTileInfo:
    store_url: str
    city_slug: str
    city_name: str | None
    address: str | None
    hours_text: str | None   # '08:00-22:00 пн-нд'


@dataclass
class StoreListPage:
    stores: list[StoreTileInfo] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)  # номери сторінок з пагінації (віджет може брехати)


@dataclass
class StoreDetail:
    city_name: str | None
    address: str | None
    hours_text: str | None
    services: list[str] = field(default_factory=list)


def _clean(node) -> str | None:
    return " ".join(node.get_text(" ", strip=True).split()) if node else None


def parse_store_list(html: str, base_url: str) -> StoreListPage:
    soup = BeautifulSoup(html, "lxml")
    page = StoreListPage()
    for a in soup.select('a[data-testid="store_tile_link"][href]'):
        m = STORE_HREF_RE.search(a["href"])
        if not m:
            continue
        # img alt = '<Мережа>, <Місто>, <Адреса>'
        img = a.select_one("img[alt]")
        parts = [p.strip() for p in img["alt"].split(", ", 2)] if img else []
        sched = _clean(a.select_one('[class*="StoreTile__workingSchedule"]'))
        page.stores.append(StoreTileInfo(
            store_url=urljoin(base_url, a["href"]), city_slug=m.group(1),
            city_name=parts[1] if len(parts) == 3 else None,
            address=_clean(a.select_one('[class*="StoreTile__title"]')),
            hours_text=sched,
        ))
    nums = {int(m.group(1)) for a in soup.select('a[href*="page="]')
            if (m := re.search(r"[?&]page=(\d+)", a["href"]))}
    page.page_numbers = sorted(nums)
    return page


def parse_store_detail(html: str) -> StoreDetail:
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.select_one('[class*="StorePage__title"]'))  # 'Київ, вул. Соборна, 2'
    city = address = None
    if title and ", " in title:
        city, address = title.split(", ", 1)
    hours = _clean(soup.select_one('[class*="StorePage__workingSchedule"]'))
    services = [_clean(e) for e in soup.select('[class*="StoreAttributes__item"]')]
    return StoreDetail(city_name=city, address=address, hours_text=hours, services=[s for s in services if s])
