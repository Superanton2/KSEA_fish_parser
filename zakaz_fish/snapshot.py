"""Оркестрація знімка (SPEC v2, розділи 4, 8.2): створення, цикл виконання черги, статус, бекап."""
import csv
import gzip
import json
import logging
import re
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from . import db, tasks
from .client import PoliteClient
from .config import Config
from .handlers import STAGES, Ctx, Unexpected, city_url
from .http import FetchError
from .parse_graphql import parse_chains
from .rate import StopRun

log = logging.getLogger(__name__)

CHAINS_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "graphql_chains.json"
MAX_CONSECUTIVE_BUGS = 10
STATUS_EVERY_S = 60


def code_version() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parent, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def default_snapshot_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def active_chains(conn: sqlite3.Connection, cfg: Config) -> list[sqlite3.Row]:
    s = cfg.snapshot
    rows = conn.execute("SELECT * FROM chains ORDER BY chain_id").fetchall()
    return [r for r in rows if r["chain_id"] not in s.chains_exclude
            and (not s.chains_only or r["chain_id"] in s.chains_only) and r["hostname"]]


# --- створення ---

def create_snapshot(conn: sqlite3.Connection, cfg: Config, snapshot_id: str | None = None) -> str:
    """Записує знімок і стартові задачі. Повторний виклик з тим самим ID нічого не дублює."""
    db.init_db(conn)
    sid = snapshot_id or default_snapshot_id()
    cfg.validate()
    if not conn.execute("SELECT 1 FROM snapshots WHERE snapshot_id = ?", (sid,)).fetchone():
        conn.execute("INSERT INTO snapshots (snapshot_id, created_at, config_json, code_version, status) "
                     "VALUES (?, ?, ?, ?, 'planned')",
                     (sid, db.utc_now(), json.dumps(cfg.public_dict(), ensure_ascii=False), code_version()))
    if not conn.execute("SELECT 1 FROM chains LIMIT 1").fetchone():
        if CHAINS_FIXTURE.exists():
            db.upsert_chains(conn, parse_chains(json.loads(CHAINS_FIXTURE.read_text(encoding="utf-8"))))
        else:
            log.warning("Таблиця chains порожня і немає %s: виконайте `seed`", CHAINS_FIXTURE)

    s = cfg.snapshot
    for slug in s.cities:
        conn.execute("INSERT OR IGNORE INTO cities (city_slug, source) VALUES (?, 'selector')", (slug,))
    for slug in s.cities_extra:
        conn.execute("INSERT OR IGNORE INTO cities (city_slug, source) VALUES (?, 'manual')", (slug,))

    included = {r["chain_id"] for r in active_chains(conn, cfg)}
    for r in conn.execute("SELECT chain_id, hostname FROM chains"):
        if r["chain_id"] not in included:
            conn.execute("INSERT OR IGNORE INTO chain_storefronts (snapshot_id, chain_id, hostname, status, checked_at) "
                         "VALUES (?, ?, ?, 'excluded', ?)", (sid, r["chain_id"], r["hostname"], db.utc_now()))
    for r in active_chains(conn, cfg):
        chain, host = r["chain_id"], r["hostname"]
        if s.storefront_listings:
            tasks.add_task(conn, sid, "storefront_home", chain, f"https://{host}/{cfg.lang}/", {"chain_id": chain})
        if s.store_directory:
            tasks.add_task(conn, sid, "store_list", f"{chain}|stores|1", f"https://{host}/{cfg.lang}/stores/",
                           {"chain_id": chain, "page": 1})
    for city in s.all_cities:
        for scope in s.scopes:
            root = s.scope_roots[scope]
            tasks.add_task(conn, sid, "city_listing", f"{city}|{root}|1", city_url(cfg, city, root),
                           {"city": city, "scope": scope, "category": root, "page": 1, "root": True})
    conn.commit()
    return sid


# --- планування етапів, що залежать від результатів ---

def plan_details(conn: sqlite3.Connection, cfg: Config, sid: str) -> int:
    cutoff = tasks.iso(datetime.now(timezone.utc) - timedelta(days=cfg.snapshot.details_max_age_days))
    rows = conn.execute(
        """SELECT p.product_id, p.url FROM products p WHERE p.product_id IN (
               SELECT product_id FROM city_listing_rows WHERE snapshot_id = ?
               UNION SELECT product_id FROM chain_listing_rows WHERE snapshot_id = ?)
           AND COALESCE(p.details_status, '') != 'not_found'
           AND NOT (COALESCE(p.details_status, '') = 'ok' AND COALESCE(p.details_scraped_at, '') >= ?)""", (sid, sid, cutoff)).fetchall()
    n = 0
    for r in rows:
        n += tasks.add_task(conn, sid, "product_detail", r["product_id"], urljoin("https://zakaz.ua", r["url"]),
                            {"product_id": r["product_id"]})
    conn.commit()
    return n


