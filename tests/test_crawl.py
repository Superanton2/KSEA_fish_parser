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


def test_details_request_absolute_urls_from_relative_listing(conn, fixtures_dir, product_html, tmp_path):
    relative = (fixtures_dir / "listing_fish_and_seafood_p1_relative.html").read_text(encoding="utf-8")
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return site(relative, product_html)(request)

    f = make_fetcher(handler, tmp_path)
    crawl.crawl_listings(conn, f, Config())
    assert all(r[0].startswith("https://zakaz.ua/uk/products/")
               for r in conn.execute("SELECT url FROM products"))
    # імітація старої бази з відносним url
    conn.execute("UPDATE products SET url = REPLACE(url, 'https://zakaz.ua', '')")
    requested.clear()
    crawl.crawl_details(conn, f, Config(), "new", 3)
    products = [u for u in requested if "/uk/products/" in u]
    assert len(products) == 3
    assert all(u.startswith("https://zakaz.ua/uk/products/") for u in products)


def _listing_and_details(conn, listing_html, product_html, tmp_path, product_handler, limit=3):
    def handler(request):
        if "/uk/products/" in str(request.url):
            return product_handler(request)
        return site(listing_html, product_html)(request)

    f = make_fetcher(handler, tmp_path)
    crawl.crawl_listings(conn, f, Config())
    return f, crawl.crawl_details(conn, f, Config(), "new", limit)


def test_details_status_ok(conn, listing_html, product_html, tmp_path):
    _listing_and_details(conn, listing_html, product_html, tmp_path,
                         lambda r: httpx.Response(200, text=product_html))
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'ok' "
                       "AND details_scraped_at IS NOT NULL") == 3


def test_404_marks_not_found_and_new_mode_skips_it(conn, listing_html, product_html, tmp_path):
    f, (_, status) = _listing_and_details(conn, listing_html, product_html, tmp_path,
                                          lambda r: httpx.Response(404, text="нема"), limit=2)
    assert status == "failed"
    nf = [r[0] for r in conn.execute("SELECT product_id FROM products WHERE details_status = 'not_found'")]
    assert len(nf) == 2
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL") == 0
    assert not {p for p, _ in crawl.select_for_details(conn, "new")} & set(nf)
    assert not {p for p, _ in crawl.select_for_details(conn, "stale:7")} & set(nf)
    assert set(nf) <= {p for p, _ in crawl.select_for_details(conn, "all")}
    assert len(crawl.select_for_details(conn, "new")) == 38


def test_server_error_marks_error_and_is_retried_later(conn, listing_html, product_html, tmp_path):
    _, (_, status) = _listing_and_details(conn, listing_html, product_html, tmp_path,
                                          lambda r: httpx.Response(500), limit=1)
    assert status == "failed"
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'error'") == 1
    assert len(crawl.select_for_details(conn, "new")) == 40


def test_init_db_migrates_old_schema():
    c = db.connect(":memory:")
    c.execute("CREATE TABLE products (product_id TEXT PRIMARY KEY, id_type TEXT NOT NULL, url TEXT NOT NULL, "
              "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, details_scraped_at TEXT)")
    db.init_db(c)
    assert "details_status" in {r[1] for r in c.execute("PRAGMA table_info(products)")}


BARE_PRODUCT = "<html><body><h1>Оболонка</h1></body></html>"


