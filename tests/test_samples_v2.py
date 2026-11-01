"""Парсери нових типів сторінок (SPEC v2, S1). Очікувані значення взято з живих зразків
tests/fixtures/samples/ (зібрано `sample --preset v2` 01.11.2026); файл вказано біля кожного тесту."""
from pathlib import Path

import pytest

from zakaz_fish.parse_listing import parse_listing
from zakaz_fish.parse_product import parse_product
from zakaz_fish.parse_storefront import parse_storefront_home, parse_storefront_listing
from zakaz_fish.parse_stores import parse_store_detail, parse_store_list

S = Path(__file__).parent / "fixtures" / "samples"


def read(name: str) -> str:
    return (S / f"{name}.html").read_text(encoding="utf-8")


# --- каталог агрегатора ---

def test_canned_root_has_14_subcategories_without_self_link():  # agg_kyiv_canned_p1
    p = parse_listing(read("agg_kyiv_canned_p1"), "canned-fish-zakaz")
    assert p.total == 462 and len(p.tiles) == 40
    assert p.city_name == "Київ" and p.h1 == "Рибна консервація у місті Київ"
    assert len(p.subcategories) == 14
    assert "canned-fish-zakaz" not in p.subcategories
    assert p.subcategories[0] == "cod-liver-zakaz" and p.subcategories[-1] == "canned-carp-zakaz"
    assert (p.tiles[0].product_id, p.tiles[0].price_min_kop, p.tiles[0].weight_text) == ("04823088800906", 12900, "121 г")


def test_leaf_page_has_no_subcategories_even_though_tags_exist():  # agg_kyiv_tuna_p1
    p = parse_listing(read("agg_kyiv_tuna_p1"), "tuna-zakaz")
    assert p.total == 155 and p.subcategories == [] and p.tags_all_slug == "canned-fish-zakaz"
    assert p.city_name == "Київ"  # H1 містить таб: «Тунець консервований\t у місті Київ»


@pytest.mark.parametrize("name,slug,total", [
    ("agg_kyiv_snacks_p1", "packed-fish-snacks-zakaz", 113),
    ("agg_kyiv_frozen_p1", "frozen-fish-and-seafood-zakaz", 419),
])
def test_scope_roots_tags_are_siblings_not_children(name, slug, total):
    p = parse_listing(read(name), slug)
    assert p.total == total and len(p.tiles) == 40 and p.subcategories == []


def test_semi_root_is_mixed_section_with_18_children():  # agg_kyiv_semi_p1
    p = parse_listing(read("agg_kyiv_semi_p1"), "half-made-food-zakaz")
    assert p.total == 770 and len(p.tiles) == 40 and len(p.subcategories) == 18
    assert "frozen-fish-sticks-zakaz" in p.subcategories and "vareniki-zakaz" in p.subcategories


def test_small_city_partial_subcategories():  # agg_if_fish_p1
    p = parse_listing(read("agg_if_fish_p1"), "fish-and-seafood-zakaz")
    assert p.total == 49 and len(p.tiles) == 40 and p.city_name == "Івано-Франківськ"
    assert p.subcategories == ["fish-paste-zakaz", "crab-sticks-zakaz", "preserves-zakaz",
                               "laminaria-zakaz", "prepared-fish-zakaz", "caviar-zakaz"]


def test_caviar_in_small_city_has_chilled_caviar_child():  # agg_if_caviar_p1
    p = parse_listing(read("agg_if_caviar_p1"), "caviar-zakaz")
    assert p.total == 4 and len(p.tiles) == 4 and p.subcategories == ["chilled-caviar-zakaz"]


def test_empty_category_is_200_with_h1_and_no_tiles():  # agg_if_fresh_fish_p1
    p = parse_listing(read("agg_if_fresh_fish_p1"), "fresh-fish-zakaz")
    assert p.total is None and p.tiles == []
    assert p.city_name == "Івано-Франківськ" and p.h1 == "Риба охолоджена у місті Івано-Франківськ"


def test_out_of_stock_tiles_follow_available_ones():  # agg_poltava_snacks_p1
    p = parse_listing(read("agg_poltava_snacks_p1"), "packed-fish-snacks-zakaz")
    assert p.total == 7 and len(p.tiles) == 33 and p.city_name == "Полтава"
    assert [t.in_stock for t in p.tiles] == [True] * 7 + [False] * 26
    assert all(t.price_min_kop is None for t in p.tiles if not t.in_stock)


def test_page2_has_no_new_structure_issue():  # agg_lviv_fish_p2
    p = parse_listing(read("agg_lviv_fish_p2"), "fish-and-seafood-zakaz")
    assert p.total == 758 and len(p.tiles) == 40 and p.city_name == "Львів"
    assert len(p.subcategories) == 9


# --- сторінка товару: meta ---

def test_product_meta_many_chains():  # prod_trout_norven
    d = parse_product(read("prod_trout_norven"))
    assert d.meta_price_min_kop == 17900 and d.meta_price_max_kop == 45036
    assert d.meta_chain_names == ["Auchan", "КОСМОС", 'Cупермаркет "Харків"', "МегаМаркет", "METRO",
                                  "NOVUS", "ОнDe", "Таврія В", "Торба", "Восторг"]
    assert d.meta_description.startswith("Купити Форель Norven")
    assert [b[1] for b in d.breadcrumbs] == ["fish-and-seafood-zakaz", "prepared-fish-zakaz", "low-salted-fish-zakaz"]