def plan_probes(conn: sqlite3.Connection, cfg: Config, sid: str) -> int:
    root = cfg.snapshot.scope_roots.get("core", cfg.root_category)
    have = conn.execute("SELECT COUNT(*) FROM tasks WHERE snapshot_id = ? AND stage = 'city_probe'", (sid,)).fetchone()[0]
    rows = conn.execute("SELECT DISTINCT city_slug FROM store_locations WHERE city_slug IS NOT NULL AND "
                        "city_slug NOT IN (SELECT city_slug FROM cities) ORDER BY city_slug LIMIT ?",
                        (max(cfg.snapshot.max_city_probes - have, 0),)).fetchall()
    n = sum(tasks.add_task(conn, sid, "city_probe", r["city_slug"], city_url(cfg, r["city_slug"], root),
                           {"city": r["city_slug"]}) for r in rows)
    conn.commit()
    return n


def write_discovered_cities(conn: sqlite3.Connection, out_dir: str | Path) -> Path:
    path = Path(out_dir) / "discovered_cities.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city_slug", "name_uk", "is_valid", "total_fish", "chains_with_stores"])
        for r in conn.execute("SELECT * FROM cities WHERE source = 'discovered' ORDER BY city_slug"):
            chains = [x[0] for x in conn.execute("SELECT DISTINCT chain_id FROM store_locations WHERE city_slug = ? "
                                                 "ORDER BY chain_id", (r["city_slug"],))]
            w.writerow([r["city_slug"], r["name_uk"], r["is_valid"], r["probe_total"], "; ".join(chains)])
    return path


# --- статус і бекап ---

def build_status(conn: sqlite3.Connection, sid: str) -> dict:
    snap = conn.execute("SELECT * FROM snapshots WHERE snapshot_id = ?", (sid,)).fetchone()
    if snap is None:
        raise KeyError(f"Знімка {sid} немає")
    counts = tasks.counts(conn, sid)
    pending = sum(c.get("pending", 0) for c in counts.values())
    hour_ago = tasks.iso(datetime.now(timezone.utc) - timedelta(hours=1))
    log_rows = conn.execute("SELECT outcome, COUNT(*) n FROM http_log WHERE snapshot_id = ? AND ts >= ? "
                            "GROUP BY outcome", (sid, hour_ago)).fetchall()
    last = conn.execute("SELECT delay_s FROM http_log WHERE snapshot_id = ? AND delay_s IS NOT NULL "
                        "ORDER BY id DESC LIMIT 100", (sid,)).fetchall()
    avg_delay = sum(r[0] for r in last) / len(last) if last else 2.5
    per_req = avg_delay + 0.4
    return {
        "snapshot_id": sid, "status": snap["status"], "stop_reason": snap["stop_reason"],
        "started_at": snap["started_at"], "updated_at": db.utc_now(),
        "stages": counts, "pending": pending,
        "requests_last_hour": sum(r["n"] for r in log_rows),
        "outcomes_last_hour": {r["outcome"]: r["n"] for r in log_rows},
        "current_delay_s": round(last[0][0], 2) if last else None,
        "avg_delay_s": round(avg_delay, 2),
        "eta_hours": round(pending * per_req / 3600, 2),
        "eta_note": "оцінка за поточною паузою; нові задачі (картки, сторінки) з'являться пізніше",
    }


def format_status(st: dict) -> str:
    lines = [f"Знімок {st['snapshot_id']}: {st['status']}" + (f" ({st['stop_reason']})" if st["stop_reason"] else ""),
             f"Задач у черзі: {st['pending']}; орієнтовно {st['eta_hours']} год при паузі {st['avg_delay_s']} с",
             f"Запитів за останню годину: {st['requests_last_hour']} {st['outcomes_last_hour']}",
             "", f"{'етап':<18}{'pending':>9}{'done':>8}{'skipped':>9}{'failed':>8}"]
    for stage, c in sorted(st["stages"].items(), key=lambda kv: tasks.PRIORITY.get(kv[0], 99)):
        lines.append(f"{stage:<18}{c.get('pending', 0):>9}{c.get('done', 0):>8}{c.get('skipped', 0):>9}{c.get('failed', 0):>8}")
    return "\n".join(lines)