def test_empty_product_page_is_failed_not_scraped(conn, listing_html, product_html, tmp_path):
    _, (run_id, status) = _listing_and_details(conn, listing_html, product_html, tmp_path,
                                               lambda r: httpx.Response(200, text=BARE_PRODUCT), limit=3)
    assert status == "failed"
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'empty'") == 3
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL") == 0
    assert count(conn, "SELECT COUNT(*) FROM product_attributes") == 0
    stats = json.loads(conn.execute("SELECT stats_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert len(stats["failed"]) == 3 and stats["empty"] == 3
    assert len(crawl.select_for_details(conn, "new")) == 40  # empty перевіряється наново


def test_five_empty_pages_in_a_row_stop_run(conn, listing_html, product_html, tmp_path):
    f, (run_id, status) = _listing_and_details(conn, listing_html, product_html, tmp_path,
                                               lambda r: httpx.Response(200, text=BARE_PRODUCT), limit=20)
    assert status == "stopped"
    row = conn.execute("SELECT status, stop_reason FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "stopped" and "5 сторінок" in row["stop_reason"]
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'empty'") == 5
    assert f.requests == 20 + 5  # 20 запитів списків + рівно 5 товарів, далі зупинка


def test_good_page_resets_empty_streak(conn, listing_html, product_html, tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        # 4 порожні, 1 нормальна, 4 порожні: серія не досягає 5
        text = product_html if len(calls) == 5 else BARE_PRODUCT
        return httpx.Response(200, text=text)

    _, (_, status) = _listing_and_details(conn, listing_html, product_html, tmp_path, handler, limit=9)
    assert status == "failed"
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'empty'") == 8
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'ok'") == 1


def interrupting(listing_html, product_html, on_request):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == on_request:
            raise KeyboardInterrupt
        return site(listing_html, product_html)(request)

    return handler


def last_run(conn):
    return conn.execute("SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()


def test_interrupt_in_listings_marks_run_stopped_and_keeps_data(conn, listing_html, product_html, tmp_path):
    _insert_old_product(conn)
    f = make_fetcher(interrupting(listing_html, product_html, 4), tmp_path)
    with pytest.raises(KeyboardInterrupt):
        crawl.crawl_listings(conn, f, Config())
    run = last_run(conn)
    assert (run["status"], run["stop_reason"]) == ("stopped", "interrupted")
    assert run["finished_at"]
    assert count(conn, "SELECT COUNT(*) FROM products") == 41  # 40 зі сторінок до перерви + old1
    assert count(conn, "SELECT is_active FROM products WHERE product_id = 'old1'") == 1


def test_interrupt_in_details_keeps_finished_products(conn, listing_html, product_html, tmp_path):
    crawl.crawl_listings(conn, make_fetcher(site(listing_html, product_html), tmp_path), Config())
    f = make_fetcher(interrupting(listing_html, product_html, 3), tmp_path)
    with pytest.raises(KeyboardInterrupt):
        crawl.crawl_details(conn, f, Config(), "new", 10)
    run = last_run(conn)
    assert (run["kind"], run["status"], run["stop_reason"]) == ("details", "stopped", "interrupted")
    assert count(conn, "SELECT COUNT(*) FROM products WHERE details_status = 'ok'") == 2


def test_interrupt_in_daily(conn, listing_html, product_html, tmp_path):
    f = make_fetcher(interrupting(listing_html, product_html, 25), tmp_path)  # у фазі деталей
    with pytest.raises(KeyboardInterrupt):
        crawl.crawl_daily(conn, f, Config())
    run = last_run(conn)
    assert (run["kind"], run["status"], run["stop_reason"]) == ("daily", "stopped", "interrupted")


def test_unexpected_error_marks_run_failed(conn, listing_html, product_html, tmp_path):
    def handler(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        crawl.crawl_listings(conn, make_fetcher(handler, tmp_path), Config())
    run = last_run(conn)
    assert run["status"] == "failed" and "boom" in run["stop_reason"]


def test_cli_returns_130_on_interrupt(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(crawl, "crawl_listings", boom)
    from zakaz_fish.__main__ import main
    assert main(["crawl-listings", "--force"]) == 130
    assert "Перервано" in capsys.readouterr().err


def test_listings_stop_on_redirect_without_following(conn, listing_html, product_html, tmp_path):
    """Реальна поведінка сайту: ?page=N+1 після останньої сторінки редіректить (307)."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if "page=3" in url:
            return httpx.Response(307, headers={"location": url.split("?")[0]})
        if "page=2" in url:  # друга сторінка з іншими товарами
            return httpx.Response(200, text=listing_html.replace("--novus", "--zzz").replace("--04820", "--14820"))
        return httpx.Response(200, text=listing_html)

    f = make_fetcher(handler, tmp_path)
    run_id, status = crawl.crawl_listings(conn, f, Config())
    assert status == "ok"
    assert f.requests == 30  # 10 категорій × (стор. 1, 2, редірект на 3); без запитів за Location
    assert len([u for u in seen if "page=3" in u]) == 10
    assert len([u for u in seen if "?" not in u]) == 10  # лише сторінки 1
    assert not list((tmp_path / run_id / "listing").glob("*_p3.html.gz"))
