"""Обробники задач знімка (SPEC v2, 8.3–8.7). Обробник не комітить: транзакцію веде runner.

Обробник отримує вже перевірену відповідь `ok`. Структурні проблеми сторінки сигналізує `Unexpected`.
"""
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import db, tasks
from .config import Config
from .crawl import save_details
from .fish import fish_match
from .ids import classify_id
from .parse_listing import parse_listing
from .parse_product import parse_product
from .parse_storefront import parse_storefront_home, parse_storefront_listing
from .parse_stores import parse_store_detail, parse_store_list
from .prices import parse_weight, until_to_date

log = logging.getLogger(__name__)

# Назви верхніх категорій вітрин, у яких шукаємо рибні підрозділи (перевірено на novus: «Консерви», «Заморозка»;
# «Чипси та снеки» / «Снеки» взято з ТЗ і не підтверджено зразком: якщо не знайдено, це піде у звіт якості).
PARENT_NAMES = {"Консерви": ("canned",), "Чипси та снеки": ("snacks",), "Снеки": ("snacks",),
                "Заморозка": ("frozen", "semi")}


class Unexpected(Exception):
    """Сторінка 200, але без очікуваної структури. keep=True: зберегти запис обробника (напр. details_status)."""

    def __init__(self, msg: str, keep: bool = False):
        super().__init__(msg)
        self.keep = keep


@dataclass
class Ctx:
    conn: sqlite3.Connection
    cfg: Config
    snapshot_id: str
    snap_date: date
    chains: set[str] = field(default_factory=set)
    hostnames: dict[str, str] = field(default_factory=dict)
    last_final_url: str | None = None   # фінальний URL останньої відповіді (для перевірки редіректу вітрини)

    @property
    def now(self) -> str:
        return db.utc_now()


# --- допоміжне ---

def city_url(cfg: Config, city: str, slug: str, page: int = 1) -> str:
    u = f"https://zakaz.ua/{cfg.lang}/{city}/{slug}/"
    return u if page == 1 else f"{u}?page={page}"


def chain_url(ctx: Ctx, chain: str, path: str, page: int = 1) -> str:
    u = f"https://{ctx.hostnames[chain]}/{ctx.cfg.lang}/{path}/"
    return u if page == 1 else f"{u}?page={page}"


class _Tile:
    """Мінімальний аналог Tile для db.upsert_product_from_tile."""

    def __init__(self, product_id, url, slug, title, weight_text, image_url=None):
        self.product_id, self.url, self.slug, self.title = product_id, url, slug, title
        self.weight_text, self.image_url = weight_text, image_url


def _category_name(h1: str | None, city_name: str | None) -> str | None:
    if h1 and city_name and h1.endswith(f" у місті {city_name}"):
        return h1[: -len(f" у місті {city_name}")].strip()
    return h1


def _bump_stats(ctx: Ctx, city: str, cat: str, scope: str, **inc) -> None:
    ctx.conn.execute("INSERT OR IGNORE INTO city_category_stats (snapshot_id, city_slug, category_slug, scope) "
                     "VALUES (?, ?, ?, ?)", (ctx.snapshot_id, city, cat, scope))
    sets = ", ".join(f"{k} = COALESCE({k}, 0) + ?" for k in inc if k not in ("site_total", "status"))
    params = [v for k, v in inc.items() if k not in ("site_total", "status")]
    if sets:
        ctx.conn.execute(f"UPDATE city_category_stats SET {sets} WHERE snapshot_id = ? AND city_slug = ? "
                         "AND category_slug = ?", (*params, ctx.snapshot_id, city, cat))
    for k in ("site_total", "status"):
        if k in inc:
            ctx.conn.execute(f"UPDATE city_category_stats SET {k} = ? WHERE snapshot_id = ? AND city_slug = ? "
                             "AND category_slug = ?", (inc[k], ctx.snapshot_id, city, cat))
    ctx.conn.execute("UPDATE city_category_stats SET unique_seen = (SELECT COUNT(*) FROM city_listing_rows "
                     "WHERE snapshot_id = ? AND city_slug = ? AND category_slug = ?) "
                     "WHERE snapshot_id = ? AND city_slug = ? AND category_slug = ?",
                     (ctx.snapshot_id, city, cat, ctx.snapshot_id, city, cat))


# --- S3: каталог агрегатора ---

