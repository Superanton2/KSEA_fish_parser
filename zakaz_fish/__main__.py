import argparse
import json
import logging
import sys

from .config import load_config
from . import crawl, db
from .export import export_all
from .http import Fetcher
from .smoke import format_report, last_smoke_ok, run_smoke
from .stats import collect_stats
from .parse_graphql import parse_chains, parse_delivery_schedules


def cmd_init_db(args, cfg) -> int:
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    print(f"База ініціалізована: {cfg.db_path}")
    return 0


def cmd_seed(args, cfg) -> int:
    with open(args.chains, encoding="utf-8") as f:
        chains = parse_chains(json.load(f))
    with open(args.stores, encoding="utf-8") as f:
        stores = parse_delivery_schedules(json.load(f))
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    run_id = db.start_run(conn, "seed", cfg.city)
    db.upsert_chains(conn, chains)
    db.upsert_stores(conn, stores, source="delivery_schedules_fixture")
    db.finish_run(conn, run_id, "ok", {"chains": len(chains), "stores": len(stores)})
    print(f"seed: {len(chains)} мереж, {len(stores)} магазинів")
    return 0


def _open(cfg):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return conn


def _guard(conn, args) -> bool:
    """crawl-* не запускаються, поки останній smoke-check не OK (або передано --force)."""
    if args.force or last_smoke_ok(conn):
        return True
    print("Останній smoke-check не OK (або його не було). Запустіть `smoke-check` "
          "або передайте --force.", file=sys.stderr)
    return False


def _report(conn, run_id, status) -> int:
    row = conn.execute("SELECT stop_reason, stats_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    print(f"run {run_id}: {status}")
    if row["stop_reason"]:
        print(f"ЗУПИНЕНО: {row['stop_reason']}", file=sys.stderr)
    print(row["stats_json"])
    return {"ok": 0, "stopped": 2}.get(status, 1)


def cmd_smoke(args, cfg) -> int:
    conn = _open(cfg)
    with Fetcher(cfg) as fetcher:
        _, checks = run_smoke(conn, cfg, fetcher)
    print(format_report(checks))
    return 0 if all(c.ok for c in checks) else 1


def _run_crawl(args, cfg, runner) -> int:
    conn = _open(cfg)
    if not _guard(conn, args):
        return 1
    try:
        with Fetcher(cfg, cfg.raw_dir) as fetcher:
            return _report(conn, *runner(conn, fetcher))
    except KeyboardInterrupt:
        # run уже позначено 'stopped' (interrupted), зібране збережено
        print("Перервано (Ctrl+C). Зібрані дані збережено, run позначено stopped.", file=sys.stderr)
        return 130


def cmd_crawl_listings(args, cfg) -> int:
    return _run_crawl(args, cfg, lambda conn, f: crawl.crawl_listings(conn, f, cfg))


def cmd_crawl_details(args, cfg) -> int:
    return _run_crawl(args, cfg, lambda conn, f: crawl.crawl_details(conn, f, cfg, args.mode, args.limit))


def cmd_daily(args, cfg) -> int:
    return _run_crawl(args, cfg, lambda conn, f: crawl.crawl_daily(conn, f, cfg))


def cmd_sample(args, cfg) -> int:
    from .sample import run_sample
    extra = []
    for item in args.add or []:
        name, _, url = item.partition("=")
        extra.append((name, url))
    with Fetcher(cfg) as fetcher:
        s = run_sample(cfg, fetcher, args.preset, extra)
    for e in s.entries:
        print(e["name"], e.get("status", ""), e.get("location", ""), e.get("skipped", e.get("error", "")))
    print(f"Запитів: {fetcher.requests}; індекс: tests/fixtures/samples/index.json")
    if s.stopped:
        print(f"ЗУПИНЕНО: {s.stopped}", file=sys.stderr)
        return 2
    return 0


def cmd_snapshot(args, cfg) -> int:
    from . import snapshot as sn, tasks
    conn = _open(cfg)
    act = args.action
    if act == "create":
        sid = sn.create_snapshot(conn, cfg, args.id)
        print(f"Знімок {sid} створено. Задач: {sum(sum(c.values()) for c in tasks.counts(conn, sid).values())}")
        return 0
    if act == "run":
        stages = args.stages.split(",") if args.stages else None
        return sn.run_snapshot(conn, cfg, args.id, max_requests=args.max_requests, stages=stages)
    sid = args.id or (conn.execute("SELECT snapshot_id FROM snapshots ORDER BY created_at DESC LIMIT 1").fetchone() or [None])[0]
    if act == "status":
        print(sn.format_status(sn.build_status(conn, sid)))
        return 0
    if act == "retry-failed":
        n = tasks.retry_failed(conn, sid)
        conn.commit()
        print(f"Повернуто в чергу: {n}")
        return 0
    if act == "backup":
        print(sn.backup_db(conn, cfg))
        return 0
    if act == "export":
        from .package import export_package
        print(export_package(conn, cfg, sid))
        return 0
    return 1


def cmd_export(args, cfg) -> int:
    for path in export_all(_open(cfg), cfg.out_dir):
        print(path)
    return 0


def cmd_stats(args, cfg) -> int:
    print(collect_stats(_open(cfg)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zakaz_fish")
    p.add_argument("--config", default=None, help="шлях до config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    s = sub.add_parser("seed")
    s.add_argument("--chains", required=True)
    s.add_argument("--stores", required=True)
    s.set_defaults(func=cmd_seed)

    sub.add_parser("smoke-check").set_defaults(func=cmd_smoke)
    sm = sub.add_parser("sample")
    sm.add_argument("--preset", default="v2")
    sm.add_argument("--add", action="append", metavar="NAME=URL")
    sm.set_defaults(func=cmd_sample)
    sn = sub.add_parser("snapshot")
    sn.add_argument("action", choices=["create", "run", "status", "retry-failed", "export", "backup"])
    sn.add_argument("--id", default=None)
    sn.add_argument("--max-requests", type=int, default=None)
    sn.add_argument("--stages", default=None, help="напр. product_detail або city_listing,chain_listing")
    sn.set_defaults(func=cmd_snapshot)
    sub.add_parser("export").set_defaults(func=cmd_export)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    c = sub.add_parser("crawl-listings")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_crawl_listings)

    d = sub.add_parser("crawl-details")
    d.add_argument("--mode", default="new", help="new | all | stale:<days>")
    d.add_argument("--limit", type=int, default=None)
    d.add_argument("--force", action="store_true")
    d.set_defaults(func=cmd_crawl_details)

    dl = sub.add_parser("daily")
    dl.add_argument("--force", action="store_true")
    dl.set_defaults(func=cmd_daily)
    return p


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
