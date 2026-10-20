import gzip
import logging
import random
import time
from pathlib import Path

import httpx

from .config import Config

log = logging.getLogger(__name__)

STOP_STATUSES = {403, 429, 503}
RETRY_STATUSES = {500, 502, 504}
STOP_MARKERS = ("Just a moment", "cf-chl", "challenge-platform")
RETRY_PAUSES_S = (5, 15, 45)


class StopCrawl(Exception):
    """Сайт відповів блокуванням або перевіркою Cloudflare: запуск негайно зупиняється."""


class FetchError(Exception):
    """Запит не вдався (мережа, таймаут, 5xx після повторів або інший непридатний статус)."""


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

    def get(self, url: str, raw_name: str | None = None) -> str:
        """GET з паузою, повторами й аварійною зупинкою. Успішну відповідь зберігає в raw/ (gzip)."""
        last_error = "невідома помилка"
        for attempt in range(len(RETRY_PAUSES_S) + 1):
            self._sleep(random.uniform(self.cfg.delay_min_s, self.cfg.delay_max_s))
            self.requests += 1
            try:
                resp = self.client.get(url)
            except httpx.TransportError as e:  # мережеві помилки й таймаути
                last_error = f"{type(e).__name__}: {e}"
            else:
                body = resp.text
                if resp.status_code in STOP_STATUSES:
                    raise StopCrawl(f"HTTP {resp.status_code} для {url}")
                for marker in STOP_MARKERS:
                    if marker in body:
                        raise StopCrawl(f"Сторінка перевірки Cloudflare ({marker!r}) для {url}")
                if resp.status_code in RETRY_STATUSES:
                    last_error = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} для {url}")
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
