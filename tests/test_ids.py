import pytest

from zakaz_fish.ids import classify_id, ean13_check, parse_product_url

CHAINS = {"novus", "metro", "kharkiv", "megamarket"}


def test_parse_product_url():
    url = "/uk/products/riba-oseledets-maistri-smaku--novus02877258000000/"
    assert parse_product_url(url) == ("riba-oseledets-maistri-smaku", "novus02877258000000")
    full = "https://zakaz.ua/uk/products/a--b--04820088410548/?x=1"
    assert parse_product_url(full) == ("a--b", "04820088410548")


def test_parse_product_url_without_id():
    with pytest.raises(ValueError):
        parse_product_url("/uk/products/no-id/")


def test_ean13_check():
    assert ean13_check("4820088410548") is True
    assert ean13_check("4820088410549") is False
    assert ean13_check("123") is False


def test_classify_ean():
    assert classify_id("04820088410548", CHAINS) == ("ean", "4820088410548", True, None)
    assert classify_id("04820088410549", CHAINS)[2] is False


def test_classify_chain_and_other():
    assert classify_id("novus02877258000000", CHAINS) == ("chain", None, None, "novus")
    assert classify_id("unknown02877258000000", CHAINS)[0] == "other"
    assert classify_id("abc", CHAINS)[0] == "other"


def test_classify_14_digits_without_leading_zero_is_other():
    assert classify_id("14820088410548", CHAINS) == ("other", None, None, None)
    assert classify_id("94820088410548", CHAINS)[0] == "other"
    assert classify_id("04820088410548", CHAINS)[0] == "ean"
