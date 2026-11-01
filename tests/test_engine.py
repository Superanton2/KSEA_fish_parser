"""S2: robots, обмежувач, клієнт знімка без cookies, черга задач."""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from zakaz_fish import db, tasks
from zakaz_fish.client import PoliteClient
from zakaz_fish.config import Config, RateCfg
from zakaz_fish.http import FetchError
from zakaz_fish.rate import RateLimiter, StopRun
from zakaz_fish.robots import RobotsGuard

ROBOTS = Path(__file__).parent / "fixtures" / "robots"
UA = "KSEA-FishMarketResearch/0.2"


# --- robots.txt ---

@pytest.fixture(scope="module")
def zakaz_robots():
    return (ROBOTS / "zakaz.ua.robots.txt").read_text(encoding="utf-8")


def guard(ua, text):
    g = RobotsGuard(ua)
    g.load("zakaz.ua", 200, text)
    return g


@pytest.mark.parametrize("url", [
    "https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/?page=2",
    "https://zakaz.ua/uk/products/riba--novus02877258000000/",
    "https://zakaz.ua/uk/lviv/canned-fish-zakaz/",
])
def test_robots_allows_our_agent(zakaz_robots, url):
    assert guard("KSEA-FishMarketResearch", zakaz_robots).allowed(url)


@pytest.mark.parametrize("ua", ["Wget", "FastFishBot", "Meta-ExternalAgent"])
def test_robots_blocks_listed_agents(zakaz_robots, ua):
    assert not guard(ua, zakaz_robots).allowed("https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/")


def test_stores_api_disallowed_by_robots():
    g = RobotsGuard("KSEA-FishMarketResearch")
    g.load("stores-api.zakaz.ua", 200, (ROBOTS / "stores-api.zakaz.ua.robots.txt").read_text(encoding="utf-8"))
    assert not g.allowed("https://stores-api.zakaz.ua/graphql")


def test_robots_404_allows_all_and_5xx_unavailable():
    g = RobotsGuard("x")
    assert g.load("a.zakaz.ua", 404, "") == "no_robots" and g.allowed("https://a.zakaz.ua/anything")
    assert g.load("b.zakaz.ua", 503, "") == "unavailable" and not g.available("b.zakaz.ua")


def test_config_rejects_blocked_user_agent():
    with pytest.raises(ValueError):
        Config(user_agent="FastFishBot/1.0").validate()
    Config(user_agent=UA).validate()


# --- обмежувач ---

class Fake:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def limiter(**kw):
    f = Fake()
    lim = RateLimiter(RateCfg(**kw), clock=f.clock, sleep=f.sleep, uniform=lambda a, b: a)
    return lim, f


def test_slowdown_x2_and_cooldown_after_retryable():
    lim, f = limiter()
    cd = lim.record_retryable()
    assert cd == 60 and (lim.dmin, lim.dmax) == (3.0, 6.0) and f.sleeps == [60]
    assert lim.record_retryable() == 120 and (lim.dmin, lim.dmax) == (6.0, 12.0)


def test_cooldown_and_delay_caps():
    lim, _ = limiter(breaker_errors=10 ** 6)
    for _ in range(12):
        cd = lim.record_retryable()
    assert cd == 1800 and lim.dmax == 60 and lim.dmin == 60


def test_speedup_after_300_ok_but_not_below_floor():
    lim, _ = limiter()
    for _ in range(300):
        lim.record_ok()
    assert (lim.dmin, lim.dmax) == pytest.approx((1.35, 2.7))
    for _ in range(300 * 10):
        lim.record_ok()
    assert (lim.dmin, lim.dmax) == (1.0, 2.0)


def test_returns_to_base_after_slowdown():
    lim, _ = limiter()
    lim.record_retryable()
    for _ in range(50 * 3):
        lim.record_ok()
    assert (lim.dmin, lim.dmax) == (1.5, 3.0) and not lim.slowed


def test_breaker_pause_after_8_errors_in_window_and_stop_after_3_trips():
    lim, f = limiter(cooldown_base_s=0, cooldown_max_s=0)
    for _ in range(7):
        lim.record_retryable()
    assert 1200 not in f.sleeps
    lim.record_retryable()
    assert 1200 in f.sleeps
    for _ in range(8):
        lim.record_retryable()
    with pytest.raises(StopRun) as e:
        for _ in range(8):
            lim.record_retryable()
    assert e.value.code == 3


def test_429_retry_after_and_three_per_hour_stop():
    lim, f = limiter()
    assert lim.record_429(30) == 30
    assert lim.record_429(None) == 600
    with pytest.raises(StopRun) as e:
        lim.record_429(5)
    assert e.value.code == 3


def test_hourly_cap_waits():
    lim, f = limiter(max_requests_per_hour=2)
    lim.wait()
    lim.wait()
    n = len(f.sleeps)
    d = lim.wait()
    assert d > 3000 and len(f.sleeps) == n + 2


# --- клієнт ---

def make_client(handler, tmp_path=None, **rate):
    conn = db.connect(":memory:")
    db.init_db(conn)
    conn.execute("INSERT INTO snapshots (snapshot_id, created_at, config_json, status) VALUES ('s','x','{}','running')")
    cfg = Config(user_agent=UA, rate=RateCfg(**rate))
    robots = (ROBOTS / "zakaz.ua.robots.txt").read_text(encoding="utf-8")

    def h(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return handler(request)

    return PoliteClient(cfg, conn, "s", transport=httpx.MockTransport(h), sleep=lambda s: None), conn


URL = "https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/"


def test_no_cookies_sent_between_requests():
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, text="<html>ok</html>", headers={"set-cookie": "city=kyiv; Path=/"})

    c, _ = make_client(handler)
    c.fetch(URL)
    c.fetch(URL + "?page=2")
    assert seen == [None, None]


