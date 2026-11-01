"""Контекст міста: місцеві й віддалені магазини (SPEC v2, 9.4). Усе рахується зі зібраних HTML-даних.

`supply_hint` це підказка, а не факт: сайт не вказує, з якого магазину ціна.
"""
import json
import sqlite3

JACCARD_MIN = 0.9
_LOOKALIKE = str.maketrans("aceopxykmthb", "асеорхукмтнв")   # латиниця -> схожа кирилиця


def norm_name(s: str) -> str:
    """Назва мережі для зіставлення: у meta зустрічається мішанина кирилиці й латиниці (напр. «ОнDe»)."""
    s = (s or "").casefold().translate(_LOOKALIKE)
    return "".join(ch for ch in s if ch.isalnum())


def chain_name_index(conn: sqlite3.Connection) -> dict[str, str]:
    idx = {norm_name(r["name"]): r["chain_id"] for r in conn.execute("SELECT chain_id, name FROM chains")}
    idx.update({norm_name(r["chain_id"]): r["chain_id"] for r in conn.execute("SELECT chain_id FROM chains")})
    return idx


def meta_chain_ids(meta_json: str | None, index: dict[str, str]) -> set[str]:
    try:
        names = json.loads(meta_json) if meta_json else []
    except ValueError:
        return set()
    return {index[n] for n in map(norm_name, names) if n in index}


def chains_with_stores(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in conn.execute("SELECT DISTINCT city_slug, chain_id FROM store_locations WHERE city_slug IS NOT NULL"):
        out.setdefault(r["city_slug"], set()).add(r["chain_id"])
    return out


def chains_with_directory(conn: sqlite3.Connection) -> set[str]:
    """Мережі, чий довідник магазинів зібрано (інакше «немає магазинів у місті» не можна стверджувати)."""
    return {r[0] for r in conn.execute("SELECT DISTINCT chain_id FROM store_locations")}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def national_set(city_products: dict[str, set[str]], baseline_cities: list[str]) -> tuple[set[str] | None, float | None]:
    """Національний набір N = перетин товарів міст-еталонів; визначений, лише якщо схожість їхніх множин ≥ 0,9."""
    sets = [city_products[c] for c in baseline_cities if c in city_products]
    if len(sets) < 2:
        return None, None
    sim = min(jaccard(sets[i], sets[j]) for i in range(len(sets)) for j in range(i + 1, len(sets)))
    if sim < JACCARD_MIN:
        return None, sim
    return set.intersection(*sets), sim


def supply_hint(id_type: str, chain_from_id: str | None, chain_has_store: int | None,
                local_carriers: set[str], carriers: set[str], in_national: int | None) -> str:
    if id_type == "chain":
        if chain_has_store == 1:
            return "local"
        if chain_has_store == 0:
            return "remote"
        return "unknown"
    if in_national is None:
        return "unknown"
    if local_carriers:
        return "local_or_national" if in_national else "local"
    if in_national:
        return "national"
    return "remote" if carriers else "unknown"
