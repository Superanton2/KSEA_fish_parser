import pytest

from zakaz_fish.parse_product import parse_product


@pytest.fixture(scope="module")
def details(product_html):
    return parse_product(product_html, store_description=False)


def test_title_and_breadcrumbs(details):
    assert details.title == "Оселедець Матіас норвезький без голови слабосолений"
    assert details.breadcrumbs == [
        (1, "fish-and-seafood-zakaz", "Риба та морепродукти"),
        (2, "prepared-fish-zakaz", "Риба приготована"),
        (3, "low-salted-fish-zakaz", "Солена риба"),
    ]


def test_attributes(details):
    values = {k: v for k, (_, v) in details.attributes.items()}
    assert values == {
        "tm": "Майстри Смаку", "pr": "Власне виробництво", "weight": "Вагові",
        "shelf_life": "5 діб", "trange": "2...6", "country": "Україна",
        "genus_species": "оселедець",
    }
    assert details.attributes["tm"][0] == "Торгова марка"


def test_columns_mapping(details):
    assert details.columns() == {
        "brand": "Майстри Смаку", "producer": "Власне виробництво", "country": "Україна",
        "species": "оселедець", "shelf_life": "5 діб", "storage_temp": "2...6",
    }


def test_composition_and_nutrition(details):
    assert details.composition == "оселедець, сіль кухонна харчова"
    assert (details.kcal, details.protein, details.fat, details.carbs) == (242.0, 17.7, 19.5, None)


def test_description_flag(product_html, details):
    assert details.description is None
    with_desc = parse_product(product_html, store_description=True)
    assert with_desc.description.startswith("Що ж таке оселедець Матіас?")


def test_unknown_taxon_is_kept():
    html = ('<ul><li data-marker="Taxon foo"><p class="ProductAbout__entryTitle">Щось</p>'
            '<p class="ProductAbout__entryValue">X</p></li></ul>')
    assert parse_product(html).attributes == {"foo": ("Щось", "X")}


def test_relative_breadcrumb_hrefs(fixtures_dir, details):
    html = (fixtures_dir / "product_novus02877258000000_relative.html").read_text(encoding="utf-8")
    assert 'href="/uk/kyiv/' in html
    assert parse_product(html).breadcrumbs == details.breadcrumbs


def test_is_empty(details):
    assert not details.is_empty
    assert parse_product("<html><body><h1>Назва</h1></body></html>").is_empty
