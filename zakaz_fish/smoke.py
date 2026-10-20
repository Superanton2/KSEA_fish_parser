import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import Config
from .crawl import category_url
from .http import FetchError, Fetcher, StopCrawl
from .parse_listing import parse_listing
from .parse_product import parse_product

LIVE_DIR = Path("tests/fixtures/live")


@dataclass
class Check:
    name: str
    ok: bool
    found: str


def _save(live_dir: Path, name: str, html: str) -> None:
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / name).write_text(html, encoding="utf-8")


def run_smoke(conn: sqlite3.Connection, cfg: Config, fetcher: Fetcher,
              live_dir: Path = LIVE_DIR) -> tuple[str, list[Check]]:
    """Рівно 3 запити до живого сайту. Нічого не «лагодить». -> (run_id, checks)."""
    run_id = db.start_run(conn, "smoke", cfg.city)
    checks: list[Check] = []
    stop_reason = None
    try:
        # 1. Перша сторінка кореневої категорії
        url1 = category_url(cfg, cfg.root_category, 1)
        html1 = fetcher.get(url1)
        _save(live_dir, "listing_p1.html", html1)
        p1 = parse_listing(html1)
        complete = [t for t in p1.tiles if t.product_id and t.price_min_kop is not None]
        checks.append(Check("Список, стор. 1: ≥20 карток з product_id і ціною",
                            len(complete) >= 20, f"карток {len(p1.tiles)}, з ціною {len(complete)}"))
        checks.append(Check("Список, стор. 1: total розпізнано", p1.total is not None, f"total={p1.total}"))

        # 2. Друга сторінка
        html2 = fetcher.get(category_url(cfg, cfg.root_category, 2))
        _save(live_dir, "listing_p2.html", html2)
        p2 = parse_listing(html2)
        ids1 = {t.product_id for t in p1.tiles}
        fresh = [t for t in p2.tiles if t.product_id not in ids1]
        checks.append(Check("Список, стор. 2: ≥20 карток, більшість нових",
                            len(p2.tiles) >= 20 and len(fresh) * 2 > len(p2.tiles),
                            f"карток {len(p2.tiles)}, нових {len(fresh)}"))

        # 3. Сторінка першого товару
        if p1.tiles:
            tile = p1.tiles[0]
            html3 = fetcher.get(tile.url)
            _save(live_dir, f"product_{tile.product_id}.html", html3)
            d = parse_product(html3)
            checks.append(Check(f"Товар {tile.product_id}: ≥3 хлібні крихти і ≥3 Taxon",
                                len(d.breadcrumbs) >= 3 and len(d.attributes) >= 3,
                                f"крихт {len(d.breadcrumbs)}, Taxon {len(d.attributes)}"))
        else:
            checks.append(Check("Товар: ≥3 хлібні крихти і ≥3 Taxon", False,
                                "на сторінці 1 немає карток, сторінку товару не запитано"))
    except StopCrawl as e:
        stop_reason = str(e)
        checks.append(Check("Доступ до сайту", False, f"ЗУПИНКА: {e}"))
    except FetchError as e:
        checks.append(Check("Доступ до сайту", False, str(e)))

    ok = bool(checks) and all(c.ok for c in checks) and len(checks) >= 3
    status = "ok" if ok else ("stopped" if stop_reason else "failed")
    db.finish_run(conn, run_id, status,
                  {"requests": fetcher.requests, "checks": [c.__dict__ for c in checks]}, stop_reason)
    return run_id, checks


def format_report(checks: list[Check]) -> str:
    width = max((len(c.name) for c in checks), default=0)
    lines = [f"{c.name.ljust(width)}  {'OK  ' if c.ok else 'FAIL'}  {c.found}" for c in checks]
    lines.append("")
    lines.append("Результат: " + ("усе OK" if all(c.ok for c in checks) else
                                   "є FAIL: crawl-* не запустяться без --force"))
    return "\n".join(lines)


def last_smoke_ok(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT status FROM runs WHERE kind = 'smoke' "
                       "ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()
    return bool(row) and row[0] == "ok"