def write_status_json(conn: sqlite3.Connection, cfg: Config, sid: str) -> None:
    path = cfg.data_dir / "status.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(build_status(conn, sid), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("status.json не записано: %s", e)


def backup_db(conn: sqlite3.Connection, cfg: Config, label: str = "") -> Path | None:
    if cfg.db_path == ":memory:":
        return None
    dest_dir = cfg.data_dir / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"zakaz_fish_{stamp}{('_' + label) if label else ''}.sqlite"
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()
    return dest


# --- виконання ---

def _safe(key: str) -> str:
    return re.sub(r"[^\w.-]+", "_", key)[:150]


def _save_raw(cfg: Config, sid: str, stage: str, key: str, html: str) -> None:
    path = Path(cfg.raw_dir) / sid / stage / f"{_safe(key)}.html.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(html)


def _set_snapshot(conn, sid, status, reason=None, finished=False):
    conn.execute("UPDATE snapshots SET status = ?, stop_reason = ?, finished_at = ?, "
                 "started_at = COALESCE(started_at, ?) WHERE snapshot_id = ?",
                 (status, reason, db.utc_now() if finished else None, db.utc_now(), sid))
    conn.commit()


def _execute(ctx: Ctx, client: PoliteClient, task, now: datetime) -> str | None:
    """Виконує одну задачу. -> етап, якщо задачу завершено (done/skipped/failed), інакше None."""
    conn, cfg = ctx.conn, ctx.cfg
    stage = task["stage"]
    handler, follow, nf_hook, redirect_hook = STAGES[stage]
    p = tasks.payload(task)
    try:
        resp = client.fetch(task["url"], follow_redirects=follow)
    except FetchError as e:
        tasks.mark_failed(conn, task["task_id"], str(e))
        conn.commit()
        return stage

    def retry(error, keep=False):
        if keep:
            conn.commit()
        else:
            conn.rollback()
        tasks.mark_retry(conn, task, error, now, resp.status)
        conn.commit()

    out = resp.outcome
    try:
        if out == "ok":
            ctx.last_final_url = resp.final_url
            _save_raw(cfg, ctx.snapshot_id, stage, task["task_key"], resp.text)
            handler(ctx, task, resp.text, p)
            client.report_structure(True)
            tasks.mark_done(conn, task["task_id"], resp.status)
        elif out == "not_found":
            res = nf_hook(ctx, task, resp, p) if nf_hook else "skipped"
            if res == "done":
                tasks.mark_done(conn, task["task_id"], resp.status)
            else:
                tasks.mark_skipped(conn, task["task_id"], "not_found", resp.status)
        elif out == "redirect":
            if redirect_hook is None:
                raise Unexpected(f"редірект на {resp.location}")
            res = redirect_hook(ctx, task, resp, p)
            if res == "done":
                tasks.mark_done(conn, task["task_id"], resp.status)
            else:
                tasks.mark_skipped(conn, task["task_id"], "not_found", resp.status)
        elif out == "robots":
            tasks.mark_skipped(conn, task["task_id"], "robots")
            if stage == "storefront_home":
                conn.execute("INSERT OR REPLACE INTO chain_storefronts (snapshot_id, chain_id, hostname, status, "
                             "checked_at) VALUES (?, ?, ?, 'robots_disallowed', ?)",
                             (ctx.snapshot_id, p["chain_id"], ctx.hostnames.get(p["chain_id"]), db.utc_now()))
        elif out in ("retryable", "rate_limited"):
            retry(resp.error or out)
            return None
        else:  # code_error
            tasks.mark_failed(conn, task["task_id"], resp.error or out, resp.status)
    except Unexpected as e:
        client.report_structure(False)       # може підняти StopRun; запис обробника при цьому зберігається, якщо keep
        retry(f"unexpected_page: {e}", keep=e.keep)
        return None
    conn.commit()
    return stage


