import gzip

import httpx
import pytest

from zakaz_fish.config import Config
from zakaz_fish.http import FetchError, Fetcher, StopCrawl

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