def test_product_canned_single_chain_and_breadcrumbs():  # prod_cod_liver_novus
    d = parse_product(read("prod_cod_liver_novus"))
    assert d.meta_chain_names == ["NOVUS"] and d.meta_price_min_kop == d.meta_price_max_kop == 12900
    assert [b[1] for b in d.breadcrumbs] == ["tins-jars-cooking-zakaz", "canned-fish-zakaz", "cod-liver-zakaz"]
    assert d.attributes["country"][1] == "Ісландія" and d.kcal == 387.0


def test_storefront_product_path_opens_same_card_on_aggregator():
    # prod_from_storefront_path = zakaz.ua/uk/products/<шлях із вітрини novus>: та сама картка
    a, b = parse_product(read("prod_from_storefront_path")), parse_product(read("prod_trout_norven"))
    assert a.title == b.title and a.breadcrumbs == b.breadcrumbs and a.attributes == b.attributes
    assert a.meta_chain_names == b.meta_chain_names


# --- вітрини ---

@pytest.mark.parametrize("chain,total,fish_slug", [
    ("novus", 339, "fish-and-seafood-novus"),
    ("metro", 128, "fish-and-seafood-metro"),
    ("tavriav", 345, "fish-and-seafood-tavriav"),
])
def test_storefront_home_finds_fish_category_by_name(chain, total, fish_slug):
    cats = parse_storefront_home(read(f"{chain}_home"), f"https://{chain}.zakaz.ua/uk/")
    fish = [c for c in cats if c.name == "Риба та морепродукти"]
    assert [c.slug for c in fish] == [fish_slug]
    assert fish[0].url == f"https://{chain}.zakaz.ua/uk/categories/{fish_slug}/"
    p = parse_storefront_listing(read(f"{chain}_fish_p1"), f"https://{chain}.zakaz.ua/uk/")
    assert p.total == total and len(p.tiles) == 30 and len(p.subcategories) == 9


def test_novus_home_has_parent_categories():  # novus_home
    cats = {c.slug: c.name for c in parse_storefront_home(read("novus_home"), "https://novus.zakaz.ua/uk/")}
    assert cats["tins-jars-cooking"] == "Консерви" and cats["frozen"] == "Заморозка"


def test_novus_tiles_with_discount():  # novus_fish_p1
    p = parse_storefront_listing(read("novus_fish_p1"), "https://novus.zakaz.ua/uk/")
    t = p.tiles[0]
    assert (t.product_id, t.price_kop, t.old_price_kop, t.discount_percent, t.discount_until_text, t.weight_text) == \
        ("04820185100281", 17900, 40900, 56, "до 02.11", "180 г")
    assert t.product_path == "/uk/products/file-forel-norven-180g--04820185100281/"
    herring = next(x for x in p.tiles if x.product_id == "novus02877258000000")
    assert herring.price_kop == 26900 and herring.old_price_kop is None and herring.discount_percent is None
    assert herring.weight_text == "за 1 кг"
    assert [s.slug for s in p.subcategories][:3] == ["fresh-fish", "fish", "chilled-seafood"]


def test_novus_page2_and_canned_parent():  # novus_fish_p2, novus_canned_p1
    p2 = parse_storefront_listing(read("novus_fish_p2"), "https://novus.zakaz.ua/uk/")
    assert p2.total == 339 and len(p2.tiles) == 30
    p1 = parse_storefront_listing(read("novus_fish_p1"), "https://novus.zakaz.ua/uk/")
    assert not {t.product_id for t in p1.tiles} & {t.product_id for t in p2.tiles}
    c = parse_storefront_listing(read("novus_canned_p1"), "https://novus.zakaz.ua/uk/")
    assert c.h1 == "Консерви" and c.total == 385
    assert "canned-fish" in [s.slug for s in c.subcategories]


def test_metro_discount_tile():  # metro_fish_p1
    p = parse_storefront_listing(read("metro_fish_p1"), "https://metro.zakaz.ua/uk/")
    t = next(x for x in p.tiles if x.product_id == "04820293124612")
    assert (t.price_kop, t.old_price_kop, t.discount_percent, t.discount_until_text) == (179900, 224900, 20, "до 30.11")


# --- магазини ---

def test_store_list_novus():  # novus_stores_p1
    p = parse_store_list(read("novus_stores_p1"), "https://novus.zakaz.ua/uk/")
    assert len(p.stores) == 30 and p.page_numbers == [2, 3]
    s = p.stores[0]
    assert (s.store_url, s.city_slug, s.city_name, s.address, s.hours_text) == (
        "https://novus.zakaz.ua/uk/stores/ternopil/vul-perlia-3/", "ternopil", "Тернопіль",
        "вул. Перля, 3", "08:00-22:00 пн-нд")


def test_store_list_short_page_is_last():  # metro_stores_p1 (23 < 30, пагінації немає)
    p = parse_store_list(read("metro_stores_p1"), "https://metro.zakaz.ua/uk/")
    assert len(p.stores) == 23 and p.page_numbers == []
    assert p.stores[0].city_slug == "lviv" and p.stores[0].address == "Городоцька, 300"


def test_store_list_tavriav():  # tavriav_stores_p1
    p = parse_store_list(read("tavriav_stores_p1"), "https://tavriav.zakaz.ua/uk/")
    assert len(p.stores) == 30 and p.page_numbers == [2, 3, 4] and p.stores[0].city_name == "Одеса"


def test_store_detail_has_live_fish_service():  # novus_store_detail
    d = parse_store_detail(read("novus_store_detail"))
    assert (d.city_name, d.address, d.hours_text) == ("Київ", "вул. Соборна, 2", "08:00-22:00 пн-нд")
    assert d.services == ["Каса самообслуговування", "Парковка", "Кулінарія", "Пекарня",
                          "Жива риба", "Товари для дому", "Їдальня"]
