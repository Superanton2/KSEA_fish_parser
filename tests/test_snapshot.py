"""S2–S7: створення знімка і виконання черги на фейковому сайті зі зразків."""
import json

import pytest

from tests.fakesite import make_site
from zakaz_fish import db, snapshot, tasks
from zakaz_fish.config import Config, RateCfg, SnapshotCfg

SID = "2026-11-05"


def make_cfg(tmp_path, **snap):
    s = dict(cities=["kyiv", "ivano-frankivsk"], scopes=["core", "canned", "snacks"], chains_only=["novus"],
             max_store_details=2, max_city_probes=2, baseline_cities=["ivano-frankivsk"])
    s.update(snap)
    return Config(user_agent="KSEA-FishMarketResearch/0.2", contact_email="secret@example.org",
                  db_path=str(tmp_path / "db.sqlite"), raw_dir=str(tmp_path / "raw"), out_dir=str(tmp_path / "out"),
                  snapshot=SnapshotCfg(**s), rate=RateCfg(delay_min_s=0, delay_max_s=0))


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return cfg, conn


def run(env, **kw):
    cfg, conn = env
    kw.setdefault("transport", make_site())
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("wait_for_delayed", False)
    return snapshot.run_snapshot(conn, cfg, SID, **kw)


def test_create_is_idempotent_and_hides_email(env):
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    snapshot.create_snapshot(conn, cfg, SID)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == n
    stored = conn.execute("SELECT config_json FROM snapshots").fetchone()[0]
    assert "secret@example.org" not in stored and "contact_email" not in stored
    # 2 міста x 3 розділи + novus: головна й список магазинів
    stages = tasks.counts(conn, SID)
    assert stages["city_listing"]["pending"] == 6 and stages["storefront_home"]["pending"] == 1
    assert stages["store_list"]["pending"] == 1


def test_full_run_on_fake_site(env):
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    log = []
    assert run(env, transport=make_site(log)) == 0
    assert conn.execute("SELECT status FROM snapshots").fetchone()[0] in ("done", "done_with_failures")
    assert not conn.execute("SELECT 1 FROM tasks WHERE status = 'pending'").fetchone()

    # агрегатор: консервація Києва з 14 підрозділами, без самопосилання
    cats = {r[0] for r in conn.execute("SELECT category_slug FROM city_category_stats WHERE city_slug = 'kyiv' "
                                       "AND scope = 'canned'")}
    assert "canned-fish-zakaz" in cats and "cod-liver-zakaz" in cats and len(cats) == 15
    assert conn.execute("SELECT COUNT(*) FROM city_listing_rows WHERE city_slug = 'kyiv' AND scope = 'canned'").fetchone()[0] > 0
    # порожня категорія малого міста
    st = conn.execute("SELECT status FROM city_category_stats WHERE city_slug='ivano-frankivsk' "
                      "AND category_slug='canned-fish-zakaz'").fetchone()
    assert st[0] == "not_found"
    # плитки «Немає в наявності» не зберігаються
    snacks = conn.execute("SELECT site_total, unique_seen, out_of_stock FROM city_category_stats WHERE "
                          "city_slug='ivano-frankivsk' AND category_slug='packed-fish-snacks-zakaz'").fetchone()
    assert (snacks["site_total"], snacks["unique_seen"], snacks["out_of_stock"]) == (7, 7, 26)
    # назву міста записано з H1
    assert conn.execute("SELECT name_uk FROM cities WHERE city_slug='kyiv'").fetchone()[0] == "Київ"
    # підрозділи не вигадані для snacks
    assert not conn.execute("SELECT 1 FROM city_category_stats WHERE category_slug = 'chips-zakaz'").fetchone()

    # вітрина: знайдено рибні категорії за назвою, каталог дав ціни
    scopes = {r[0] for r in conn.execute("SELECT scope FROM chain_categories WHERE chain_id='novus'")}
    assert {"core", "canned"} <= scopes
    row = conn.execute("SELECT * FROM chain_listing_rows WHERE chain_id='novus' AND product_id='04820185100281' "
                       "AND scope='core'").fetchone()
    assert (row["price_kop"], row["old_price_kop"], row["discount_until_text"]) == (17900, 40900, "до 02.11")
    assert row["discount_until_date"] == "2026-11-02"
    assert conn.execute("SELECT status FROM chain_storefronts WHERE chain_id='novus'").fetchone()[0] == "ok"

    # товар на вітрині й в агрегаторі: один рядок products
    assert conn.execute("SELECT COUNT(*) FROM products WHERE product_id='04820185100281'").fetchone()[0] == 1
    # дублі: novus-ID без агрегатора мають first_source=storefront
    assert conn.execute("SELECT COUNT(*) FROM products WHERE first_source = 'storefront'").fetchone()[0] > 0

    # картки: meta збережено
    d = conn.execute("SELECT * FROM products WHERE details_status='ok' LIMIT 1").fetchone()
    assert d["meta_description"] and json.loads(d["meta_chain_names"])
    # магазини: список + 2 сторінки магазинів з послугами
    assert conn.execute("SELECT COUNT(*) FROM store_locations").fetchone()[0] == 30
    assert conn.execute("SELECT COUNT(*) FROM store_locations WHERE services_json IS NOT NULL").fetchone()[0] == 2
    # перевірка міст: невалідні (404) записані
    assert conn.execute("SELECT COUNT(*) FROM cities WHERE source='discovered' AND is_valid=0").fetchone()[0] == 2
    assert (cfg.data_dir / "status.json").exists() and (tmp := cfg.out_dir)
    # жодного запиту до stores-api, лише дозволені хости
    assert {h for h, _ in log} <= {"zakaz.ua", "novus.zakaz.ua"}
    # порядок: усі списки йдуть раніше за картки товарів
    paths = [p for _, p in log]
    first_product = next(i for i, p in enumerate(paths) if "/products/" in p)
    assert not any("/categories/" in p or "canned-fish" in p for p in paths[first_product:])


