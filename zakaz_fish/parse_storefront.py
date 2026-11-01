"""Сторінки вітрини мережі (<мережа>.zakaz.ua): головна й каталог категорії. Селектори перевірено на
tests/fixtures/samples/{novus,metro,tavriav}_*.html (01.11.2026)."""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .ids import parse_product_url
from .prices import price_to_kop

DISCOUNT_RE = re.compile(r"(\d+)")
UNTIL_RE = re.compile(r"до\s+(\d{1,2})\.(\d{1,2})")


@dataclass
class StoreCategory:
    slug: str
    name: str
    url: str


@dataclass
class StoreTile:
    product_id: str
    slug: str
    product_path: str       # /uk/products/<slug>--<id>/ (відкривається і на zakaz.ua)
    title: str
    price_kop: int | None
    old_price_kop: int | None
    discount_percent: int | None
    discount_until_text: str | None   # як на сайті: 'до 02.11'
    weight_text: str | None
    position: int


@dataclass
class StorefrontListing:
    h1: str | None
    total: int | None
    tiles: list[StoreTile] = field(default_factory=list)
    subcategories: list[StoreCategory] = field(default_factory=list)


def _slug(href: str) -> str:
    return href.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _clean(node) -> str | None:
    return " ".join(node.get_text(" ", strip=True).split()) if node else None


def _price(node) -> int | None:
    cap = node.select_one('[class*="Price__value"]') if node else None
    return price_to_kop(cap.get_text(strip=True)) if cap else None


def parse_storefront_home(html: str, base_url: str) -> list[StoreCategory]:
    """Верхні категорії головної: усі посилання /uk/categories/<slug>/ з текстом (унікальні за slug)."""
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, StoreCategory] = {}
    for a in soup.select('a[href*="/uk/categories/"]'):
        name = _clean(a)
        slug = _slug(a["href"])
        if name and slug and slug not in seen:
            seen[slug] = StoreCategory(slug, name, urljoin(base_url, a["href"]))
    return list(seen.values())


def parse_storefront_listing(html: str, base_url: str) -> StorefrontListing:
    soup = BeautifulSoup(html, "lxml")
    total = None
    gn = soup.select_one('[data-marker="Goods Number"]')
    if gn:
        m = re.search(r"\d+", gn.get_text().replace(" ", "").replace(" ", ""))
        total = int(m.group()) if m else None

    tiles: list[StoreTile] = []
    for a in soup.select('a[data-marker="Product Tile"][href*="/uk/products/"]'):
        slug, product_id = parse_product_url(urljoin(base_url, a["href"]))
        disc = a.select_one('[data-marker="Discount"]')
        dm = DISCOUNT_RE.search(disc.get_text()) if disc else None
        until = _clean(a.select_one('[data-marker="Promotion_until_date"]'))
        old = _price(a.select_one('[data-marker="Old Price"]'))
        price = _price(a.select_one('[data-marker="Discounted Price"]'))
        tiles.append(StoreTile(
            product_id=product_id, slug=slug, product_path=a["href"],
            title=(a.get("title") or "").strip(),
            price_kop=price, old_price_kop=old,
            discount_percent=int(dm.group(1)) if dm else None,
            discount_until_text=until,
            weight_text=_clean(a.select_one('[data-marker="Weight"]')),
            position=len(tiles) + 1,
        ))

    subs = [StoreCategory(_slug(a["href"]), _clean(a) or "", urljoin(base_url, a["href"]))
            for a in soup.select('[data-marker="categories slider"] a[href]')]
    return StorefrontListing(h1=_clean(soup.h1), total=total, tiles=tiles, subcategories=subs)
