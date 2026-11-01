"""Постійна черга задач у SQLite (SPEC v2, 4.1, 7.4)."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db

RETRY_DELAYS_S = (5 * 60, 15 * 60, 3600, 3 * 3600)   # після 1-ї..4-ї невдачі; 5-та означає failed
MAX_ATTEMPTS = 5

PRIORITY = {
    "storefront_home": 20, "storefront_parent": 25, "store_list": 30, "city_listing": 50,
    "chain_listing": 55, "product_detail": 70, "store_detail": 80, "city_probe": 90,
}
LISTING_STAGES = ("storefront_home", "storefront_parent", "city_listing", "chain_listing")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_task(conn: sqlite3.Connection, snapshot_id: str, stage: str, key: str, url: str,
             payload: dict | None = None, priority: int | None = None) -> bool:
    """Додає задачу; повторне додавання (той самий snapshot, stage, key) ігнорується. -> чи додано."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO tasks (snapshot_id, stage, task_key, url, priority, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, stage, key, url, PRIORITY[stage] if priority is None else priority,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None, db.utc_now()))
    return cur.rowcount > 0


def next_ready(conn, snapshot_id: str, now: str, stages=None):
    sql = ("SELECT * FROM tasks WHERE snapshot_id = ? AND status = 'pending' "
           "AND (next_attempt_at IS NULL OR next_attempt_at <= ?)")
    params: list = [snapshot_id, now]
    if stages:
        sql += f" AND stage IN ({','.join('?' * len(stages))})"
        params += list(stages)
    sql += " ORDER BY priority, task_id LIMIT 1"
    return conn.execute(sql, params).fetchone()


def next_wakeup(conn, snapshot_id: str, stages=None) -> str | None:
    """Найближчий next_attempt_at серед відкладених задач (None, якщо відкладених немає)."""
    sql = "SELECT MIN(next_attempt_at) FROM tasks WHERE snapshot_id = ? AND status = 'pending'"
    params: list = [snapshot_id]
    if stages:
        sql += f" AND stage IN ({','.join('?' * len(stages))})"
        params += list(stages)
    return conn.execute(sql, params).fetchone()[0]


def pending_count(conn, snapshot_id: str, stages=None) -> int:
    sql = "SELECT COUNT(*) FROM tasks WHERE snapshot_id = ? AND status = 'pending'"
    params: list = [snapshot_id]
    if stages:
        sql += f" AND stage IN ({','.join('?' * len(stages))})"
        params += list(stages)
    return conn.execute(sql, params).fetchone()[0]


def mark_done(conn, task_id: int, http_status: int | None = None) -> None:
    conn.execute("UPDATE tasks SET status = 'done', done_at = ?, last_http_status = ?, last_error = NULL "
                 "WHERE task_id = ?", (db.utc_now(), http_status, task_id))


def mark_skipped(conn, task_id: int, reason: str, http_status: int | None = None) -> None:
    conn.execute("UPDATE tasks SET status = 'skipped', done_at = ?, last_error = ?, last_http_status = ? "
                 "WHERE task_id = ?", (db.utc_now(), reason, http_status, task_id))


def mark_failed(conn, task_id: int, error: str, http_status: int | None = None) -> None:
    conn.execute("UPDATE tasks SET status = 'failed', done_at = ?, last_error = ?, last_http_status = ? "
                 "WHERE task_id = ?", (db.utc_now(), error, http_status, task_id))


def mark_retry(conn, task, error: str, now: datetime, http_status: int | None = None) -> str:
    """attempts += 1; наступна спроба за графіком або failed після MAX_ATTEMPTS. -> новий статус."""
    attempts = task["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        conn.execute("UPDATE tasks SET status = 'failed', attempts = ?, done_at = ?, last_error = ?, "
                     "last_http_status = ? WHERE task_id = ?",
                     (attempts, iso(now), error, http_status, task["task_id"]))
        return "failed"
    when = now + timedelta(seconds=RETRY_DELAYS_S[attempts - 1])
    conn.execute("UPDATE tasks SET attempts = ?, next_attempt_at = ?, last_error = ?, last_http_status = ? "
                 "WHERE task_id = ?", (attempts, iso(when), error, http_status, task["task_id"]))
    return "pending"


def retry_failed(conn, snapshot_id: str) -> int:
    cur = conn.execute("UPDATE tasks SET status = 'pending', attempts = 0, next_attempt_at = NULL, "
                       "done_at = NULL WHERE snapshot_id = ? AND status = 'failed'", (snapshot_id,))
    return cur.rowcount


def counts(conn, snapshot_id: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT stage, status, COUNT(*) n FROM tasks WHERE snapshot_id = ? "
                          "GROUP BY stage, status", (snapshot_id,)):
        out.setdefault(r["stage"], {})[r["status"]] = r["n"]
    return out


def payload(task) -> dict:
    return json.loads(task["payload_json"]) if task["payload_json"] else {}
