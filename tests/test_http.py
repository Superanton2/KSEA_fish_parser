import gzip

import httpx
import pytest

from zakaz_fish.config import Config
from zakaz_fish.http import FetchError, Fetcher, PageRedirected, StopCrawl

URL = "https://zakaz.ua/uk/kyiv/fish-and-seafood-zakaz/"


def make(handler, tmp_path=None):
    sleeps = []
    f = Fetcher(Config(), raw_dir=tmp_path, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return f, sleeps


@pytest.mark.parametrize("status", [403, 429, 503])
def test_stop_on_blocking_status(status):
    f, _ = make(lambda r: httpx.Response(status, text="no"))
    with pytest.raises(StopCrawl):
        f.get(URL)
    assert f.requests == 1


@pytest.mark.parametrize("marker", ["Just a moment", "cf-chl", "challenge-platform"])
def test_stop_on_cloudflare_body(marker):
    f, _ = make(lambda r: httpx.Response(200, text=f"<html>{marker}...</html>"))
    with pytest.raises(StopCrawl):
        f.get(URL)


def test_success_saves_gzip_and_sets_headers(tmp_path):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text="<html>привіт</html>")

    f, sleeps = make(handler, tmp_path)
    assert f.get(URL, "run1/listing/x_p1.html.gz") == "<html>привіт</html>"
    with gzip.open(tmp_path / "run1/listing/x_p1.html.gz", "rt", encoding="utf-8") as fh:
        assert fh.read() == "<html>привіт</html>"
    assert seen["accept-language"] == "uk"
    assert "KSEA-FishMarketResearch" in seen["user-agent"]
    assert 1.5 <= sleeps[0] <= 3.0


def test_retry_on_500_then_success():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502) if len(calls) < 3 else httpx.Response(200, text="ok")

    f, sleeps = make(handler)
    assert f.get(URL) == "ok"
    assert len(calls) == 3
    assert 5 in sleeps and 15 in sleeps


def test_retry_exhausted_raises_fetch_error():
    f, sleeps = make(lambda r: httpx.Response(500))
    with pytest.raises(FetchError):
        f.get(URL)
    assert f.requests == 4
    assert [s for s in sleeps if s in (5, 15, 45)] == [5, 15, 45]


def test_network_error_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("boom")
        return httpx.Response(200, text="ok")

    f, _ = make(handler)
    assert f.get(URL) == "ok"


def test_404_is_not_retried():
    f, _ = make(lambda r: httpx.Response(404, text="x"))
    with pytest.raises(FetchError):
        f.get(URL)
    assert f.requests == 1


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("x"), httpx.ReadError("x"), httpx.ReadTimeout("x"),
    httpx.ConnectTimeout("x"), httpx.PoolTimeout("x"),
])
def test_retryable_exceptions_are_retried(exc):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise exc
        return httpx.Response(200, text="ok")

    f, sleeps = make(handler)
    assert f.get(URL) == "ok"
    assert len(calls) == 2 and 5 in sleeps


@pytest.mark.parametrize("exc", [
    httpx.UnsupportedProtocol("x"), httpx.LocalProtocolError("x"),
    httpx.RemoteProtocolError("x"), httpx.WriteError("x"),
])
def test_other_transport_errors_fail_immediately(exc):
    calls = []

    def handler(request):
        calls.append(1)
        raise exc

    f, sleeps = make(handler)
    with pytest.raises(FetchError):
        f.get(URL)
    assert len(calls) == 1
    assert not [s for s in sleeps if s in (5, 15, 45)]


def test_invalid_url_fails_immediately():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.InvalidURL("x")

    f, sleeps = make(handler)
    with pytest.raises(FetchError):
        f.get(URL)
    assert len(calls) == 1 and not sleeps[1:]


def test_unsupported_protocol_with_real_transport_fails_immediately():
    sleeps = []
    f = Fetcher(Config(), sleep=sleeps.append)  # реальний транспорт: помилка виникає до мережі
    with pytest.raises(FetchError):
        f.get("ftp://zakaz.ua/x")
    with pytest.raises(FetchError):
        f.get("/uk/relative/")
    assert f.requests == 2
    assert not [s for s in sleeps if s in (5, 15, 45)]


BEACON = ("<html><body><div data-marker='CatalogProductTile'></div><script>var a=document.createElement('script');"
          "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';</script></body></html>")


def test_cloudflare_beacon_script_is_not_a_challenge():
    f, _ = make(lambda r: httpx.Response(200, text=BEACON))
    assert f.get(URL) == BEACON


@pytest.mark.parametrize("body", [
    "<title>Just a moment...</title>" + BEACON,
    "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script>" + BEACON,
    "<script>window._cf_chl_opt={}</script>cf-chl-bypass",
])
def test_real_challenge_still_stops_even_with_beacon(body):
    f, _ = make(lambda r: httpx.Response(200, text=body))
    with pytest.raises(StopCrawl) as e:
        f.get(URL)
    assert e.value.body == body


def test_blocking_status_stops_even_with_ok_looking_body():
    f, _ = make(lambda r: httpx.Response(403, text=BEACON))
    with pytest.raises(StopCrawl):
        f.get(URL)


def test_redirect_followed_by_default_and_reported_when_disabled():
    def handler(request):
        if request.url.path.endswith("/old/"):
            return httpx.Response(307, headers={"location": "https://zakaz.ua/uk/kyiv/new/"})
        return httpx.Response(200, text="new page")

    f, _ = make(handler)
    assert f.get("https://zakaz.ua/uk/kyiv/old/") == "new page"
    n = f.requests
    with pytest.raises(PageRedirected) as e:
        f.get("https://zakaz.ua/uk/kyiv/old/", follow_redirects=False)
    assert e.value.location == "https://zakaz.ua/uk/kyiv/new/"
    assert f.requests == n + 1  # другий запит за Location не робився
