"""Збір живих зразків сторінок для написання парсерів (SPEC v2, розділ 8.1).

Не більше MAX_REQUESTS запитів за запуск; той самий клієнт (пауза, без cookies, заборонені хости).
Перед запитом до хоста перевіряється його robots.txt. Відповіді (зокрема 3xx/404) зберігаються як є.
"""
import json
import re
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .config import Config
from .http import FetchError, Fetcher, StopCrawl

MAX_REQUESTS = 40
FIXTURES = Path("tests/fixtures")
Z = "https://zakaz.ua/uk"

ROBOTS_HOSTS = ["zakaz.ua", "novus.zakaz.ua", "metro.zakaz.ua", "tavriav.zakaz.ua", "stores-api.zakaz.ua"]
CHAINS = ["novus", "metro", "tavriav"]

STATIC_V2 = [
    ("agg_kyiv_canned_p1", f"{Z}/kyiv/canned-fish-zakaz/"),
    ("agg_kyiv_tuna_p1", f"{Z}/kyiv/tuna-zakaz/"),
    ("agg_kyiv_snacks_p1", f"{Z}/kyiv/packed-fish-snacks-zakaz/"),
    ("agg_kyiv_frozen_p1", f"{Z}/kyiv/frozen-fish-and-seafood-zakaz/"),
    ("agg_kyiv_semi_p1", f"{Z}/kyiv/half-made-food-zakaz/"),
    ("agg_if_fish_p1", f"{Z}/ivano-frankivsk/fish-and-seafood-zakaz/"),
    ("agg_if_caviar_p1", f"{Z}/ivano-frankivsk/caviar-zakaz/"),
    ("agg_lviv_fish_p2", f"{Z}/lviv/fish-and-seafood-zakaz/?page=2"),
    ("prod_trout_norven", f"{Z}/products/file-forel-norven-180g--04820185100281/"),
    ("prod_cod_liver_novus", f"{Z}/products/pechinka-triski-novus-121g--04823088800906/"),
    ("novus_stores_p1", "https://novus.zakaz.ua/uk/stores/"),
    ("novus_store_detail", "https://novus.zakaz.ua/uk/stores/kyiv/vul-soborna-2/"),
    ("metro_stores_p1", "https://metro.zakaz.ua/uk/stores/"),
    ("tavriav_stores_p1", "https://tavriav.zakaz.ua/uk/stores/"),
]


class Sampler:
    def __init__(self, fetcher: Fetcher, out_dir: Path, ua: str, max_requests: int = MAX_REQUESTS):
        self.f = fetcher
        self.dir = out_dir
        self.ua = ua
        self.max = max_requests
        self.entries: list[dict] = []
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.stopped: str | None = None
        out_dir.mkdir(parents=True, exist_ok=True)

    # --- один запит ---
    def fetch(self, name: str, url: str, *, is_robots: bool = False) -> str | None:
        if self.stopped:
            return None
        entry = {"name": name, "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.entries.append(entry)
        host = urlsplit(url).hostname
        if not is_robots:
            rp = self.robots.get(host)
            if rp is None and host not in self.robots:
                entry["skipped"] = "robots.txt хоста не прочитано"
                return None
            if rp is not None and not rp.can_fetch(self.ua, url):
                entry["skipped"] = "robots.txt забороняє"
                return None
        if self.f.requests >= self.max:
            entry["skipped"] = f"ліміт {self.max} запитів"
            return None
        try:
            resp = self.f.fetch_response(url)
        except StopCrawl as e:
            self.stopped = str(e)
            entry["error"] = str(e)
            return None
        except FetchError as e:
            entry["error"] = str(e)
            return None
        entry["status"] = resp.status_code
        entry["final_url"] = str(resp.url)
        if resp.is_redirect:
            entry["location"] = resp.headers.get("location")
        entry["bytes"] = len(resp.content)
        path = (FIXTURES / "robots" / f"{host}.robots.txt") if is_robots else (self.dir / f"{name}.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text, encoding="utf-8")
        entry["file"] = str(path)
        if is_robots:
            rp = urllib.robotparser.RobotFileParser()
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            elif resp.status_code == 404:
                rp.parse([])
            else:
                rp = None  # недоступний: не запитуємо цей хост
            self.robots[host] = rp
        return resp.text if resp.status_code == 200 else None

    def save_index(self) -> Path:
        p = self.dir / "index.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"entries": []}
        names = {e["name"] for e in self.entries}
        entries = [e for e in old["entries"] if e["name"] not in names] + self.entries
        p.write_text(json.dumps({"requests": self.f.requests, "stopped": self.stopped, "entries": entries},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        return p


def find_link(html: str, base: str, pattern: str, texts: list[str]) -> str | None:
    """Посилання, що відповідає pattern у href, текст якого починається з одного з texts (без регістру)."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        if not re.search(pattern, a["href"]):
            continue
        t = " ".join(a.get_text(" ", strip=True).split()).lower()
        if any(t.startswith(x.lower()) for x in texts):
            return urljoin(base, a["href"])
    return None


def find_first_product_link(html: str, base: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    a = soup.select_one('a[href*="/products/"]')
    return urljoin(base, a["href"]) if a else None


def run_sample(cfg: Config, fetcher: Fetcher, preset: str = "v2", extra: list[tuple[str, str]] | None = None,
               out_dir: Path = FIXTURES / "samples") -> Sampler:
    if preset not in ("v2", "none"):
        raise ValueError(f"Невідомий preset: {preset}")
    s = Sampler(fetcher, out_dir, cfg.user_agent.split("/")[0])
    hosts = ROBOTS_HOSTS if preset == "v2" else sorted({urlsplit(u).hostname for _, u in extra or []})
    for host in hosts:
        s.fetch(f"robots_{host}", f"https://{host}/robots.txt", is_robots=True)
    if preset == "none":
        for name, url in extra or []:
            s.fetch(name, url)
        s.save_index()
        return s
    for name, url in STATIC_V2:
        s.fetch(name, url)
    # Вітрини: категорії знаходимо за назвою на головній, не вгадуємо.
    first_card: str | None = None
    for chain in CHAINS:
        base = f"https://{chain}.zakaz.ua/uk/"
        home = s.fetch(f"{chain}_home", base)
        if not home:
            continue
        fish = find_link(home, base, r"/categories/", ["Риба та морепродукти"])
        if fish:
            html = s.fetch(f"{chain}_fish_p1", fish)
            if html and first_card is None and chain == "novus":
                first_card = find_first_product_link(html, base)
            if chain == "novus":
                s.fetch("novus_fish_p2", fish + ("&" if "?" in fish else "?") + "page=2")
        else:
            s.entries.append({"name": f"{chain}_fish_p1", "skipped": "посилання на «Риба та морепродукти» не знайдено на головній"})
        if chain == "novus":
            canned = find_link(home, base, r"/categories/", ["Консерви"])
            if canned:
                s.fetch("novus_canned_p1", canned)
            else:
                s.entries.append({"name": "novus_canned_p1", "skipped": "посилання «Консерви» не знайдено на головній"})
    # Чи відкриває zakaz.ua/uk/products/<шлях з вітрини> ту саму картку (1 запит)
    if first_card:
        path = urlsplit(first_card).path
        m = re.search(r"/products/(.+)$", path)
        if m:
            s.fetch("prod_from_storefront_path", f"{Z}/products/{m.group(1)}")
    for name, url in extra or []:
        s.fetch(name, url)
    s.save_index()
    return s