def test_blocked_host_raises_before_network():
    calls = []
    c, _ = make_client(lambda r: calls.append(r) or httpx.Response(200, text=""))
    with pytest.raises(FetchError):
        c.fetch("https://stores-api.zakaz.ua/graphql")
    assert calls == []


def test_robots_disallowed_url_is_not_requested():
    calls = []
    c, conn = make_client(lambda r: calls.append(r.url.path) or httpx.Response(200, text="x"))
    c.robots.load("zakaz.ua", 200, "User-agent: *\nDisallow: /uk/secret/\n")
    r = c.fetch("https://zakaz.ua/uk/secret/page")
    assert r.outcome == "robots" and calls == []
    assert conn.execute("SELECT outcome FROM http_log").fetchone()[0] == "robots"


def test_403_stops_with_code_2():
    c, _ = make_client(lambda r: httpx.Response(403, text="no"))
    with pytest.raises(StopRun) as e:
        c.fetch(URL)
    assert e.value.code == 2


def test_503_with_cloudflare_marker_stops_but_plain_503_is_retryable():
    c, _ = make_client(lambda r: httpx.Response(503, text="<title>Just a moment...</title> cf-chl"))
    with pytest.raises(StopRun) as e:
        c.fetch(URL)
    assert e.value.code == 2
    c2, _ = make_client(lambda r: httpx.Response(503, text="Service Unavailable"))
    resp = c2.fetch(URL)
    assert resp.outcome == "retryable" and c2.limiter.slowed


def test_benign_cloudflare_beacon_is_ok():
    html = '<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></html>'
    c, _ = make_client(lambda r: httpx.Response(200, text=html))
    assert c.fetch(URL).outcome == "ok"


def test_429_returns_rate_limited_then_three_stop_with_code_3():
    c, _ = make_client(lambda r: httpx.Response(429, text="slow", headers={"retry-after": "5"}))
    assert c.fetch(URL).outcome == "rate_limited"
    assert c.fetch(URL).outcome == "rate_limited"
    with pytest.raises(StopRun) as e:
        c.fetch(URL)
    assert e.value.code == 3


def test_redirect_and_404_classified_and_logged():
    c, conn = make_client(lambda r: httpx.Response(302, headers={"location": "/uk/kyiv/x/"}) if "x" not in r.url.path
                          else httpx.Response(404, text="nf"))
    r = c.fetch(URL)
    assert (r.outcome, r.location) == ("redirect", "/uk/kyiv/x/")
    assert c.fetch("https://zakaz.ua/uk/x").outcome == "not_found"
    assert [x[0] for x in conn.execute("SELECT outcome FROM http_log WHERE url NOT LIKE '%robots%'")] == ["redirect", "not_found"]


def test_network_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("boom")

    c, _ = make_client(handler)
    assert c.fetch(URL).outcome == "retryable"


def test_five_unexpected_pages_in_a_row_stop():
    c, _ = make_client(lambda r: httpx.Response(200, text="<html></html>"))
    c.fetch(URL)
    for _ in range(4):
        c.report_structure(False)
    c.report_structure(True)
    for _ in range(4):
        c.report_structure(False)
    with pytest.raises(StopRun) as e:
        c.report_structure(False)
    assert e.value.code == 3


# --- черга ---

@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    c.execute("INSERT INTO snapshots (snapshot_id, created_at, config_json, status) VALUES ('s','x','{}','running')")
    return c


def test_add_task_is_idempotent(conn):
    assert tasks.add_task(conn, "s", "city_listing", "k", "u")
    assert not tasks.add_task(conn, "s", "city_listing", "k", "u")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_priority_order_and_next_attempt(conn):
    tasks.add_task(conn, "s", "product_detail", "p", "u")
    tasks.add_task(conn, "s", "city_listing", "c", "u")
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    t = tasks.next_ready(conn, "s", tasks.iso(now))
    assert t["stage"] == "city_listing"
    assert tasks.mark_retry(conn, t, "HTTP 500", now) == "pending"
    t2 = tasks.next_ready(conn, "s", tasks.iso(now))
    assert t2["stage"] == "product_detail"          # city_listing відкладено на 5 хв
    later = datetime(2026, 11, 5, 12, 6, tzinfo=timezone.utc)
    assert tasks.next_ready(conn, "s", tasks.iso(later))["stage"] == "city_listing"
    assert tasks.next_wakeup(conn, "s") == "2026-11-05T12:05:00Z"


def test_fifth_failure_marks_failed_and_retry_failed_resets(conn):
    tasks.add_task(conn, "s", "city_listing", "c", "u")
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    statuses = []
    for _ in range(5):
        t = conn.execute("SELECT * FROM tasks").fetchone()
        statuses.append(tasks.mark_retry(conn, t, "err", now))
    assert statuses == ["pending"] * 4 + ["failed"]
    assert tasks.retry_failed(conn, "s") == 1
    t = conn.execute("SELECT * FROM tasks").fetchone()
    assert (t["status"], t["attempts"], t["next_attempt_at"]) == ("pending", 0, None)
