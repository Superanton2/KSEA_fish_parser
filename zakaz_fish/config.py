import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    city: str = "kyiv"
    lang: str = "uk"
    root_category: str = "fish-and-seafood-zakaz"
    db_path: str = "data/zakaz_fish.sqlite"
    raw_dir: str = "data/raw"
    delay_min_s: float = 1.5
    delay_max_s: float = 3.0
    timeout_s: float = 30
    max_pages_per_category: int = 100
    user_agent: str = "KSEA-FishMarketResearch/0.1"
    contact_email: str = ""
    store_descriptions: bool = False

    @property
    def full_user_agent(self) -> str:
        if self.contact_email:
            return f"{self.user_agent} (+mailto:{self.contact_email})"
        return self.user_agent


def load_config(path: str | Path | None = None) -> Config:
    """Читає config.toml; якщо файлу немає, повертає значення за замовчуванням."""
    p = Path(path) if path else Path("config.toml")
    if not p.exists():
        if path:
            raise FileNotFoundError(f"Конфіг не знайдено: {p}")
        return Config()
    with p.open("rb") as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Невідомі ключі в конфігу: {sorted(unknown)}")
    return Config(**data)