def run_snapshot(conn: sqlite3.Connection, cfg: Config, sid: str | None = None, *, transport=None,
                 sleep=time.sleep, max_requests: int | None = None, stages: list[str] | None = None,
                 wait_for_delayed: bool = True, now_fn=lambda: datetime.now(timezone.utc)) -> int:
    """Виконує чергу. -> код виходу (0, 2, 3, 4, 130)."""
    db.init_db(conn)
    if sid is None:
        r = conn.execute("SELECT snapshot_id FROM snapshots WHERE status != 'done' AND status != "
                         "'done_with_failures' ORDER BY created_at DESC LIMIT 1").fetchone()
        if r is None:
            raise SystemExit("Немає незавершеного знімка: виконайте `snapshot create`")
        sid = r[0]
    cfg.validate()
    snap_date = date.fromisoformat(sid[:10]) if re.match(r"\d{4}-\d{2}-\d{2}", sid) else now_fn().date()
    ctx = Ctx(conn, cfg, sid, snap_date, chains=db.known_chain_ids(conn),
              hostnames={r["chain_id"]: r["hostname"] for r in conn.execute("SELECT chain_id, hostname FROM chains")})
    client = PoliteClient(cfg, conn, sid, transport=transport, sleep=sleep)
    _set_snapshot(conn, sid, "running")
    planned_details = planned_probes = False
    backed_up: set[str] = set()
    bugs = 0
    last_status = 0.0
    code = 0
    try:
        if not client.ensure_robots("zakaz.ua"):
            raise StopRun(3, "robots.txt zakaz.ua недоступний")
        for probe in (f"https://zakaz.ua/{cfg.lang}/", f"https://zakaz.ua/{cfg.lang}/products/"):
            if not client.robots.allowed(probe):
                raise StopRun(4, f"robots.txt zakaz.ua забороняє {probe}")
        while True:
            if time.monotonic() - last_status >= STATUS_EVERY_S:
                write_status_json(conn, cfg, sid)
                last_status = time.monotonic()
            if max_requests is not None and client.requests >= max_requests:
                _set_snapshot(conn, sid, "stopped", f"досягнуто --max-requests {max_requests}")
                write_status_json(conn, cfg, sid)
                return 0
            if not planned_details and ((stages and "product_detail" in stages) or
                                        tasks.pending_count(conn, sid, tasks.LISTING_STAGES) == 0):
                planned_details = True
                log.info("Заплановано карток товарів: %d", plan_details(conn, cfg, sid))
            if cfg.snapshot.city_probes and not planned_probes and tasks.pending_count(conn, sid, ("store_list",)) == 0:
                planned_probes = True
                log.info("Заплановано перевірок міст: %d", plan_probes(conn, cfg, sid))
            now = now_fn()
            task = tasks.next_ready(conn, sid, tasks.iso(now), stages)
            if task is None:
                wake = tasks.next_wakeup(conn, sid, stages)
                if wake is None:
                    break
                if not wait_for_delayed:
                    break
                secs = (datetime.strptime(wake, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - now).total_seconds()
                sleep(max(1.0, min(secs, 60)))
                continue
            try:
                finished = _execute(ctx, client, task, now)
                bugs = 0
            except StopRun:
                raise
            except Exception as e:                      # помилка коду обробника: задача failed, запуск іде далі
                conn.rollback()
                log.exception("Задача %s/%s: помилка обробника", task["stage"], task["task_key"])
                tasks.mark_failed(conn, task["task_id"], f"{type(e).__name__}: {e}")
                conn.commit()
                bugs += 1
                if bugs >= MAX_CONSECUTIVE_BUGS:
                    raise
                finished = task["stage"]
            if finished and finished not in backed_up and tasks.pending_count(conn, sid, (finished,)) == 0 \
                    and finished in tasks.LISTING_STAGES + ("store_list", "product_detail"):
                backed_up.add(finished)
                dest = backup_db(conn, cfg, finished)
                if dest:
                    log.info("Етап %s завершено, бекап: %s", finished, dest)
        failed = conn.execute("SELECT COUNT(*) FROM tasks WHERE snapshot_id = ? AND status = 'failed'", (sid,)).fetchone()[0]
        _set_snapshot(conn, sid, "done_with_failures" if failed else "done", finished=True)
        write_discovered_cities(conn, cfg.out_dir)
    except StopRun as e:
        conn.commit()
        _set_snapshot(conn, sid, "stopped", e.reason)
        log.error("ЗУПИНЕНО (код %d): %s", e.code, e.reason)
        code = e.code
    except KeyboardInterrupt:
        conn.commit()
        _set_snapshot(conn, sid, "stopped", "interrupted")
        code = 130
    finally:
        write_status_json(conn, cfg, sid)
        client.close()
    return code