def city_listing(ctx: Ctx, task, html: str, p: dict) -> None:
    conn, cfg, sid = ctx.conn, ctx.cfg, ctx.snapshot_id
    city, scope, cat, page = p["city"], p["scope"], p["category"], p["page"]
    parsed = parse_listing(html, cat)
    if not parsed.h1 or not parsed.city_name:
        raise Unexpected(f"немає H1 з містом на {task['url']}")
    row = conn.execute("SELECT name_uk FROM cities WHERE city_slug = ?", (city,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO cities (city_slug, name_uk, source) VALUES (?, ?, 'selector')",
                     (city, parsed.city_name))
    elif row["name_uk"] is None:
        conn.execute("UPDATE cities SET name_uk = ? WHERE city_slug = ?", (parsed.city_name, city))
    elif row["name_uk"] != parsed.city_name:
        raise Unexpected(f"H1 містить місто {parsed.city_name!r}, очікувалось {row['name_uk']!r}")

    if page == 1:
        db.upsert_category(conn, cat, _category_name(parsed.h1, parsed.city_name), 1 if p.get("root") else 2,
                           p.get("parent"), scope)
        _bump_stats(ctx, city, cat, scope, site_total=parsed.total)
    if not parsed.tiles:
        if page == 1:
            _bump_stats(ctx, city, cat, scope, status="not_found")   # порожня категорія: 200, H1, 0 карток
        return

    keywords = cfg.snapshot.fish_keywords if cfg.snapshot.scope_filters.get(scope) == "fish_keywords" else None
    now = ctx.now
    in_stock = [t for t in parsed.tiles if t.in_stock]
    kept = filtered = 0
    for t in in_stock:
        if keywords is not None and not fish_match(t.title, keywords):
            filtered += 1
            continue
        db.upsert_product_from_tile(conn, t, classify_id(t.product_id, ctx.chains), parse_weight(t.weight_text), now)
        conn.execute("UPDATE products SET first_source = 'aggregator' WHERE product_id = ? AND first_source IS NULL",
                     (t.product_id,))
        conn.execute("INSERT OR IGNORE INTO product_categories (product_id, slug, source) VALUES (?, ?, 'listing')",
                     (t.product_id, cat))
        conn.execute(
            """INSERT OR REPLACE INTO city_listing_rows (snapshot_id, city_slug, category_slug, scope, product_id,
                   page, position, price_min_kop, price_max_kop, is_promo, weight_text, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, city, cat, scope, t.product_id, page, t.position, t.price_min_kop, t.price_max_kop,
             int(t.is_promo), t.weight_text, now))
        kept += 1
    _bump_stats(ctx, city, cat, scope, pages_fetched=1, filtered_out=filtered,
                out_of_stock=len(parsed.tiles) - len(in_stock), status="ok")

    # підрозділи: з кореня розділу; для змішаного `semi` корінь уже містить усе (рішення за замовчуванням)
    if page == 1 and p.get("root") and scope != "semi":
        for sub in parsed.subcategories:
            db.upsert_category(conn, sub, None, 2, cat, scope)
            tasks.add_task(conn, sid, "city_listing", f"{city}|{sub}|1", city_url(cfg, city, sub),
                           {"city": city, "scope": scope, "category": sub, "page": 1, "root": False, "parent": cat})

    # наступна сторінка: сторінка повна й ще не набрали total (доступних товарів)
    size = p.get("size") or len(parsed.tiles)
    seen = p.get("seen", 0) + len(in_stock)
    first_id = p.get("first_id") or parsed.tiles[0].product_id
    again = page > 1 and parsed.tiles[0].product_id == first_id
    if len(parsed.tiles) >= size and not again and (parsed.total is None or seen < (parsed.total or 0)) \
            and page < cfg.max_pages_per_category:
        tasks.add_task(conn, sid, "city_listing", f"{city}|{cat}|{page + 1}", city_url(cfg, city, cat, page + 1),
                       {**p, "page": page + 1, "size": size, "seen": seen, "first_id": first_id})


def city_listing_not_found(ctx: Ctx, task, resp, p: dict) -> str:
    _bump_stats(ctx, p["city"], p["category"], p["scope"], status="not_found")
    return "skipped"


def city_listing_redirect(ctx: Ctx, task, resp, p: dict) -> str:
    if p["page"] >= 2:
        return "done"                                   # ?page=N+1 -> сторінка 1: кінець пагінації
    if f"/{p['city']}/" in (resp.location or ""):
        _bump_stats(ctx, p["city"], p["category"], p["scope"], status="not_found")
        return "skipped"
    raise Unexpected(f"редірект на {resp.location}")


# --- S4: картки товарів ---

def product_detail(ctx: Ctx, task, html: str, p: dict) -> None:
    pid = p["product_id"]
    final_path = urlsplit(ctx.last_final_url or task["url"]).path
    if "/products/" not in final_path:
        # Сайт відправляє недоступну для гостя без адреси картку на головну (307 -> /uk/) замість 404.
        # Пілот 02.11.2026: усі такі товари були лише на вітринах. Це стан «немає картки», а не зміна верстки.
        log.info("Картка %s редіректить на %s: details_status=not_found", pid, ctx.last_final_url)
        ctx.conn.execute("UPDATE products SET details_status = 'not_found' WHERE product_id = ?", (pid,))
        return
    d = parse_product(html, ctx.cfg.store_descriptions)
    if d.is_empty:
        ctx.conn.execute("UPDATE products SET details_status = 'empty' WHERE product_id = ?", (pid,))
        raise Unexpected(f"картка {pid} без хлібних крихт і Taxon", keep=True)
    save_details(ctx.conn, pid, d, ctx.cfg, ctx.now)
    # хлібні крихти без scope: категорії, відомі з каталогів, свій scope зберігають (COALESCE)
    ctx.conn.execute(
        "UPDATE products SET meta_description = ?, meta_price_min_kop = ?, meta_price_max_kop = ?, "
        "meta_chain_names = ? WHERE product_id = ?",
        (d.meta_description, d.meta_price_min_kop, d.meta_price_max_kop,
         json.dumps(d.meta_chain_names, ensure_ascii=False), pid))


def product_detail_not_found(ctx: Ctx, task, resp, p: dict) -> str:
    ctx.conn.execute("UPDATE products SET details_status = 'not_found' WHERE product_id = ?", (p["product_id"],))
    return "done"


# --- S5: вітрини ---

def _classify_chain_category(name: str, slug: str, scopes) -> str | None:
    n = (name or "").strip()
    rules = {
        "canned": (n == "Рибна консервація", "canned-fish" in slug),
        "snacks": (n == "Рибні снеки", "fish-snacks" in slug),
        "frozen": (n == "Риба та морепродукти заморожені", "frozen-fish" in slug),
        "semi": (n.startswith("Напівфабрикати"), "half-made" in slug),
    }
    for scope in scopes:
        if scope == "core":
            if n == "Риба та морепродукти" or slug.startswith("fish-and-seafood"):
                return "core"
        elif any(rules.get(scope, ())):
            return scope
    return None


def _register_chain_category(ctx: Ctx, chain: str, slug: str, name: str, parent: str | None,
                             scope: str, matched_by: str) -> None:
    ctx.conn.execute("INSERT OR REPLACE INTO chain_categories (chain_id, slug, name, parent_slug, scope, matched_by) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (chain, slug, name, parent, scope, matched_by))
    if ctx.cfg.snapshot.storefront_listings and scope in ctx.cfg.snapshot.scopes:
        tasks.add_task(ctx.conn, ctx.snapshot_id, "chain_listing", f"{chain}|{slug}|1",
                       chain_url(ctx, chain, f"categories/{slug}"),
                       {"chain_id": chain, "category": slug, "scope": scope, "page": 1})


def _set_storefront(ctx: Ctx, chain: str, status: str, notes: str | None = None) -> None:
    ctx.conn.execute("INSERT OR REPLACE INTO chain_storefronts (snapshot_id, chain_id, hostname, status, "
                     "checked_at, notes) VALUES (?, ?, ?, ?, ?, ?)",
                     (ctx.snapshot_id, chain, ctx.hostnames.get(chain), status, ctx.now, notes))


def storefront_home(ctx: Ctx, task, html: str, p: dict) -> None:
    chain = p["chain_id"]
    host = ctx.hostnames[chain]
    if urlsplit(ctx.last_final_url or task["url"]).hostname != host:
        _set_storefront(ctx, chain, "unavailable", f"редірект на {ctx.last_final_url}")
        return
    cats = parse_storefront_home(html, task["url"])
    if not cats:
        raise Unexpected(f"на головній {host} немає категорій")
    _set_storefront(ctx, chain, "ok")
    scopes = list(ctx.cfg.snapshot.scopes)
    overrides = ctx.cfg.storefront_overrides.get(chain, {})
    for scope, slug in overrides.items():
        if scope in scopes:
            _register_chain_category(ctx, chain, slug, slug, None, scope, "override")
    open_scopes = [s for s in scopes if s not in overrides]
    for c in cats:
        scope = _classify_chain_category(c.name, c.slug, [s for s in open_scopes if s == "core"])
        if scope:
            _register_chain_category(ctx, chain, c.slug, c.name, None, scope, "name")
        wanted = [s for s in PARENT_NAMES.get(c.name, ()) if s in open_scopes]
        if wanted:
            tasks.add_task(ctx.conn, ctx.snapshot_id, "storefront_parent", f"{chain}|{c.slug}", c.url,
                           {"chain_id": chain, "parent": c.slug, "scopes": wanted})


def storefront_parent(ctx: Ctx, task, html: str, p: dict) -> None:
    chain = p["chain_id"]
    page = parse_storefront_listing(html, task["url"])
    if page.h1 is None:
        raise Unexpected(f"немає H1 на {task['url']}")
    for sub in page.subcategories:
        scope = _classify_chain_category(sub.name, sub.slug, p["scopes"])
        if scope:
            _register_chain_category(ctx, chain, sub.slug, sub.name, p["parent"], scope,
                                     "name" if sub.name else "slug")


def chain_listing(ctx: Ctx, task, html: str, p: dict) -> None:
    conn, sid = ctx.conn, ctx.snapshot_id
    chain, cat, scope, page = p["chain_id"], p["category"], p["scope"], p["page"]
    parsed = parse_storefront_listing(html, task["url"])
    if parsed.h1 is None:
        raise Unexpected(f"немає H1 на {task['url']}")
    if not parsed.tiles:
        return
    keywords = ctx.cfg.snapshot.fish_keywords if ctx.cfg.snapshot.scope_filters.get(scope) == "fish_keywords" else None
    now = ctx.now
    for t in parsed.tiles:
        if t.price_kop is None:
            continue
        if keywords is not None and not fish_match(t.title, keywords):
            continue
        url = "https://zakaz.ua" + t.product_path
        if conn.execute("SELECT 1 FROM products WHERE product_id = ?", (t.product_id,)).fetchone():
            conn.execute("UPDATE products SET last_seen = ?, is_active = 1 WHERE product_id = ?", (now, t.product_id))
        else:
            db.upsert_product_from_tile(conn, _Tile(t.product_id, url, t.slug, t.title, t.weight_text),
                                        classify_id(t.product_id, ctx.chains), parse_weight(t.weight_text), now)
            conn.execute("UPDATE products SET first_source = 'storefront' WHERE product_id = ?", (t.product_id,))
        conn.execute(
            """INSERT OR REPLACE INTO chain_listing_rows (snapshot_id, chain_id, category_slug, scope, product_id,
                   page, position, price_kop, old_price_kop, discount_percent, discount_until_text,
                   discount_until_date, weight_text, product_url, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, chain, cat, scope, t.product_id, page, t.position, t.price_kop, t.old_price_kop,
             t.discount_percent, t.discount_until_text, until_to_date(t.discount_until_text, ctx.snap_date),
             t.weight_text, url, now))
    size = p.get("size") or len(parsed.tiles)
    seen = p.get("seen", 0) + len(parsed.tiles)
    first_id = p.get("first_id") or parsed.tiles[0].product_id
    again = page > 1 and parsed.tiles[0].product_id == first_id
    if len(parsed.tiles) >= size and not again and (parsed.total is None or seen < parsed.total) \
            and page < ctx.cfg.max_pages_per_category:
        tasks.add_task(conn, sid, "chain_listing", f"{chain}|{cat}|{page + 1}",
                       chain_url(ctx, chain, f"categories/{cat}", page + 1),
                       {**p, "page": page + 1, "size": size, "seen": seen, "first_id": first_id})


def chain_listing_redirect(ctx: Ctx, task, resp, p: dict) -> str:
    if p["page"] >= 2:
        return "done"
    raise Unexpected(f"редірект на {resp.location}")


# --- S6: магазини ---

def store_list(ctx: Ctx, task, html: str, p: dict) -> None:
    conn, sid = ctx.conn, ctx.snapshot_id
    chain, page = p["chain_id"], p["page"]
    parsed = parse_store_list(html, task["url"])
    if not parsed.stores:
        if page == 1:
            raise Unexpected(f"у списку магазинів {chain} немає магазинів")
        return
    for s in parsed.stores:
        conn.execute(
            """INSERT INTO store_locations (store_url, chain_id, city_slug, city_name, address, hours_text,
                   first_seen_snapshot, last_seen_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(store_url) DO UPDATE SET city_slug = excluded.city_slug, city_name = excluded.city_name,
                   address = excluded.address, hours_text = excluded.hours_text,
                   last_seen_snapshot = excluded.last_seen_snapshot""",
            (s.store_url, chain, s.city_slug, s.city_name, s.address, s.hours_text, sid, sid))
        if ctx.cfg.snapshot.store_details:
            limit = ctx.cfg.snapshot.max_store_details
            have = conn.execute("SELECT COUNT(*) FROM tasks WHERE snapshot_id = ? AND stage = 'store_detail'",
                                (sid,)).fetchone()[0]
            if not limit or have < limit:
                tasks.add_task(conn, sid, "store_detail", s.store_url, s.store_url,
                               {"chain_id": chain, "store_url": s.store_url})
    if page + 1 in parsed.page_numbers or (not parsed.page_numbers and len(parsed.stores) >= 30):
        tasks.add_task(conn, sid, "store_list", f"{chain}|stores|{page + 1}",
                       chain_url(ctx, chain, "stores", page + 1), {"chain_id": chain, "page": page + 1})


def store_list_redirect(ctx: Ctx, task, resp, p: dict) -> str:
    if p["page"] >= 2:
        return "done"
    raise Unexpected(f"редірект на {resp.location}")


def store_detail(ctx: Ctx, task, html: str, p: dict) -> None:
    d = parse_store_detail(html)
    if not d.address and not d.services:
        raise Unexpected(f"сторінка магазину без адреси: {p['store_url']}")
    ctx.conn.execute(
        "UPDATE store_locations SET services_json = ?, address = COALESCE(?, address), "
        "hours_text = COALESCE(?, hours_text), detail_scraped_at = ? WHERE store_url = ?",
        (json.dumps(d.services, ensure_ascii=False), d.address, d.hours_text, ctx.now, p["store_url"]))


# --- S7: перевірка міст ---

def city_probe(ctx: Ctx, task, html: str, p: dict) -> None:
    slug = p["city"]
    parsed = parse_listing(html, ctx.cfg.snapshot.scope_roots.get("core", "fish-and-seafood-zakaz"))
    soup = BeautifulSoup(html, "lxml")
    crumb = soup.select_one(f'a[data-marker^="Breadcrumb Category"][href="/{ctx.cfg.lang}/{slug}/"]')
    valid = bool(parsed.city_name and crumb and (parsed.total or 0) > 0)
    ctx.conn.execute(
        "INSERT INTO cities (city_slug, name_uk, source, is_valid, validated_at, probe_total) "
        "VALUES (?, ?, 'discovered', ?, ?, ?) ON CONFLICT(city_slug) DO UPDATE SET name_uk = excluded.name_uk, "
        "is_valid = excluded.is_valid, validated_at = excluded.validated_at, probe_total = excluded.probe_total",
        (slug, parsed.city_name, int(valid), ctx.now, parsed.total))


def city_probe_invalid(ctx: Ctx, task, resp, p: dict) -> str:
    ctx.conn.execute(
        "INSERT INTO cities (city_slug, source, is_valid, validated_at) VALUES (?, 'discovered', 0, ?) "
        "ON CONFLICT(city_slug) DO UPDATE SET is_valid = 0, validated_at = excluded.validated_at",
        (p["city"], ctx.now))
    return "done"


# stage -> (handler, follow_redirects, not_found-hook, redirect-hook)
STAGES = {
    "storefront_home": (storefront_home, True, None, None),
    "storefront_parent": (storefront_parent, True, None, None),
    "store_list": (store_list, False, None, store_list_redirect),
    "city_listing": (city_listing, False, city_listing_not_found, city_listing_redirect),
    "chain_listing": (chain_listing, False, None, chain_listing_redirect),
    "product_detail": (product_detail, True, product_detail_not_found, None),
    "store_detail": (store_detail, True, None, None),
    "city_probe": (city_probe, False, city_probe_invalid, city_probe_invalid),
}
