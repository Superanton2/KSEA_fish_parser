from collections import Counter

import pytest

from zakaz_fish.ids import classify_id
from zakaz_fish.parse_listing import parse_listing
from zakaz_fish.prices import parse_weight

CHAINS = {"novus", "metro", "kharkiv", "megamarket"}


@pytest.fixture(scope="module")
def page(listing_html):
    return parse_listing(listing_html)


def test_total_and_counts(page):
    assert page.total == 1502
    assert len(page.tiles) == 40
    assert sum(t.is_promo for t in page.tiles) == 22
    assert sum(t.price_max_kop > t.price_min_kop for t in page.tiles) == 17


def test_id_types(page):
    cls = [classify_id(t.product_id, CHAINS) for t in page.tiles]
    assert Counter(c[0] for c in cls) == {"ean": 27, "chain": 13}
    assert all(c[2] for c in cls if c[0] == "ean")
    assert Counter(c[3] for c in cls if c[0] == "chain") == {
        "novus": 8, "metro": 2, "kharkiv": 2, "megamarket": 1}


def test_first_tile(page):
    t = page.tiles[0]
    assert t.product_id == "novus02877258000000"
    assert t.title == "Оселедець Матіас норвезький без голови слабосолений"
    assert t.price_min_kop == t.price_max_kop == 19900
    assert t.weight_text == "за 1 кг"
    assert t.is_promo is True
    assert t.position == 1
    assert t.image_url


def test_range_tile(page):
    t = next(t for t in page.tiles if t.product_id == "04820088410548")
    assert (t.price_min_kop, t.price_max_kop) == (8650, 13150)
    assert t.weight_text == "250 г"
    assert parse_weight(t.weight_text) == ("pack", 250, "г")


def test_subcategories(page):
    assert page.subcategories == [
        "fresh-fish-zakaz", "frozen-fish-zakaz", "seafood-zakaz", "fish-paste-zakaz",
        "crab-sticks-zakaz", "preserves-zakaz", "laminaria-zakaz", "prepared-fish-zakaz",
        "caviar-zakaz"]


def test_empty_page():
    p = parse_listing("<html><body></body></html>")
    assert p.total is None and p.tiles == [] and p.subcategories == []


@pytest.fixture(scope="module")
def relative_page(fixtures_dir):
    html = (fixtures_dir / "listing_fish_and_seafood_p1_relative.html").read_text(encoding="utf-8")
    assert 'href="/uk/products/' in html and "https://zakaz.ua/uk/products/" not in html
    return parse_listing(html)


def test_relative_hrefs_become_absolute(page, relative_page):
    assert len(relative_page.tiles) == 40
    assert all(t.url.startswith("https://zakaz.ua/uk/products/") for t in relative_page.tiles)
    assert all(t.image_url.startswith("https://") for t in relative_page.tiles)
    assert [t.url for t in relative_page.tiles] == [t.url for t in page.tiles]
    assert [t.image_url for t in relative_page.tiles] == [t.image_url for t in page.tiles]
    assert relative_page.tiles[0].product_id == "novus02877258000000"
    assert relative_page.subcategories == page.subcategories
