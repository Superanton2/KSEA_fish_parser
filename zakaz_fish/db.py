import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  city TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  stop_reason TEXT,
  stats_json TEXT
);

CREATE TABLE IF NOT EXISTS chains (
  chain_id TEXT PRIMARY KEY, 
  name TEXT, 
  hostname TEXT, 
  logo_url TEXT
);

CREATE TABLE IF NOT EXISTS stores (
  store_id TEXT PRIMARY KEY,
  chain_id TEXT REFERENCES chains(chain_id),
  zone_hint TEXT,
  city TEXT,
  source TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  slug TEXT PRIMARY KEY, name TEXT, level INTEGER, parent_slug TEXT
);

CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY,
  id_type TEXT NOT NULL,
  ean13 TEXT, 
  ean13_valid INTEGER,
  chain_id TEXT,
  url TEXT NOT NULL, 
  slug TEXT,
  title TEXT,
  weight_text TEXT,
  unit_kind TEXT,
  pack_qty REAL, 
  pack_unit TEXT,
  image_url TEXT,
  brand TEXT, 
  producer TEXT, 
  country TEXT, 
  species TEXT,
  shelf_life TEXT, 
  storage_temp TEXT,
  composition TEXT,
  kcal_100g REAL, 
  protein_100g REAL, 
  fat_100g REAL, 
  carbs_100g REAL,
  description TEXT,
  cat_l1 TEXT, 
  cat_l2 TEXT, 
  cat_l3 TEXT,
  first_seen TEXT NOT NULL, 
  last_seen TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  details_scraped_at TEXT,
  details_status TEXT               -- ok | empty | not_found | error
);

CREATE TABLE IF NOT EXISTS product_categories (
  product_id TEXT REFERENCES products(product_id),
  slug TEXT REFERENCES categories(slug),
  source TEXT NOT NULL,
  PRIMARY KEY (product_id, slug, source)
);

CREATE TABLE IF NOT EXISTS product_attributes (
  product_id TEXT REFERENCES products(product_id),
  attr_key TEXT, attr_title TEXT, attr_value TEXT,
  PRIMARY KEY (product_id, attr_key)
);

CREATE TABLE IF NOT EXISTS listing_prices (
  run_id TEXT REFERENCES runs(run_id),
  product_id TEXT REFERENCES products(product_id),
  city TEXT NOT NULL,
  category_slug TEXT NOT NULL,
  scraped_at TEXT NOT NULL,
  price_min_kop INTEGER, price_max_kop INTEGER,
  is_promo INTEGER,
  page INTEGER, position INTEGER,
  PRIMARY KEY (run_id, product_id, category_slug)
);

CREATE TABLE IF NOT EXISTS store_prices (
  run_id TEXT, product_id TEXT, scraped_at TEXT,
  chain_id TEXT, store_id TEXT,
  available INTEGER,
  price_kop INTEGER, full_price_kop INTEGER,
  discount_percent INTEGER, discount_date_to TEXT,
  store_ean TEXT,
  PRIMARY KEY (run_id, product_id, store_id)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Колонки, додані після першої версії схеми: (таблиця, колонка, тип)
MIGRATIONS = [
    ("products", "details_status", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ctype in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def upsert_chains(conn: sqlite3.Connection, chains) -> None:
    conn.executemany(
        """INSERT INTO chains (chain_id, name, hostname, logo_url) VALUES (?, ?, ?, ?)
           ON CONFLICT(chain_id) DO UPDATE SET
             name = excluded.name, hostname = excluded.hostname, logo_url = excluded.logo_url""",
        [(c.chain_id, c.name, c.hostname, c.logo_url) for c in chains])


def upsert_stores(conn: sqlite3.Connection, refs, source: str) -> None:
    # city не чіпаємо при повторному seed: його заповнюють вручну
    conn.executemany(
        """INSERT INTO stores (store_id, chain_id, zone_hint, source) VALUES (?, ?, ?, ?)
           ON CONFLICT(store_id) DO UPDATE SET
             chain_id = excluded.chain_id, zone_hint = excluded.zone_hint, source = excluded.source""",
        [(r.store_id, r.chain_id, r.zone_hint, source) for r in refs])


def start_run(conn: sqlite3.Connection, kind: str, city: str) -> str:
    """Створює рядок у runs зі статусом running; run_id унікальний навіть в межах секунди."""
    base = new_run_id()
    run_id, n = base, 1
    while conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
        n += 1
        run_id = f"{base}-{n}"
    conn.execute("INSERT INTO runs (run_id, kind, city, started_at, status) VALUES (?, ?, ?, ?, 'running')",
                 (run_id, kind, city, utc_now()))
    conn.commit()
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: str, status: str,
               stats: dict | None = None, stop_reason: str | None = None) -> None:
    import json
    conn.execute("UPDATE runs SET finished_at = ?, status = ?, stop_reason = ?, stats_json = ? WHERE run_id = ?",
                 (utc_now(), status, stop_reason, json.dumps(stats or {}, ensure_ascii=False), run_id))
    conn.commit()


def known_chain_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT chain_id FROM chains")}


def upsert_category(conn: sqlite3.Connection, slug: str, name: str | None,
                    level: int | None, parent_slug: str | None) -> None:
    conn.execute(
        """INSERT INTO categories (slug, name, level, parent_slug) VALUES (?, ?, ?, ?)
           ON CONFLICT(slug) DO UPDATE SET
             name = COALESCE(excluded.name, name),
             level = COALESCE(excluded.level, level),
             parent_slug = COALESCE(excluded.parent_slug, parent_slug)""",
        (slug, name, level, parent_slug))


def upsert_product_from_tile(conn: sqlite3.Connection, tile, ids: tuple, weight: tuple, now: str) -> None:
    """ids = classify_id(...), weight = parse_weight(...). first_seen не змінюється, is_active повертається в 1."""
    id_type, ean13, ean13_valid, chain_id = ids
    unit_kind, pack_qty, pack_unit = weight
    conn.execute(
        """INSERT INTO products (product_id, id_type, ean13, ean13_valid, chain_id, url, slug, title,
                                 weight_text, unit_kind, pack_qty, pack_unit, image_url,
                                 first_seen, last_seen, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(product_id) DO UPDATE SET
             id_type = excluded.id_type, ean13 = excluded.ean13, ean13_valid = excluded.ean13_valid,
             chain_id = excluded.chain_id, url = excluded.url, slug = excluded.slug,
             title = excluded.title, weight_text = excluded.weight_text,
             unit_kind = excluded.unit_kind, pack_qty = excluded.pack_qty, pack_unit = excluded.pack_unit,
             image_url = excluded.image_url, last_seen = excluded.last_seen, is_active = 1""",
        (tile.product_id, id_type, ean13, None if ean13_valid is None else int(ean13_valid), chain_id,
         tile.url, tile.slug, tile.title, tile.weight_text, unit_kind, pack_qty, pack_unit,
         tile.image_url, now, now))
