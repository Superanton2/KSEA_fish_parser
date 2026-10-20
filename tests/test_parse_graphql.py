import json

import pytest

from zakaz_fish import db
from zakaz_fish.parse_graphql import parse_chains, parse_delivery_schedules, parse_uber_product
from zakaz_fish.sources import StorePriceSource


def load(fixtures_dir, name):
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


def test_chains(fixtures_dir):
    chains = parse_chains(load(fixtures_dir, "graphql_chains.json"))
    assert len(chains) == 21
    assert {c.chain_id: c for c in chains}["ekomarket"].hostname == "eko.zakaz.ua"


def test_delivery_schedules(fixtures_dir):
    refs = {r.chain_id: r for r in parse_delivery_schedules(load(fixtures_dir, "graphql_delivery_schedules.json"))}
    assert len(refs) == 21
    assert (refs["novus"].store_id, refs["novus"].zone_hint) == ("48201031", "Mostytsky")
    assert (refs["metro"].store_id, refs["metro"].zone_hint) == ("48215611", "Teremki-2")


def test_uber_product(fixtures_dir):
    ean, chains, stores = parse_uber_product(
        load(fixtures_dir, "graphql_uber_product_novus02877258000000.json"))
    assert ean == "novus02877258000000"
    assert chains == ["novus"]
    assert len(stores) == 1
    s = stores[0]
    assert (s.chain_id, s.store_id, s.available) == ("novus", "48201031", True)
    assert (s.price_kop, s.full_price_kop, s.discount_percent) == (19900, 26900, 26)
    assert s.discount_date_to == "2026-10-28"


def test_seed_is_idempotent_and_keeps_manual_city(fixtures_dir):
    conn = db.connect(":memory:")
    db.init_db(conn)
    chains = parse_chains(load(fixtures_dir, "graphql_chains.json"))
    refs = parse_delivery_schedules(load(fixtures_dir, "graphql_delivery_schedules.json"))
    for _ in range(2):
        db.upsert_chains(conn, chains)
        db.upsert_stores(conn, refs, "delivery_schedules_fixture")
        conn.execute("UPDATE stores SET city = 'kyiv' WHERE store_id = '48201031'")
    assert conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0] == 21
    assert conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 21
    assert conn.execute("SELECT city FROM stores WHERE store_id = '48201031'").fetchone()[0] == "kyiv"


def test_store_price_source_is_stub():
    with pytest.raises(NotImplementedError, match="TODO\\(unverified\\)"):
        StorePriceSource().fetch(["novus02877258000000"])
