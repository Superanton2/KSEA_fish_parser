import pytest

from zakaz_fish.prices import parse_weight, price_to_kop


def test_price_to_kop():
    assert price_to_kop("199.00 ") == 19900
    assert price_to_kop("1 399.00") == 139900
    assert price_to_kop("1 399.00 ₴") == 139900
    assert price_to_kop("86.50") == 8650
    assert price_to_kop("86,5") == 8650


def test_price_to_kop_invalid():
    with pytest.raises(ValueError):
        price_to_kop("abc")


@pytest.mark.parametrize("text,expected", [
    ("за 1 кг", ("per_kg", 1, "кг")),
    ("250 г", ("pack", 250, "г")),
    ("500 мл", ("pack", 500, "мл")),
    ("1 л", ("pack", 1, "л")),
    ("6 шт", ("pack", 6, "шт")),
    ("1.5 кг", ("pack", 1.5, "кг")),
    ("щось дивне", ("unknown", None, None)),
    (None, ("unknown", None, None)),
])
def test_parse_weight(text, expected):
    assert parse_weight(text) == expected
