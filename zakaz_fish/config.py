import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# Імена агентів, заблокованих у robots.txt zakaz.ua (tests/fixtures/robots/zakaz.ua.robots.txt, 20.10.2026).
# urllib.robotparser порівнює підрядком у першому токені User-Agent, тому токен не має їх містити.
ROBOTS_BLOCKED_AGENTS = (
    "DOC", "Download Ninja", "fast", "Fetch", "grub-client", "HTTrack", "ia_archiver", "k2spider", "larbin",
    "libwww", "linko", "magpie-crawler", "majestic", "Meta-ExternalAgent", "Meta-ExternalFetcher",
    "Microsoft.URL.Control", "MSIECrawler", "NPBot", "Offline Explorer", "oodlebot", "ShopWiki",
    "sitecheck.internetseer.com", "SiteSnagger", "Teleport", "TeleportPro", "UbiCrawler", "Web Link Validator",
    "WebCopier", "WebReaper", "WebStripper", "WebZIP", "wget", "Xenu", "Zao", "Zealbot", "ZyBORG",
)

DEFAULT_FISH_KEYWORDS = [
    "риб", "оселед", "скумбр", "лосос", "сьомг", "форел", "тунец", "тунц", "сардин",
    "шпрот", "кільк", "хамс", "мойв", "тріск", "минта", "хек", "кальмар", "креветк", "міді", "восьмин",
    "краб", "ікр", "анчоус", "бичк", "вобл", "лящ", "тарань", "корюшк", "судак", "щук", "короп", "карас",
    "вугр", "вугор", "палтус", "камбал", "путасу", "сибас", "сібас", "дорад", "тиляпі", "пангас",
    "морепродукт", "молюск", "гребінц", "устриц", "рапан", "ламінарі", "водорост", "сурімі", "масляна",
    "каракатиц", "лобстер", "омар", "лангуст", "осетр", "товстолоб", "тюльк", "горбуш", "нерк", "кижуч",
    "мерлуз", "ставрид", "сайр", "нототен", "окун"]


@dataclass
class RateCfg:
    delay_min_s: float = 1.5
    delay_max_s: float = 3.0
    delay_floor_min_s: float = 1.0
    delay_floor_max_s: float = 2.0
    delay_ceiling_s: float = 60
    speedup_after_ok: int = 300
    slowdown_factor: float = 2.0
    cooldown_base_s: float = 60
    cooldown_max_s: float = 1800
    breaker_window: int = 100
    breaker_errors: int = 8
    breaker_pause_s: float = 1200
    breaker_max_trips: int = 3
    breaker_trip_window_s: float = 10800
    max_requests_per_hour: int = 2400


@dataclass
class SnapshotCfg:
    cities: list[str] = field(default_factory=lambda: [
        "kyiv", "odesa", "lviv", "kharkiv", "zhytomyr", "dnipro", "rivne",
        "chernivtsi", "vinnytsia", "poltava", "ivano-frankivsk", "kryvyi-rih"])
    cities_extra: list[str] = field(default_factory=list)
    city_probes: bool = True
    auto_include_discovered: bool = False
    max_city_probes: int = 150
    baseline_cities: list[str] = field(default_factory=lambda: ["ivano-frankivsk", "kryvyi-rih"])
    scopes: list[str] = field(default_factory=lambda: ["core", "canned", "snacks", "frozen", "semi"])
    chains_exclude: list[str] = field(default_factory=lambda: ["masterzoo", "biotus"])
    chains_only: list[str] = field(default_factory=list)
    max_store_details: int = 0
    storefront_listings: bool = True
    store_directory: bool = True
    store_details: bool = True
    details_max_age_days: int = 30
    fish_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_FISH_KEYWORDS))
    scope_roots: dict[str, str] = field(default_factory=lambda: {
        "core": "fish-and-seafood-zakaz", "canned": "canned-fish-zakaz",
        "snacks": "packed-fish-snacks-zakaz", "frozen": "frozen-fish-and-seafood-zakaz",
        "semi": "half-made-food-zakaz"})
    scope_filters: dict[str, str] = field(default_factory=lambda: {"semi": "fish_keywords"})

    @property
    def all_cities(self) -> list[str]:
        return list(dict.fromkeys(self.cities + self.cities_extra))


@dataclass
class Config:
    city: str = "kyiv"
    lang: str = "uk"
    root_category: str = "fish-and-seafood-zakaz"
    db_path: str = "data/zakaz_fish.sqlite"
    raw_dir: str = "data/raw"
    out_dir: str = "data/out"
    delay_min_s: float = 1.5
    delay_max_s: float = 3.0
    timeout_s: float = 30
    max_pages_per_category: int = 100
    user_agent: str = "KSEA-FishMarketResearch/0.1"
    contact_email: str = ""
    store_descriptions: bool = False
    snapshot: SnapshotCfg = field(default_factory=SnapshotCfg)
    rate: RateCfg = field(default_factory=RateCfg)
    storefront_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def full_user_agent(self) -> str:
        if self.contact_email:
            return f"{self.user_agent} (+mailto:{self.contact_email})"
        return self.user_agent

    @property
    def ua_token(self) -> str:
        """Перший токен User-Agent (до '/'): саме його порівнює robotparser."""
        return self.user_agent.split("/")[0].strip()

    def validate(self) -> None:
        token = self.ua_token.lower()
        bad = [a for a in ROBOTS_BLOCKED_AGENTS if a.lower() in token]
        if bad:
            raise ValueError(f"User-Agent {self.user_agent!r} містить {bad}: заборонено robots.txt zakaz.ua")
        unknown = set(self.snapshot.scopes) - set(self.snapshot.scope_roots)
        if unknown:
            raise ValueError(f"Для розділів {sorted(unknown)} немає snapshot.scope_roots")

    def public_dict(self) -> dict:
        """Конфіг для запису в snapshots.config_json: без contact_email."""
        d = asdict(self)
        d.pop("contact_email", None)
        return d

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).parent


def _build(cls, data: dict, where: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Невідомі ключі в {where}: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | Path | None = None) -> Config:
    """Читає config.toml; якщо файлу немає, повертає значення за замовчуванням."""
    p = Path(path) if path else Path("config.toml")
    if not p.exists():
        if path:
            raise FileNotFoundError(f"Конфіг не знайдено: {p}")
        return Config()
    with p.open("rb") as f:
        data = tomllib.load(f)
    snap = _build(SnapshotCfg, data.pop("snapshot", {}), "[snapshot]")
    rate = _build(RateCfg, data.pop("rate", {}), "[rate]")
    overrides = data.pop("storefront_overrides", {})
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Невідомі ключі в конфігу: {sorted(unknown)}")
    cfg = Config(**data, snapshot=snap, rate=rate, storefront_overrides=overrides)
    cfg.validate()
    return cfg
