import gzip
import logging
import random
import time
from pathlib import Path

from urllib.parse import urlsplit

import httpx

from .config import Config

log = logging.getLogger(__name__)

# robots.txt: User-agent * / Disallow: / (перевірено 01.11.2026); читати можна лише сам /robots.txt
BLOCKED_HOSTS = {"stores-api.zakaz.ua"}

STOP_STATUSES = {403, 429, 503}
RETRY_STATUSES = {500, 502, 504}
STOP_MARKERS = ("Just a moment", "cf-chl", "challenge-platform")
# Cloudflare вставляє цей скрипт-«маячок» у звичайні сторінки (перевірено: відповідь 200 з 40 картками);
# сам по собі він не ознака перевірки. Справжня перевірка має інші шляхи (напр. .../orchestrate/chl_page/).
BENIGN_SNIPPETS = ("/cdn-cgi/challenge-platform/scripts/jsd/",)
RETRY_PAUSES_S = (5, 15, 45)
# Повторюємо лише це; UnsupportedProtocol, InvalidURL, LocalProtocolError та інше це помилки
# запиту або коду, повтор їх не лікує.
RETRY_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


class StopCrawl(Exception):
    """Сайт відповів блокуванням або перевіркою Cloudflare: запуск негайно зупиняється."""

    def __init__(self, message: str, body: str | None = None):
        super().__init__(message)
        self.body = body  # тіло відповіді для діагностики


class PageRedirected(Exception):
    """Запит із follow_redirects=False отримав редірект (сайт так закінчує пагінацію: ?page=N+1 -> сторінка 1)."""

    def __init__(self, url: str, location: str | None):
        super().__init__(f"{url} -> {location}")
        self.location = location


class FetchError(Exception):
    """Запит не вдався (мережа, таймаут, 5xx після повторів або інший непридатний статус)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class Fetcher:
    def __init__(self, cfg: Config, raw_dir: Path | None = None, *,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.cfg = cfg
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self._sleep = sleep
        self.requests = 0
        self._started = time.monotonic()
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=cfg.timeout_s,
            transport=transport,
            headers={
                "User-Agent": cfg.full_user_agent,
                "Accept-Language": cfg.lang,
                "Accept": "text/html",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def _prepare(self, url: str) -> None:
        """Перед кожним запитом: заборонені хости, порожній cookie jar (контекст лише з URL)."""
        parts = urlsplit(url)
        if parts.hostname in BLOCKED_HOSTS and parts.path != "/robots.txt":
            raise FetchError(f"Хост {parts.hostname} заборонений (robots.txt): {url}")
        self.client.cookies.clear()

    def fetch_response(self, url: str, *, follow_redirects: bool = False) -> httpx.Response:
        """Один запит (без повторів) з паузою; повертає відповідь як є, зокрема 3xx/4xx/5xx.

        Піднімає StopCrawl на 403/429/503 і сторінках перевірки Cloudflare. Для `sample`.
        """
        self._prepare(url)
        self._sleep(random.uniform(self.cfg.delay_min_s, self.cfg.delay_max_s))
        self.requests += 1
        try:
            resp = self.client.get(url, follow_redirects=follow_redirects)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__}: {e} для {url}") from e
        body = resp.text
        if resp.status_code in STOP_STATUSES:
            raise StopCrawl(f"HTTP {resp.status_code} для {url}", body)
        checked = body
        for snippet in BENIGN_SNIPPETS:
            checked = checked.replace(snippet, "")
        for marker in STOP_MARKERS:
            if marker in checked:
                raise StopCrawl(f"Сторінка перевірки Cloudflare ({marker!r}) для {url}", body)
        return resp

    def get(self, url: str, raw_name: str | None = None, *, follow_redirects: bool = True) -> str:
        """GET з паузою, повторами й аварійною зупинкою. Успішну відповідь зберігає в raw/ (gzip).

        follow_redirects=False: на 3xx піднімає PageRedirected, не роблячи другого запиту.
        """
        last_error = "невідома помилка"
        for attempt in range(len(RETRY_PAUSES_S) + 1):
            self._prepare(url)
            self._sleep(random.uniform(self.cfg.delay_min_s, self.cfg.delay_max_s))
            self.requests += 1
            try:
                resp = self.client.get(url, follow_redirects=follow_redirects)
            except RETRY_EXCEPTIONS as e:
                last_error = f"{type(e).__name__}: {e}"
            except (httpx.TransportError, httpx.InvalidURL) as e:
                raise FetchError(f"{type(e).__name__}: {e} для {url}") from e
            else:
                body = resp.text
                if resp.status_code in STOP_STATUSES:
                    raise StopCrawl(f"HTTP {resp.status_code} для {url}", body)
                checked = body
                for snippet in BENIGN_SNIPPETS:
                    checked = checked.replace(snippet, "")
                for marker in STOP_MARKERS:
                    if marker in checked:
                        raise StopCrawl(f"Сторінка перевірки Cloudflare ({marker!r}) для {url}", body)
                if not follow_redirects and resp.is_redirect:
                    raise PageRedirected(url, resp.headers.get("location"))
                if resp.status_code in RETRY_STATUSES:
                    last_error = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} для {url}", resp.status_code)
                else:
                    self._save_raw(raw_name, body)
                    log.info("GET %s -> %s (запитів: %d, %.0f с)", url, resp.status_code,
                             self.requests, self.elapsed_s)
                    return body
            if attempt < len(RETRY_PAUSES_S):
                pause = RETRY_PAUSES_S[attempt]
                log.warning("%s для %s; повтор через %d с", last_error, url, pause)
                self._sleep(pause)
        raise FetchError(f"{last_error} для {url} після {len(RETRY_PAUSES_S)} повторів")

    def _save_raw(self, raw_name: str | None, body: str) -> None:
        if not (self.raw_dir and raw_name):
            return
        path = self.raw_dir / raw_name
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(body)
