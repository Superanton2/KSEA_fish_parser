import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .ids import parse_product_url
from .prices import price_to_kop


@dataclass
class Tile:
    product_id: str
    slug: str
    url: str
    title: str
    price_min_kop: int | None
    price_max_kop: int | None
    weight_text: str | None
    is_promo: bool
    image_url: str | None
    position: int


@dataclass
class ListingPage:
    total: int | None
    tiles: list[Tile] = field(default_factory=list)
    subcategories: list[str] = field(default_factory=list)


def _text(node) -> str | None:
    return node.get_text(strip=True) if node else None


def _category_slug(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1]


def parse_listing(html: str) -> ListingPage:
    soup = BeautifulSoup(html, "lxml")

    total = None
    head = soup.select_one('[data-marker="Products"] .ProductBox__head p')
    if head:
        m = re.search(r"\d+", head.get_text().replace(" ", "").replace(" ", ""))
        total = int(m.group()) if m else None

    tiles: list[Tile] = []
    for a in soup.select('[data-marker="Products List"] a[href*="/uk/products/"]'):
        if not a.select_one('[data-marker="CatalogProductTile"]'):
            continue
        href = a["href"]
        slug, product_id = parse_product_url(href)
        start = _text(a.select_one(".PricesRange__start"))
        end = _text(a.select_one(".PricesRange__end"))
        price_min = price_to_kop(start) if start else None
        price_max = price_to_kop(end) if end else price_min
        img = a.select_one("img[src]")
        tiles.append(Tile(
            product_id=product_id,
            slug=slug,
            url=href,
            title=(a.get("title") or "").strip(),
            price_min_kop=price_min,
            price_max_kop=price_max,
            weight_text=_text(a.select_one('[data-marker="Weight"]')),
            is_promo=a.select_one('[data-marker="Offer"]') is not None,
            image_url=img["src"] if img else None,
            position=len(tiles) + 1,
        ))

    subcategories = [_category_slug(a["href"]) for a in soup.select(".CategoriesSlider__item a[href]")]
    return ListingPage(total=total, tiles=tiles, subcategories=subcategories)
