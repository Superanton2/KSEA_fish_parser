import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .prices import price_to_kop

# Taxon-ключ -> колонка products
ATTR_TO_COLUMN = {
    "tm": "brand",
    "pr": "producer",
    "country": "country",
    "genus_species": "species",
    "shelf_life": "shelf_life",
    "trange": "storage_temp",
}


@dataclass
class ProductDetails:
    title: str | None
    breadcrumbs: list[tuple[int, str, str]] = field(default_factory=list)  # (level, slug, name)
    attributes: dict[str, tuple[str, str]] = field(default_factory=dict)   # key -> (title, value)
    composition: str | None = None
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carbs: float | None = None
    description: str | None = None
    meta_description: str | None = None
    meta_price_min_kop: int | None = None
    meta_price_max_kop: int | None = None
    meta_chain_names: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Ні хлібних крихт, ні жодного Taxon: схоже на «порожню» оболонку без даних товару."""
        return not self.breadcrumbs and not self.attributes

    def columns(self) -> dict[str, str]:
        """Атрибути, відображені в колонки products."""
        return {col: self.attributes[key][1]
                for key, col in ATTR_TO_COLUMN.items() if key in self.attributes}


def _text(node) -> str | None:
    return node.get_text(strip=True) if node else None


def _number(text: str | None) -> float | None:
    """'242.00ккал' -> 242.0, '17.70г' -> 17.7."""
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not m:
        return None
    try:
        return float(Decimal(m.group().replace(",", ".")))
    except InvalidOperation:
        return None


META_CHAINS_RE = re.compile(r"з доставкою із супермаркетів\s+(.+?)\.\s+Порівняйте ціни", re.S)
META_PRICES_RE = re.compile(r"ціни від\s+([\d\s.,]+?)\s+до\s+([\d\s.,]+?)\s+і виберіть")


def parse_meta(soup) -> tuple[str | None, int | None, int | None, list[str]]:
    """meta description: 'Купити <назва> з доставкою із супермаркетів A, B, "C". Порівняйте ціни від X до Y і ...'.
    Назви мереж містять коми лише всередині лапок (напр. Cупермаркет "Харків"), тож ділимо по ', '."""
    tag = soup.select_one('meta[name="description"]')
    text = tag.get("content") if tag else None
    if not text:
        return None, None, None, []
    chains = []
    m = META_CHAINS_RE.search(text)
    if m:
        chains = [c.strip() for c in m.group(1).split(", ") if c.strip()]
    lo = hi = None
    m = META_PRICES_RE.search(text)
    if m:
        lo, hi = price_to_kop(m.group(1)), price_to_kop(m.group(2))
    return text, lo, hi, chains


def parse_product(html: str, store_description: bool = False) -> ProductDetails:
    soup = BeautifulSoup(html, "lxml")
    d = ProductDetails(title=_text(soup.select_one("h1")))
    d.meta_description, d.meta_price_min_kop, d.meta_price_max_kop, d.meta_chain_names = parse_meta(soup)

    for a in soup.select('a[data-marker^="Breadcrumb Category"]'):
        level = int(a["data-marker"].rsplit(" ", 1)[-1])
        if level == 0:
            continue
        slug = a["href"].rstrip("/").rsplit("/", 1)[-1]
        d.breadcrumbs.append((level, slug, a.get_text(strip=True)))

    for li in soup.select('li[data-marker^="Taxon "]'):
        key = li["data-marker"][len("Taxon "):].strip()
        title = _text(li.select_one(".ProductAbout__entryTitle")) or ""
        value = _text(li.select_one(".ProductAbout__entryValue")) or ""
        d.attributes.setdefault(key, (title, value))

    for item in soup.select(".ProductAbout__item"):
        heading = item.select_one(".ProductAbout__itemTitle")
        name = _text(heading) or ""
        if name == "Склад":
            heading.extract()
            d.composition = item.get_text(" ", strip=True) or None
        elif name.startswith("Харчові властивості"):
            for li in item.select("li"):
                title = _text(li.select_one(".ProductAbout__entryTitle"))
                value = _number(_text(li.select_one(".ProductAbout__entryValue")))
                if title == "Калорійність":
                    d.kcal = value
                elif title == "Білки":
                    d.protein = value
                elif title == "Жири":
                    d.fat = value
                elif title == "Вуглеводи":
                    d.carbs = value

    if store_description:
        d.description = _text(soup.select_one('[data-marker="About product"] .Truncate__content'))
    return d
