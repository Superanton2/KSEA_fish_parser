import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db
from .config import Config
from .http import FetchError, Fetcher, StopCrawl
from .ids import classify_id
from .parse_listing import parse_listing
from .parse_product import ATTR_TO_COLUMN, parse_product
from .prices import parse_weight

log = logging.getLogger(__name__)

KNOWN_SUBCATEGORIES = [
    "fresh-fish-zakaz", "frozen-fish-zakaz", "seafood-zakaz", "fish-paste-zakaz",
    "crab-sticks-zakaz", "preserves-zakaz", "laminaria-zakaz", "prepared-fish-zakaz",
    "caviar-zakaz",
]


def category_url(cfg: Config, slug: str, page: int = 1) -> str:
    url = f"https://zakaz.ua/{cfg.lang}/{cfg.city}/{slug}/"
    return url if page == 1 else f"{url}?page={page}"


def _run_started_at(conn, run_id) -> str:
    return conn.execute("SELECT started_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]


def _crawl_category(conn, fetcher: Fetcher, cfg: Config, run_id: str, now: str, slug: str,
                    chains: set[str], seen_run: set[str]) -> tuple[set[str], int | None, list[str]]:
    """Обходить сторінки однієї категорії. -> (унікальні product_id, total з 1-ї сторінки, subcategories 1-ї сторінки)."""
    seen: set[str] = set()
    total, subcats = None, []
    for page in range(1, cfg.max_pages_per_category + 1):
        html = fetcher.get(category_url(cfg, slug, page), f"{run_id}/listing/{slug}_p{page}.html.gz")
        parsed = parse_listing(html)
        if page == 1:
            total, subcats = parsed.total, parsed.subcategories
        if not parsed.tiles:
            break
        new_ids = {t.product_id for t in parsed.tiles} - seen
        if not new_ids:
            break
        for t in parsed.tiles:
            db.upsert_product_from_tile(conn, t, classify_id(t.product_id, chains),
                                        parse_weight(t.weight_text), now)
            conn.execute("INSERT OR IGNORE INTO product_categories (product_id, slug, source) "
                         "VALUES (?, ?, 'listing')", (t.product_id, slug))
            conn.execute(
                """INSERT OR REPLACE INTO listing_prices (run_id, product_id, city, category_slug, scraped_at,
                       price_min_kop, price_max_kop, is_promo, page, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, t.product_id, cfg.city, slug, now, t.price_min_kop, t.price_max_kop,
                 int(t.is_promo), page, t.position))
        seen |= new_ids
        seen_run |= new_ids
        conn.commit()
    else:
        log.warning("Категорія %s: досягнуто max_pages_per_category=%d", slug, cfg.max_pages_per_category)
    return seen, total, subcats


def _listings(conn: sqlite3.Connection, fetcher: Fetcher, cfg: Config, run_id: str) -> tuple[str, dict, str | None]:
    """-> (status, stats, stop_reason)."""
    now = _run_started_at(conn, run_id)
    chains = db.known_chain_ids(conn)
    if not chains:
        log.warning("Таблиця chains порожня: виконайте `seed`, інакше товари мереж отримають id_type='other'")
    stats: dict = {"categories_ok": [], "categories_failed": [], "warnings": []}
    seen_run: set[str] = set()
    per_category: dict[str, set[str]] = {}

    root = cfg.root_category
    db.upsert_category(conn, root, None, 1, None)
    try:
        # Корінь: першим, щоб узяти список підкатегорій і total.
        root_seen, total, subcats = _crawl_category(conn, fetcher, cfg, run_id, now, root, chains, seen_run)
        per_category[root] = root_seen
        stats["categories_ok"].append(root)
        stats["root_total"] = total
        stats["root_unique"] = len(root_seen)

        if subcats != KNOWN_SUBCATEGORIES:
            msg = f"Підкатегорії відрізняються від 9 відомих: {subcats}"
            log.warning(msg)
            stats["warnings"].append(msg)
        for sub in subcats:
            db.upsert_category(conn, sub, None, 2, root)
            try:
                per_category[sub], _, _ = _crawl_category(conn, fetcher, cfg, run_id, now, sub, chains, seen_run)
                stats["categories_ok"].append(sub)
            except FetchError as e:
                log.error("Категорія %s не оброблена: %s", sub, e)
                stats["categories_failed"].append(sub)
    except StopCrawl as e:
        conn.commit()
        stats["requests"] = fetcher.requests
        return "stopped", stats, str(e)
    except FetchError as e:
        # Збій на кореневій категорії: далі йти нема з чим.
        log.error("Коренева категорія не оброблена: %s", e)
        stats["categories_failed"].append(root)
        conn.commit()
        stats["requests"] = fetcher.requests
        return "failed", stats, str(e)

    if total and abs(len(root_seen) - total) / total > 0.02:
        msg = f"Унікальних товарів {len(root_seen)} проти total={total} (розбіжність понад 2%)"
        log.warning(msg)
        stats["warnings"].append(msg)

    in_subs = set().union(*(per_category[s] for s in subcats if s in per_category)) if subcats else set()
    orphans = root_seen - in_subs
    stats["root_only_products"] = len(orphans)
    if orphans:
        log.info("У батьківській категорії, але в жодній підкатегорії: %d товарів", len(orphans))

    status = "failed" if stats["categories_failed"] else "ok"
    if status == "ok":
        stale = [r[0] for r in conn.execute("SELECT product_id FROM products WHERE is_active = 1")
                 if r[0] not in seen_run]
        conn.executemany("UPDATE products SET is_active = 0 WHERE product_id = ?", [(p,) for p in stale])
        stats["deactivated"] = len(stale)
    stats["products_seen"] = len(seen_run)
    stats["requests"] = fetcher.requests
    conn.commit()
    return status, stats, None


def crawl_listings(conn: sqlite3.Connection, fetcher: Fetcher, cfg: Config, kind: str = "listings") -> tuple[str, str]:
    """-> (run_id, status)."""
    run_id = db.start_run(conn, kind, cfg.city)
    status, stats, reason = _listings(conn, fetcher, cfg, run_id)
    db.finish_run(conn, run_id, status, stats, reason)
    return run_id, status


_STALE = re.compile(r"^stale(?::(\d+))?$")


def select_for_details(conn: sqlite3.Connection, mode: str, limit: int | None = None) -> list[tuple[str, str]]:
    """-> [(product_id, url)] для mode = new | all | stale[:days]."""
    if mode == "new":
        where, params = "details_scraped_at IS NULL", ()
    elif mode == "all":
        where, params = "1=1", ()
    elif m := _STALE.match(mode):
        days = int(m.group(1) or 7)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        where, params = "(details_scraped_at IS NULL OR details_scraped_at < ?)", (cutoff,)
    else:
        raise ValueError(f"Невідомий режим: {mode!r} (new | all | stale:<днів>)")
    sql = f"SELECT product_id, url FROM products WHERE is_active = 1 AND {where} ORDER BY first_seen, product_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(r[0], r[1]) for r in conn.execute(sql, params)]


def save_details(conn: sqlite3.Connection, product_id: str, details, cfg: Config, now: str) -> None:
    crumbs = {level: (slug, name) for level, slug, name in details.breadcrumbs}
    prev = None
    for level, slug, name in details.breadcrumbs:
        db.upsert_category(conn, slug, name, level, prev)
        conn.execute("INSERT OR IGNORE INTO product_categories (product_id, slug, source) "
                     "VALUES (?, ?, 'breadcrumb')", (product_id, slug))
        prev = slug

    conn.execute("DELETE FROM product_attributes WHERE product_id = ?", (product_id,))
    conn.executemany(
        "INSERT INTO product_attributes (product_id, attr_key, attr_title, attr_value) VALUES (?, ?, ?, ?)",
        [(product_id, k, t, v) for k, (t, v) in details.attributes.items()])

    cols = details.columns()
    sets = {col: cols.get(col) for col in ATTR_TO_COLUMN.values()}
    sets.update(
        composition=details.composition,
        kcal_100g=details.kcal, protein_100g=details.protein, fat_100g=details.fat,
        carbs_100g=details.carbs,
        description=details.description if cfg.store_descriptions else None,
        cat_l1=crumbs.get(1, (None,))[0], cat_l2=crumbs.get(2, (None,))[0], cat_l3=crumbs.get(3, (None,))[0],
        details_scraped_at=now,
    )
    assignments = ", ".join(f"{c} = ?" for c in sets)
    conn.execute(f"UPDATE products SET {assignments} WHERE product_id = ?", (*sets.values(), product_id))


def _details(conn, fetcher, cfg, run_id, mode, limit) -> tuple[str, dict, str | None]:
    todo = select_for_details(conn, mode, limit)
    stats = {"selected": len(todo), "done": 0, "failed": []}
    for product_id, url in todo:
        try:
            html = fetcher.get(url, f"{run_id}/product/{product_id}.html.gz")
        except StopCrawl as e:
            conn.commit()
            stats["requests"] = fetcher.requests
            return "stopped", stats, str(e)
        except FetchError as e:
            log.error("Товар %s не завантажено: %s", product_id, e)
            stats["failed"].append(product_id)
            continue
        details = parse_product(html, cfg.store_descriptions)
        save_details(conn, product_id, details, cfg, db.utc_now())
        conn.commit()
        stats["done"] += 1
    stats["requests"] = fetcher.requests
    return ("failed" if stats["failed"] else "ok"), stats, None


def crawl_details(conn: sqlite3.Connection, fetcher: Fetcher, cfg: Config, mode: str = "new",
                  limit: int | None = None, kind: str = "details") -> tuple[str, str]:
    select_for_details(conn, mode, 0)  # валідація режиму до створення run
    run_id = db.start_run(conn, kind, cfg.city)
    status, stats, reason = _details(conn, fetcher, cfg, run_id, mode, limit)
    db.finish_run(conn, run_id, status, stats, reason)
    return run_id, status


def crawl_daily(conn: sqlite3.Connection, fetcher: Fetcher, cfg: Config) -> tuple[str, str]:
    run_id = db.start_run(conn, "daily", cfg.city)
    status, stats, reason = _listings(conn, fetcher, cfg, run_id)
    all_stats = {"listings": stats}
    if status != "stopped":
        d_status, d_stats, d_reason = _details(conn, fetcher, cfg, run_id, "new", None)
        all_stats["details"] = d_stats
        if d_status != "ok" and status == "ok":
            status, reason = d_status, d_reason
        elif d_status == "stopped":
            status, reason = d_status, d_reason
    db.finish_run(conn, run_id, status, all_stats, reason)
    return run_id, status
