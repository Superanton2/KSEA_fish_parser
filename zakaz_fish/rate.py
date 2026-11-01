"""Адаптивний обмежувач швидкості (SPEC v2, 7.3): AIMD + запобіжник. Час і сон підміняються в тестах."""
import random
import time
from collections import deque

from .config import RateCfg


class StopRun(Exception):
    """Знімок треба зупинити. code: 2 блокування, 3 запобіжник/429/неочікувані сторінки, 4 robots."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class RateLimiter:
    RETURN_AFTER_OK = 50      # успіхів поспіль після сповільнення, щоб почати повертатись до стартових пауз
    RETURN_FACTOR = 0.75

    def __init__(self, cfg: RateCfg, *, clock=time.monotonic, sleep=time.sleep, uniform=random.uniform):
        self.cfg = cfg
        self.clock, self.sleep, self.uniform = clock, sleep, uniform
        self.base = (cfg.delay_min_s, cfg.delay_max_s)
        self.dmin, self.dmax = self.base
        self.ok_streak = 0          # лічильник для прискорення
        self.err_streak = 0
        self.slowed = False
        self.ok_since_slow = 0
        self.window: deque[bool] = deque(maxlen=cfg.breaker_window)   # True = помилка
        self.trips: deque[float] = deque()
        self.rate_limited_at: deque[float] = deque()
        self.sent: deque[float] = deque()
        self.last_delay = 0.0

    # --- перед запитом ---
    def wait(self) -> float:
        """Пауза перед запитом (+ очікування, якщо перевищено ліміт на годину). -> тривалість, с."""
        now = self.clock()
        while self.sent and now - self.sent[0] >= 3600:
            self.sent.popleft()
        extra = 0.0
        if len(self.sent) >= self.cfg.max_requests_per_hour:
            extra = 3600 - (now - self.sent[0])
            self.sleep(extra)
        delay = self.uniform(self.dmin, self.dmax)
        self.sleep(delay)
        self.last_delay = delay + extra
        self.sent.append(self.clock())
        return self.last_delay

    # --- після запиту ---
    def record_ok(self) -> None:
        self.err_streak = 0
        self.window.append(False)
        if self.slowed:
            self.ok_since_slow += 1
            if self.ok_since_slow >= self.RETURN_AFTER_OK:
                self.ok_since_slow = 0
                self.dmin = max(self.base[0], self.dmin * self.RETURN_FACTOR)
                self.dmax = max(self.base[1], self.dmax * self.RETURN_FACTOR)
                if (self.dmin, self.dmax) == self.base:
                    self.slowed = False
            return
        self.ok_streak += 1
        if self.ok_streak >= self.cfg.speedup_after_ok:
            self.ok_streak = 0
            self.dmin = max(self.cfg.delay_floor_min_s, self.dmin * 0.9)
            self.dmax = max(self.cfg.delay_floor_max_s, self.dmax * 0.9)

    def record_retryable(self) -> float:
        """Помилка 5xx/таймаут: сповільнення, охолодження, перевірка запобіжника. -> тривалість охолодження."""
        c = self.cfg
        self.ok_streak = 0
        self.ok_since_slow = 0
        self.err_streak += 1
        self.slowed = True
        self.dmin = min(c.delay_ceiling_s, self.dmin * c.slowdown_factor)
        self.dmax = min(c.delay_ceiling_s, self.dmax * c.slowdown_factor)
        cooldown = min(c.cooldown_base_s * 2 ** (self.err_streak - 1), c.cooldown_max_s)
        self.sleep(cooldown)
        self.window.append(True)
        if sum(self.window) >= c.breaker_errors:
            self._trip()
        return cooldown

    def _trip(self) -> None:
        c = self.cfg
        now = self.clock()
        self.trips.append(now)
        while self.trips and now - self.trips[0] > c.breaker_trip_window_s:
            self.trips.popleft()
        if len(self.trips) >= c.breaker_max_trips:
            raise StopRun(3, f"Запобіжник спрацював {len(self.trips)} раз(и) за "
                             f"{c.breaker_trip_window_s / 3600:.0f} год: потрібна людина")
        self.window.clear()
        self.sleep(c.breaker_pause_s)

    def record_429(self, retry_after: float | None) -> float:
        """429: пауза Retry-After (або 600 с, максимум 3600), паузи ×2; три за годину означають зупинку."""
        now = self.clock()
        self.rate_limited_at.append(now)
        while self.rate_limited_at and now - self.rate_limited_at[0] > 3600:
            self.rate_limited_at.popleft()
        if len(self.rate_limited_at) >= 3:
            raise StopRun(3, "Три відповіді 429 за годину")
        pause = min(retry_after if retry_after else 600, 3600)
        self.ok_streak = 0
        self.ok_since_slow = 0
        self.slowed = True
        self.dmin = min(self.cfg.delay_ceiling_s, self.dmin * 2)
        self.dmax = min(self.cfg.delay_ceiling_s, self.dmax * 2)
        self.sleep(pause)
        return pause