def test_interrupted_run_resumes_without_duplicates(env):
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    assert run(env, max_requests=8) == 0
    done_before = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
    assert 0 < done_before < 20
    assert conn.execute("SELECT status FROM snapshots").fetchone()[0] == "stopped"
    assert run(env) == 0
    assert conn.execute("SELECT status FROM snapshots").fetchone()[0] in ("done", "done_with_failures")
    dup = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT stage || task_key) FROM tasks").fetchone()[0]
    assert dup == 0
    # другий повний прогін нічого не додає
    n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    run(env)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == n


def test_500_on_products_is_retried_later_not_fatal(env):
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    assert run(env, transport=make_site(fail_first_n_500=2)) == 0
    retried = conn.execute("SELECT COUNT(*) FROM tasks WHERE attempts > 0 AND status = 'pending'").fetchone()[0]
    assert retried == 2          # відкладено на 5 хв; wait_for_delayed=False -> зупинка без очікування
    assert conn.execute("SELECT COUNT(*) FROM http_log WHERE outcome='retryable'").fetchone()[0] == 2


def test_403_stops_with_code_2_and_keeps_queue(env):
    import httpx
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    site = make_site()

    def handler(req):
        if req.url.path != "/robots.txt" and "canned" in req.url.path:
            return httpx.Response(403, text="forbidden")
        return site.handle_request(req)

    assert run(env, transport=httpx.MockTransport(handler)) == 2
    row = conn.execute("SELECT status, stop_reason FROM snapshots").fetchone()
    assert row["status"] == "stopped" and "403" in row["stop_reason"]
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0] > 0


def test_robots_disallow_on_zakaz_uk_stops_with_code_4(env):
    import httpx
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /uk/\n")
        return httpx.Response(200, text="x")

    assert run(env, transport=httpx.MockTransport(handler)) == 4


def test_unexpected_pages_five_in_a_row_stop_with_code_3(env):
    import httpx
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    site = make_site()

    def handler(req):
        if req.url.host == "zakaz.ua" and req.url.path != "/robots.txt":
            return httpx.Response(200, text="<html><title>hm</title></html>")
        return site.handle_request(req)

    assert run(env, transport=httpx.MockTransport(handler)) == 3


def test_city_check_rejects_wrong_city(env):
    import httpx
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    site = make_site()

    def handler(req):
        r = site.handle_request(req)
        if req.url.path.startswith("/uk/kyiv/") and r.status_code == 200:
            return httpx.Response(200, text=r.text.replace("у місті Київ", "у місті Одеса"))
        return r

    run(env, transport=httpx.MockTransport(handler), stages=["city_listing"])
    # перша сторінка задає ім'я міста (Одеса), тож порівняння з Києвом не виникає; перевіряємо протилежне:
    conn.execute("UPDATE cities SET name_uk = 'Київ' WHERE city_slug = 'kyiv'")
    conn.execute("UPDATE tasks SET status='pending', attempts=0, next_attempt_at=NULL WHERE stage='city_listing'")
    conn.commit()
    run(env, transport=httpx.MockTransport(handler), stages=["city_listing"])
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE stage='city_listing' AND last_error LIKE 'unexpected_page%'"
                        " AND task_key LIKE 'kyiv|%' AND attempts > 0").fetchone()[0] > 0


def test_product_redirected_to_home_is_not_found_not_unexpected(env):
    """Пілот 02.11.2026: картки лише з вітрин редіректять на /uk/; це не зупинка й не `empty`."""
    from datetime import date
    from types import SimpleNamespace
    from zakaz_fish.handlers import Ctx, product_detail
    cfg, conn = env
    snapshot.create_snapshot(conn, cfg, SID)
    conn.execute("INSERT INTO products (product_id, id_type, url, first_seen, last_seen) VALUES "
                 "('04820186620320', 'ean', 'https://zakaz.ua/uk/products/x--04820186620320/', 'a', 'a')")
    ctx = Ctx(conn, cfg, SID, date(2026, 11, 2), last_final_url="https://zakaz.ua/uk/")
    task = {"url": "https://zakaz.ua/uk/products/x--04820186620320/"}
    product_detail(ctx, task, "<html><h1>Головна</h1></html>", {"product_id": "04820186620320"})
    assert conn.execute("SELECT details_status FROM products").fetchone()[0] == "not_found"
    # справжня порожня картка (URL товару, але без даних) лишається Unexpected
    from zakaz_fish.handlers import Unexpected
    ctx.last_final_url = task["url"]
    with pytest.raises(Unexpected):
        product_detail(ctx, task, "<html><h1>x</h1></html>", {"product_id": "04820186620320"})
