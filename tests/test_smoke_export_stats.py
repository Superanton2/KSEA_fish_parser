import csv

import httpx
import pytest
from openpyxl import load_workbook

from zakaz_fish import crawl, db
from zakaz_fish.__main__ import main
from zakaz_fish.config import Config
from zakaz_fish.export import export_all
from zakaz_fish.http import Fetcher
from zakaz_fish.smoke import format_report, last_smoke_ok, run_smoke
from zakaz_fish.stats import collect_stats

EMPTY = "<html><body></body></html>"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def fetcher_for(handler):
    return Fetcher(Config(), transport=httpx.MockTransport(handler), sleep=lambda s: None)


def good_site(listing_html, product_html, page2=None):
    def handler(request):
        url = str(request.url)
        if "/uk/products/" in url:
            return httpx.Response(200, text=product_html)
        return httpx.Response(200, text=page2 if "page=2" in url and page2 else listing_html)
    return handler


def shifted_page2(listing_html):
    """Сторінка 2 з іншими product_id."""
    return listing_html.replace("--novus", "--zzz").replace("--04820", "--14820")


def test_smoke_ok_makes_exactly_three_requests(conn, listing_html, product_html, tmp_path):
    f = fetcher_for(good_site(listing_html, product_html, shifted_page2(listing_html)))
    run_id, checks = run_smoke(conn, Config(), f, tmp_path)
    assert f.requests == 3
    assert all(c.ok for c in checks) and len(checks) == 4
    assert last_smoke_ok(conn)
    assert (tmp_path / "listing_p1.html").exists() and (tmp_path / "listing_p2.html").exists()
    assert "FAIL" not in format_report(checks)


def test_smoke_fails_when_page2_repeats_page1(conn, listing_html, product_html, tmp_path):
    f = fetcher_for(good_site(listing_html, product_html))
    _, checks = run_smoke(conn, Config(), f, tmp_path)
    assert not all(c.ok for c in checks)
    assert not last_smoke_ok(conn)
    assert "FAIL" in format_report(checks)


def test_smoke_fails_when_product_page_is_bare(conn, listing_html, tmp_path):
    f = fetcher_for(good_site(listing_html, EMPTY, shifted_page2(listing_html)))
    _, checks = run_smoke(conn, Config(), f, tmp_path)
    assert [c.ok for c in checks] == [True, True, True, False]


def test_smoke_stops_on_cloudflare(conn, tmp_path):
    f = fetcher_for(lambda r: httpx.Response(200, text="Just a moment..."))
    run_id, checks = run_smoke(conn, Config(), f, tmp_path)
    assert f.requests == 1
    assert conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0] == "stopped"


def populated(conn, listing_html, product_html, tmp_path):
    f = fetcher_for(good_site(listing_html, product_html))
    crawl.crawl_listings(conn, f, Config())
    crawl.crawl_details(conn, f, Config(), "new", 2)


def test_export_populated_and_empty(conn, listing_html, product_html, tmp_path):
    # порожня база
    export_all(conn, tmp_path / "empty")
    with (tmp_path / "empty" / "products.csv").open(encoding="utf-8-sig") as f:
        assert len(list(csv.reader(f))) == 1

    populated(conn, listing_html, product_html, tmp_path)
    paths = export_all(conn, tmp_path / "out")
    assert {p.name for p in paths} == {"products.csv", "listing_prices_latest.csv", "zakaz_fish.xlsx"}
    raw = (tmp_path / "out" / "products.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with (tmp_path / "out" / "products.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 40
    assert any(r["brand"] == "Майстри Смаку" for r in rows)
    with (tmp_path / "out" / "listing_prices_latest.csv").open(encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 400
    wb = load_workbook(tmp_path / "out" / "zakaz_fish.xlsx")
    assert wb.sheetnames == ["products", "listing_prices_latest", "categories", "chains", "stores"]
    assert wb["products"].max_row == 41


def test_stats_on_empty_and_populated(conn, listing_html, product_html, tmp_path):
    assert "Товарів: 0" in collect_stats(conn)
    populated(conn, listing_html, product_html, tmp_path)
    out = collect_stats(conn)
    assert "Товарів: 40 (активних 40, з деталями 2)" in out
    assert "fish-and-seafood-zakaz: 40" in out


def test_crawl_commands_refuse_without_smoke(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for cmd in ("crawl-listings", "crawl-details", "daily"):
        assert main([cmd]) == 1
    assert "smoke-check" in capsys.readouterr().err


def test_cli_init_seed_stats_export(tmp_path, monkeypatch, fixtures_dir, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init-db"]) == 0
    assert main(["seed", "--chains", str(fixtures_dir / "graphql_chains.json"),
                 "--stores", str(fixtures_dir / "graphql_delivery_schedules.json")]) == 0
    assert main(["stats"]) == 0
    assert "Мереж у довіднику: 21, магазинів: 21" in capsys.readouterr().out
    assert main(["export"]) == 0
    assert (tmp_path / "data/out/zakaz_fish.xlsx").exists()
