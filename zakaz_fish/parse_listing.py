import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .ids import parse_product_url
from .prices import price_to_kop

BASE_URL = "https://zakaz.ua"


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
    in_stock: bool = True  # False: плитка «Немає в наявності» (без ціни)


@dataclass
class ListingPage:
    total: int | None
    tiles: list[Tile] = field(default_factory=list)
    subcategories: list[str] = field(default_factory=list)
    h1: str | None = None
    city_name: str | None = None  # з H1 «<Категорія> у місті <Місто>»
    city_slug: str | None = None  # місто з хлібної крихти «Breadcrumb Category 1» (/uk/<slug>/)
    tags_all_slug: str | None = None  # посилання «Усі» у блоці CategoriesTags (категорія, чиї це підрозділи)



def _text(node) -> str | None:
    return node.get_text(strip=True) if node else None


def _category_slug(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1]


CITY_RE = re.compile(r"\s+у місті\s+(.+)$")


def parse_listing(html: str, category_slug: str | None = None) -> ListingPage:
    """category_slug: поточна категорія. Теги `CategoriesTags` вважаються підрозділами, лише якщо їхнє
    посилання «Усі» веде на неї (інакше це сусідні категорії батька, напр. у «Рибних снеках»)."""
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
        href = urljoin(BASE_URL, a["href"])
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
            image_url=urljoin(BASE_URL, img["src"]) if img else None,
            position=len(tiles) + 1,
            in_stock=price_min is not None or "Немає в наявності" not in a.get_text(),
        ))

    subcategories = [_category_slug(a["href"]) for a in soup.select(".CategoriesSlider__item a[href]")]
    all_slug = None
    tags = []
    for a in soup.select('[data-marker="Categories"] .CategoriesTags__item a[href]'):
        slug = _category_slug(a["href"])
        if a.get_text(strip=True) == "Усі":
            all_slug = slug
        else:
            tags.append(slug)
    if all_slug and all_slug == category_slug:
        subcategories += [t for t in tags if t not in subcategories]

    h1 = " ".join(soup.h1.get_text(" ", strip=True).split()) if soup.h1 else None
    m = CITY_RE.search(h1 or "")
    crumb = soup.select_one('a[data-marker="Breadcrumb Category 1"][href]')
    crumb_slug = crumb["href"].rstrip("/").rsplit("/", 1)[-1] if crumb else None
    return ListingPage(total=total, tiles=tiles, subcategories=subcategories, h1=h1,
                       city_name=m.group(1) if m else None, city_slug=crumb_slug, tags_all_slug=all_slug)
