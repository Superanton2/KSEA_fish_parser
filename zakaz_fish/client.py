"""HTTP-клієнт знімка (SPEC v2, 7): robots, обмежувач, порожній cookie jar, класифікація відповідей, http_log."""
import re
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import db
from .config import Config
from .http import BENIGN_SNIPPETS, BLOCKED_HOSTS, STOP_MARKERS, FetchError
from .rate import RateLimiter, StopRun
from .robots import RobotsGuard

CODE_ERRORS = (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.LocalProtocolError)
NETWORK_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
MAX_UNEXPECTED_IN_ROW = 5


@dataclass
class Response:
    outcome: str            # ok | retryable | not_found | redirect | robots | rate_limited | code_error
    status: int | None = None
    text: str = ""
    final_url: str | None = None
    location: str | None = None
    error: str | None = None


class PoliteClient:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, snapshot_id: str | None, *,
                 transport: httpx.BaseTransport | None = None, limiter: RateLimiter | None = None,
                 sleep=time.sleep):
        self.cfg, self.conn, self.snapshot_id = cfg, conn, snapshot_id
        self.limiter = limiter or RateLimiter(cfg.rate, sleep=sleep)
        self.robots = RobotsGuard(cfg.ua_token)
        self.requests = 0
        self.unexpected_in_row = 0
        self.http = httpx.Client(
            timeout=cfg.timeout_s, transport=transport, follow_redirects=False,
            headers={"User-Agent": cfg.full_user_agent, "Accept-Language": cfg.lang, "Accept": "text/html"})

    def close(self) -> None:
        self.http.close()

    # --- robots ---
    def ensure_robots(self, host: str) -> bool:
        """Завантажує robots.txt хоста (раз за запуск). -> True, якщо хост доступний."""
        if self.robots.known(host) and self.robots.available(host):
            return True
        resp = self._request(f"https://{host}/robots.txt", False, is_robots=True)
        if resp.outcome == "retryable" or resp.outcome == "rate_limited":
            self.robots.load(host, None, None)
            return False
        self.robots.load(host, resp.status, resp.text)
        return self.robots.available(host)

    # --- запит ---
    def fetch(self, url: str, *, follow_redirects: bool = False) -> Response:
        host = urlsplit(url).hostname
        if host in BLOCKED_HOSTS:
            raise FetchError(f"Хост {host} заборонений (robots.txt): {url}")
        if not self.ensure_robots(host):
            return Response("retryable", error=f"robots.txt хоста {host} недоступний")
        if not self.robots.allowed(url):
            self._log(url, None, None, None, "robots", 0)
            return Response("robots", error="robots")
        return self._request(url, follow_redirects)

    def _request(self, url: str, follow_redirects: bool, is_robots: bool = False) -> Response:
        parts = urlsplit(url)
        if parts.hostname in BLOCKED_HOSTS and parts.path != "/robots.txt":
            raise FetchError(f"Хост {parts.hostname} заборонений (robots.txt): {url}")
        self.http.cookies.clear()                       # принцип 9: контекст лише з URL
        delay = self.limiter.wait()
        self.requests += 1
        t0 = time.monotonic()
        try:
            r = self.http.get(url, follow_redirects=follow_redirects)
        except CODE_ERRORS as e:
            self._log(url, None, None, None, "code_error", delay)
            return Response("code_error", error=f"{type(e).__name__}: {e}")
        except (NETWORK_ERRORS + (httpx.TransportError,)) as e:
            self._log(url, None, None, None, "retryable", delay, int((time.monotonic() - t0) * 1000))
            self.limiter.record_retryable()
            return Response("retryable", error=f"{type(e).__name__}: {e}")
        ms = int((time.monotonic() - t0) * 1000)
        body, status, final = r.text, r.status_code, str(r.url)

        def done(resp: Response, outcome: str) -> Response:
            self._log(url, final, status, len(r.content), outcome, delay, ms)
            return resp

        if status == 403:
            self._log(url, final, status, len(r.content), "blocked", delay, ms)
            raise StopRun(2, f"HTTP 403 для {url}")
        checked = body
        for s in BENIGN_SNIPPETS:
            checked = checked.replace(s, "")
        title = TITLE_RE.search(body)
        if (status in (403, 503) and any(m in checked for m in STOP_MARKERS)) or \
                (title and "just a moment" in title.group(1).lower()):
            self._log(url, final, status, len(r.content), "blocked", delay, ms)
            raise StopRun(2, f"Сторінка перевірки Cloudflare для {url}")
        if status == 429:
            ra = r.headers.get("retry-after")
            self._log(url, final, status, len(r.content), "rate_limited", delay, ms)
            self.limiter.record_429(float(ra) if ra and ra.isdigit() else None)
            return Response("rate_limited", status, error="HTTP 429")
        if status in (500, 502, 503, 504):
            self.limiter.record_retryable()
            return done(Response("retryable", status, error=f"HTTP {status}"), "retryable")
        if status == 404:
            self.limiter.record_ok()
            return done(Response("not_found", status, body, final), "not_found")
        if 300 <= status < 400:
            self.limiter.record_ok()
            return done(Response("redirect", status, "", final, r.headers.get("location")), "redirect")
        if status >= 400:
            self.limiter.record_ok()
            return done(Response("code_error", status, body, final, error=f"HTTP {status}"), "unexpected_page")
        self.limiter.record_ok()
        return done(Response("ok", status, body, final), "ok")

    # --- структура сторінки (повідомляє обробник) ---
    def report_structure(self, ok: bool) -> None:
        """5 неочікуваних сторінок поспіль означають зупинку (код 3)."""
        if ok:
            self.unexpected_in_row = 0
            return
        self.unexpected_in_row += 1
        self.conn.execute("UPDATE http_log SET outcome = 'unexpected_page' WHERE id = "
                          "(SELECT MAX(id) FROM http_log)")
        if self.unexpected_in_row >= MAX_UNEXPECTED_IN_ROW:
            raise StopRun(3, f"{MAX_UNEXPECTED_IN_ROW} неочікуваних сторінок поспіль: змінилась верстка "
                             "або сайт віддає не те")

    def _log(self, url, final, status, nbytes, outcome, delay, ms=None) -> None:
        self.conn.execute(
            "INSERT INTO http_log (snapshot_id, ts, host, url, final_url, status, elapsed_ms, bytes, outcome, delay_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.snapshot_id, db.utc_now(), urlsplit(url).hostname, url, final, status, ms, nbytes, outcome, delay))
        self.conn.commit()
