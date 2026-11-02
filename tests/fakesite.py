"""Фейковий zakaz.ua для офлайн-тестів: віддає реальні зразки з tests/fixtures (MockTransport)."""
from pathlib import Path
from urllib.parse import parse_qs

import httpx

F = Path(__file__).parent / "fixtures"
S = F / "samples"


def sample(name: str) -> str:
    return (S / f"{name}.html").read_text(encoding="utf-8")


def make_site(log: list | None = None, fail_first_n_500: int = 0):
    """-> httpx.MockTransport. log збирає (host, path+query)."""
    state = {"errors_left": fail_first_n_500}
    robots = {h: (F / "robots" / f"{h}.robots.txt").read_text(encoding="utf-8")
              for h in ("zakaz.ua", "novus.zakaz.ua")}

    def html(text, status=200):
        return httpx.Response(status, text=text, headers={"set-cookie": "city=kyiv; Path=/"})

    def handler(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        page = int(parse_qs(req.url.query.decode()).get("page", ["1"])[0])
        if log is not None:
            log.append((host, path + (f"?page={page}" if page > 1 else "")))
        if path == "/robots.txt":
            return httpx.Response(200, text=robots[host]) if host in robots else httpx.Response(404)
        if state["errors_left"] > 0 and "/products/" in path:
            state["errors_left"] -= 1
            return httpx.Response(500, text="oops")
        if host == "zakaz.ua":
            if "/products/" in path:
                return html(sample("prod_cod_liver_novus") if "04823088800906" in path else sample("prod_trout_norven"))
            parts = path.strip("/").split("/")          # uk/<city>/<category>
            city, cat = parts[1], parts[2]
            if page > 1:
                return httpx.Response(302, headers={"location": f"/uk/{city}/{cat}/"})
            table = {
                ("kyiv", "canned-fish-zakaz"): sample("agg_kyiv_canned_p1"),
                ("kyiv", "packed-fish-snacks-zakaz"): sample("agg_kyiv_snacks_p1"),
                ("kyiv", "fish-and-seafood-zakaz"): (F / "listing_fish_and_seafood_p1.html").read_text(encoding="utf-8"),
                ("ivano-frankivsk", "fish-and-seafood-zakaz"): sample("agg_if_fish_p1"),
                ("ivano-frankivsk", "caviar-zakaz"): sample("agg_if_caviar_p1"),
                ("ivano-frankivsk", "packed-fish-snacks-zakaz"): sample("agg_poltava_snacks_p1").replace("Полтава", "Івано-Франківськ").replace("/uk/poltava/", "/uk/ivano-frankivsk/"),
                ("ivano-frankivsk", "canned-fish-zakaz"): sample("agg_if_fresh_fish_p1"),   # порожня категорія
            }
            if (city, cat) in table:
                return html(table[(city, cat)])
            if cat.endswith("-zakaz") and city == "kyiv":
                return html(sample("agg_kyiv_tuna_p1"))        # підрозділ
            if city == "ivano-frankivsk":
                return html(sample("agg_if_caviar_p1"))
            return httpx.Response(404, text="nf")
        # novus.zakaz.ua
        if path == "/uk/":
            return html(sample("novus_home"))
        if path.startswith("/uk/categories/"):
            slug = path.strip("/").split("/")[-1]
            if slug == "tins-jars-cooking":
                return html(sample("novus_canned_p1"))
            if page == 1:
                return html(sample("novus_fish_p1"))
            if page == 2:
                return html(sample("novus_fish_p2"))
            return httpx.Response(302, headers={"location": f"/uk/categories/{slug}/"})
        if path == "/uk/stores/":
            if page == 1:
                return html(sample("novus_stores_p1"))
            return httpx.Response(302, headers={"location": "/uk/stores/"})
        if path.startswith("/uk/stores/"):
            return html(sample("novus_store_detail"))
        return httpx.Response(404, text="nf")

    return httpx.MockTransport(handler)
