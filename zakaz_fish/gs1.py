"""Префікс GS1 -> організація-реєстратор штрихкоду (SPEC v2, 9.3).

УВАГА: це країна, де власник штрихкоду зареєстрував його в GS1, а НЕ країна походження товару.
Таблиця zakaz_fish/data/gs1_prefixes.csv складена за переліком префіксів GS1 (gs1.org/standards/id-keys/company-prefix);
перед публікацією датасету звірте її з актуальною редакцією на сайті GS1.
"""
import csv
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "gs1_prefixes.csv"


@lru_cache(maxsize=1)
def _table() -> tuple[tuple[int, int, str], ...]:
    with DATA.open(encoding="utf-8", newline="") as f:
        return tuple((int(r["start"]), int(r["end"]), r["member_org"]) for r in csv.DictReader(f))


def gs1_lookup(ean13: str | None, valid: bool | int | None) -> tuple[str | None, str | None]:
    """-> (gs1_prefix, member_org). Невалідний або відсутній EAN дає (None, None): внутрішні коди не мають країни."""
    if not ean13 or not valid or len(ean13) != 13 or not ean13.isdigit():
        return None, None
    prefix = int(ean13[:3])
    for lo, hi, org in _table():
        if lo <= prefix <= hi:
            return ean13[:3], org
    return ean13[:3], None
