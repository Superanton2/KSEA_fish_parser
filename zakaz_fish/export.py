import csv
import sqlite3
from pathlib import Path

from openpyxl import Workbook

SHEETS = {
    "products": "SELECT * FROM products ORDER BY product_id",
    "listing_prices_latest": None,  # формується окремо
    "categories": "SELECT * FROM categories ORDER BY level, slug",
    "chains": "SELECT * FROM chains ORDER BY chain_id",
    "stores": "SELECT * FROM stores ORDER BY chain_id, store_id",
}

LATEST_RUN_SQL = ("SELECT run_id FROM runs WHERE kind IN ('listings', 'daily') AND status = 'ok' "
                  "ORDER BY started_at DESC, run_id DESC LIMIT 1")


def _query(conn: sqlite3.Connection, name: str) -> tuple[list[str], list[tuple]]:
    if name == "listing_prices_latest":
        latest = conn.execute(LATEST_RUN_SQL).fetchone()
        cur = conn.execute("SELECT * FROM listing_prices WHERE run_id = ? ORDER BY category_slug, page, position",
                           (latest[0] if latest else None,))
    else:
        cur = conn.execute(SHEETS[name])
    return [d[0] for d in cur.description], [tuple(r) for r in cur.fetchall()]


def export_all(conn: sqlite3.Connection, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    for name in ("products", "listing_prices_latest"):
        header, rows = _query(conn, name)
        path = out / f"{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM для Excel на macOS
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        written.append(path)

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        header, rows = _query(conn, name)
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(list(r))
    xlsx = out / "zakaz_fish.xlsx"
    wb.save(xlsx)
    written.append(xlsx)
    return written
