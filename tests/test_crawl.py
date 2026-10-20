import json
import re

import httpx
import pytest

from zakaz_fish import crawl, db
from zakaz_fish.config import Config
from zakaz_fish.http import Fetcher
from zakaz_fish.parse_graphql import parse_chains

EMPTY = "<html><body><div data-marker='Products List'></div></body></html>"


@pytest.fixture
def conn(fixtures_dir):
    c = db.connect(":memory:")
    db.init_db(c)
    chains = parse_chains(json.loads((fixtures_dir / "graphql_chains.json").read_text(encoding="utf-8")))
    db.upsert_chains(c, chains)
    c.commit()
    return c


def make_fetcher(handler, tmp_path):
    return Fetcher(Config(), raw_dir=tmp_path, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def site(listing_html, product_html, page2=EMPTY, fail=None):
    """Мок сайту: сторінка 1 = фікстура, сторінка 2 = page2, товар = фікстура."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if fail and fail in url:
            return httpx.Response(403, text="forbidden")
        if "/uk/products/" in url:
            return httpx.Response(200, text=product_html)
        if "page=2" in url:
            return httpx.Response(200, text=page2)
        return httpx.Response(200, text=listing_html)
    return handler


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_listings_stop_on_empty_page(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    run_id, status = crawl.crawl_listings(conn, f, Config())
    assert status == "ok"
    assert f.requests == 20  # 10 категорій × (сторінка 1 + порожня сторінка 2)
    assert count(conn, "SELECT COUNT(*) FROM products") == 40
    assert count(conn, "SELECT COUNT(*) FROM categories") == 10
    assert count(conn, "SELECT COUNT(*) FROM listing_prices") == 400
    assert count(conn, "SELECT COUNT(*) FROM product_categories WHERE source = 'listing'") == 400
    assert count(conn, "SELECT COUNT(*) FROM products WHERE id_type = 'chain'") == 13
    assert (tmp_path / run_id / "listing" / "fish-and-seafood-zakaz_p1.html.gz").exists()
    stats = json.loads(conn.execute("SELECT stats_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert any("понад 2%" in w for w in stats["warnings"])  # 40 унікальних проти total=1502


def test_listings_stop_when_no_new_ids(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html, page2=listing_html), tmp_path)
    _, status = crawl.crawl_listings(conn, f, Config())
    assert status == "ok"
    assert f.requests == 20


def test_listings_max_pages(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    crawl.crawl_listings(conn, f, Config(max_pages_per_category=1))
    assert f.requests == 10


def test_repeat_run_keeps_first_seen_and_adds_snapshots(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    crawl.crawl_listings(conn, f, Config())
    conn.execute("UPDATE products SET first_seen = '2000-01-01T00:00:00Z'")
    crawl.crawl_listings(conn, f, Config())
    assert count(conn, "SELECT COUNT(*) FROM products") == 40
    assert count(conn, "SELECT COUNT(*) FROM products WHERE first_seen = '2000-01-01T00:00:00Z'") == 40
    assert count(conn, "SELECT COUNT(*) FROM listing_prices") == 800
    assert count(conn, "SELECT COUNT(*) FROM runs WHERE status = 'ok'") == 2


def _insert_old_product(conn):
    conn.execute("""INSERT INTO products (product_id, id_type, url, first_seen, last_seen, is_active)
                    VALUES ('old1', 'other', 'https://zakaz.ua/uk/products/x--old1/', 'a', 'a', 1)""")
    conn.commit()


def test_inactive_after_ok_run(conn, listing_html, product_html, tmp_path):
    _insert_old_product(conn)
    crawl.crawl_listings(conn, make_fetcher(site(listing_html, product_html), tmp_path), Config())
    assert count(conn, "SELECT is_active FROM products WHERE product_id = 'old1'") == 0
    assert count(conn, "SELECT COUNT(*) FROM products WHERE is_active = 1") == 40


def test_stop_crawl_keeps_data_and_active_flags(conn, listing_html, product_html, tmp_path):
    _insert_old_product(conn)
    f = make_fetcher(site(listing_html, product_html, fail="frozen-fish-zakaz"), tmp_path)
    run_id, status = crawl.crawl_listings(conn, f, Config())
    assert status == "stopped"
    row = conn.execute("SELECT status, stop_reason FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "stopped" and "403" in row["stop_reason"]
    assert count(conn, "SELECT COUNT(*) FROM products") == 41  # зібране збережено
    assert count(conn, "SELECT is_active FROM products WHERE product_id = 'old1'") == 1


def test_failed_category_does_not_deactivate(conn, listing_html, product_html, tmp_path):
    _insert_old_product(conn)

    def handler(request):
        if "seafood-zakaz" in str(request.url) and "fish-and-seafood" not in str(request.url):
            return httpx.Response(404, text="nope")
        return site(listing_html, product_html)(request)

    _, status = crawl.crawl_listings(conn, make_fetcher(handler, tmp_path), Config())
    assert status == "failed"
    assert count(conn, "SELECT is_active FROM products WHERE product_id = 'old1'") == 1


def test_details_new_mode(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    crawl.crawl_listings(conn, f, Config())
    run_id, status = crawl.crawl_details(conn, f, Config(), mode="new", limit=3)
    assert status == "ok"
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL") == 3
    p = conn.execute("SELECT * FROM products WHERE details_scraped_at IS NOT NULL LIMIT 1").fetchone()
    assert (p["brand"], p["country"], p["species"]) == ("Майстри Смаку", "Україна", "оселедець")
    assert p["composition"] == "оселедець, сіль кухонна харчова"
    assert (p["kcal_100g"], p["carbs_100g"]) == (242.0, None)
    assert (p["cat_l1"], p["cat_l2"], p["cat_l3"]) == (
        "fish-and-seafood-zakaz", "prepared-fish-zakaz", "low-salted-fish-zakaz")
    assert p["description"] is None
    assert count(conn, "SELECT COUNT(*) FROM product_attributes") == 3 * 7
    assert conn.execute("SELECT name, level, parent_slug FROM categories WHERE slug = 'low-salted-fish-zakaz'"
                        ).fetchone()[:] == ("Солена риба", 3, "prepared-fish-zakaz")
    assert (tmp_path / run_id / "product").is_dir()

    # Повторно: беруться лише ще не оброблені; атрибути не дублюються.
    crawl.crawl_details(conn, f, Config(), mode="new", limit=3)
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL") == 6
    crawl.crawl_details(conn, f, Config(), mode="all", limit=1)
    assert count(conn, "SELECT COUNT(*) FROM product_attributes") == 6 * 7


def test_details_stale_mode_and_bad_mode(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    crawl.crawl_listings(conn, f, Config())
    crawl.crawl_details(conn, f, Config(), mode="new", limit=2)
    assert len(crawl.select_for_details(conn, "stale:7")) == 38
    conn.execute("UPDATE products SET details_scraped_at = '2000-01-01T00:00:00Z' "
                 "WHERE details_scraped_at IS NOT NULL")
    assert len(crawl.select_for_details(conn, "stale:7")) == 40
    with pytest.raises(ValueError):
        crawl.crawl_details(conn, f, Config(), mode="bogus")


def test_details_stop_crawl(conn, listing_html, product_html, tmp_path):
    crawl.crawl_listings(conn, make_fetcher(site(listing_html, product_html), tmp_path), Config())
    f = make_fetcher(site(listing_html, product_html, fail="/uk/products/"), tmp_path)
    _, status = crawl.crawl_details(conn, f, Config(), mode="new", limit=5)
    assert status == "stopped"
    assert f.requests == 1


def test_daily(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(site(listing_html, product_html), tmp_path)
    run_id, status = crawl.crawl_daily(conn, f, Config())
    assert status == "ok"
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL") == 40
    assert count(conn, "SELECT COUNT(*) FROM runs WHERE kind = 'daily'") == 1
