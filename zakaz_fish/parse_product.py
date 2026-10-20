import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

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


def parse_product(html: str, store_description: bool = False) -> ProductDetails:
    soup = BeautifulSoup(html, "lxml")
    d = ProductDetails(title=_text(soup.select_one("h1")))

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
