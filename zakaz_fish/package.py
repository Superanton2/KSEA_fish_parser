"""Пакет даних для аналітиків (SPEC v2, розділ 9): таблиці, XLSX, CSV, Parquet, словник, README, звіт якості."""
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import context, db
from .config import Config
from .dictionary_v2 import FIELDS_V2, TABLE_ORDER, render_markdown_v2
from .fish import fish_match
from .gs1 import gs1_lookup
from .snapshot import write_discovered_cities

SCOPES = ["core", "canned", "snacks", "frozen", "semi"]
PRICE_FMT = "0.00"

COLS: dict[str, list[str]] = {t: [c for tt, c, *_ in FIELDS_V2 if tt == t] for t in TABLE_ORDER}


def _uah(kop):
    return None if kop is None or pd.isna(kop) else round(float(kop) / 100, 2)


def pack_g(unit: str | None, qty) -> float | None:
    if qty is None or pd.isna(qty):
        return None
    return {"г": float(qty), "кг": float(qty) * 1000}.get(unit)


def per_kg(price_kop, unit_kind, pg) -> float | None:
    """19900 + per_kg -> 199.00; 8650 + 250 г -> 346.00; шт/мл/л -> None."""
    if price_kop is None or pd.isna(price_kop):
        return None
    if unit_kind == "per_kg":
        return round(price_kop / 100, 2)
    if pg is not None and not pd.isna(pg) and pg > 0:
        return round(price_kop / 100 / pg * 1000, 2)
    return None


def _join(values) -> str | None:
    vals = sorted({str(v) for v in values if v is not None and not pd.isna(v)})
    return "; ".join(vals) if vals else None


def _q(conn, sql, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def build_tables(conn: sqlite3.Connection, cfg: Config, sid: str) -> dict[str, pd.DataFrame]:
    kw = cfg.snapshot.fish_keywords
    clr = _q(conn, "SELECT * FROM city_listing_rows WHERE snapshot_id = ?", (sid,))
    chl = _q(conn, "SELECT * FROM chain_listing_rows WHERE snapshot_id = ?", (sid,))
    chains_df = _q(conn, "SELECT * FROM chains ORDER BY chain_id")
    chain_name = dict(zip(chains_df.chain_id, chains_df.name))
    cities_df = _q(conn, "SELECT * FROM cities")
    city_name = dict(zip(cities_df.city_slug, cities_df.name_uk))
    cat_name = {r["slug"]: r["name"] for r in conn.execute("SELECT slug, name FROM categories")}

    ids = sorted(set(clr.product_id) | set(chl.product_id))
    prod = _q(conn, "SELECT * FROM products").set_index("product_id").reindex(ids).reset_index()
    prod = prod.rename(columns={"index": "product_id"})
    idx_name = context.chain_name_index(conn)

    # --- множини для контексту ---
    scopes_by_pid: dict[str, set] = {}
    for df in (clr, chl):
        for pid, sc in zip(df.product_id, df.scope):
            scopes_by_pid.setdefault(pid, set()).add(sc)
    cities_by_pid = clr.groupby("product_id").city_slug.agg(lambda s: set(s)).to_dict()
    chains_by_pid = chl.groupby("product_id").chain_id.agg(lambda s: set(s)).to_dict()
    meta_by_pid = {r.product_id: context.meta_chain_ids(r.meta_chain_names, idx_name)
                   for r in prod.itertuples() if isinstance(r.meta_chain_names, str)}

    # --- products ---
    rows = []
    for r in prod.itertuples():
        pg = pack_g(r.pack_unit, r.pack_qty)
        gs_prefix, gs_org = gs1_lookup(r.ean13 if isinstance(r.ean13, str) else None, r.ean13_valid == 1)
        rows.append({
            "product_id": r.product_id, "id_type": r.id_type, "ean13": r.ean13,
            "ean13_valid": None if pd.isna(r.ean13_valid) else int(r.ean13_valid),
            "gs1_prefix": gs_prefix, "gs1_member_org": gs_org,
            "chain_id_from_id": r.chain_id if r.id_type == "chain" else None,
            "title": r.title, "brand": r.brand, "producer": r.producer, "country": r.country, "species": r.species,
            "weight_text": r.weight_text, "unit_kind": r.unit_kind, "pack_qty": r.pack_qty, "pack_unit": r.pack_unit,
            "pack_g": pg, "composition": r.composition, "kcal_100g": r.kcal_100g, "protein_100g": r.protein_100g,
            "fat_100g": r.fat_100g, "carbs_100g": r.carbs_100g,
            **{f"cat_l{n}_slug": getattr(r, f"cat_l{n}") for n in (1, 2, 3)},
            **{f"cat_l{n}_name": cat_name.get(getattr(r, f"cat_l{n}")) for n in (1, 2, 3)},
            "scopes": _join(scopes_by_pid.get(r.product_id, ())),
            "fish_keyword_match": int(fish_match(r.title, kw)),
            "first_source": r.first_source,
            "n_cities": len(cities_by_pid.get(r.product_id, ())), "cities": _join(cities_by_pid.get(r.product_id, ())),
            "n_chains_storefront": len(chains_by_pid.get(r.product_id, ())),
            "chains_storefront": _join(chains_by_pid.get(r.product_id, ())),
            "meta_price_min_uah": _uah(r.meta_price_min_kop), "meta_price_max_uah": _uah(r.meta_price_max_kop),
            "meta_chains": _join(chain_name.get(c, c) for c in meta_by_pid.get(r.product_id, ())),
            "details_status": r.details_status, "details_scraped_at": r.details_scraped_at,
            "product_url": r.url, "image_url": r.image_url,
        })
    products = pd.DataFrame(rows).reindex(columns=COLS["products"])
    pinfo = prod.set_index("product_id")
    pgmap = dict(zip(products.product_id, products.pack_g))

    attrs = _q(conn, "SELECT product_id, attr_key, attr_title, attr_value FROM product_attributes")
    product_attributes = attrs[attrs.product_id.isin(ids)].reset_index(drop=True)

    # --- контекст ---
    cws = context.chains_with_stores(conn)
    directory = context.chains_with_directory(conn)
    city_products = clr.groupby("city_slug").product_id.agg(lambda s: set(s)).to_dict()
    nat, sim = context.national_set(city_products, cfg.snapshot.baseline_cities)

    def carriers(pid, id_chain):
        return set(chains_by_pid.get(pid, ())) | meta_by_pid.get(pid, set()) | ({id_chain} if id_chain else set())

    # --- city_offers ---
    co_rows = []
    for (city, pid), g in clr.groupby(["city_slug", "product_id"]):
        info = pinfo.loc[pid]
        id_chain = info.chain_id if info.id_type == "chain" else None
        pg = pgmap.get(pid)
        pmin, pmax = g.price_min_kop.min(), g.price_max_kop.max()
        local = carriers(pid, id_chain) & cws.get(city, set())
        has_store = None
        if id_chain and id_chain in directory:
            has_store = int(id_chain in cws.get(city, set()))
        in_nat = None if nat is None else int(pid in nat)
        co_rows.append({
            "snapshot_id": sid, "city_slug": city, "city_name": city_name.get(city), "product_id": pid,
            "scopes": _join(g.scope), "categories": _join(g.category_slug),
            "price_min_uah": _uah(pmin), "price_max_uah": _uah(pmax),
            "price_min_per_kg_uah": per_kg(pmin, info.unit_kind, pg),
            "price_max_per_kg_uah": per_kg(pmax, info.unit_kind, pg),
            "is_promo": int(g.is_promo.max()), "n_listing_rows": len(g),
            "price_conflict": int(g[["price_min_kop", "price_max_kop"]].drop_duplicates().shape[0] > 1),
            "scraped_at_first": g.scraped_at.min(), "scraped_at_last": g.scraped_at.max(),
            "chain_has_store_in_city": has_store, "in_national_set": in_nat,
            "local_carrier_chains": _join(local),
            "supply_hint": context.supply_hint(info.id_type, id_chain, has_store, local, carriers(pid, id_chain), in_nat),
        })
    city_offers = pd.DataFrame(co_rows).reindex(columns=COLS["city_offers"])

    # --- chain_offers ---
    ch_rows = []
    for (chain, pid), g in chl.groupby(["chain_id", "product_id"]):
        info = pinfo.loc[pid]
        best = g.sort_values("price_kop").iloc[0]
        ch_rows.append({
            "snapshot_id": sid, "chain_id": chain, "chain_name": chain_name.get(chain), "product_id": pid,
            "scopes": _join(g.scope), "categories": _join(g.category_slug),
            "price_uah": _uah(best.price_kop), "old_price_uah": _uah(best.old_price_kop),
            "discount_percent": None if pd.isna(best.discount_percent) else int(best.discount_percent),
            "discount_until": best.discount_until_date,
            "price_per_kg_uah": per_kg(best.price_kop, info.unit_kind, pgmap.get(pid)),
            "scraped_at": g.scraped_at.max(),
        })
    chain_offers = pd.DataFrame(ch_rows).reindex(columns=COLS["chain_offers"])

    # --- city_chain_presence ---
    sf = {r["chain_id"]: r["status"] for r in conn.execute("SELECT chain_id, status FROM chain_storefronts WHERE snapshot_id = ?", (sid,))}
    snap_cities = cities_df[cities_df.source.isin(["selector", "manual"])].city_slug.tolist()
    n_stores = _q(conn, "SELECT city_slug, chain_id, COUNT(*) n FROM store_locations GROUP BY city_slug, chain_id")
    n_stores_map = {(r.city_slug, r.chain_id): r.n for r in n_stores.itertuples()}
    chain_prods = chl.groupby("chain_id").product_id.agg(lambda s: set(s)).to_dict()
    pres_rows = []
    for city in snap_cities:
        cp = city_products.get(city, set())
        for chain in chains_df.chain_id:
            if sf.get(chain) == "excluded":
                continue
            n_own = sum(1 for pid in cp if pid in pinfo.index and pinfo.loc[pid].chain_id == chain and pinfo.loc[pid].id_type == "chain")
            n_carried = len(cp & chain_prods.get(chain, set()))
            stores = n_stores_map.get((city, chain), 0)
            pres_rows.append({"snapshot_id": sid, "city_slug": city, "city_name": city_name.get(city), "chain_id": chain,
                              "chain_name": chain_name.get(chain), "n_stores_in_city": stores,
                              "n_chain_id_products_in_listing": n_own, "n_listing_products_carried": n_carried,
                              "presence": "local" if stores else ("remote" if (n_own or n_carried) else "absent")})
    presence = pd.DataFrame(pres_rows).reindex(columns=COLS["city_chain_presence"])

    # --- stores / cities / chains / categories ---
    st = _q(conn, "SELECT * FROM store_locations WHERE last_seen_snapshot = ? ORDER BY chain_id, city_slug, address", (sid,))
    stores = pd.DataFrame({
        "chain_id": st.chain_id, "chain_name": st.chain_id.map(chain_name), "city_slug": st.city_slug,
        "city_name": st.city_name, "address": st.address, "hours_text": st.hours_text,
        "services": st.services_json.map(lambda j: "; ".join(json.loads(j)) if isinstance(j, str) else None),
        "has_live_fish": st.services_json.map(lambda j: None if not isinstance(j, str) else
                                              int(any("жива риба" in s.casefold() for s in json.loads(j)))),
        "store_url": st.store_url, "last_seen_snapshot": st.last_seen_snapshot,
    }).reindex(columns=COLS["stores"])

    stats = _q(conn, "SELECT * FROM city_category_stats WHERE snapshot_id = ?", (sid,))
    roots = cfg.snapshot.scope_roots
    ct_rows = []
    for c in cities_df.itertuples():
        if c.source == "discovered":
            continue
        row = {"city_slug": c.city_slug, "city_name": c.name_uk, "source": c.source,
               "is_valid": None if pd.isna(c.is_valid) else int(c.is_valid),
               "is_baseline": int(c.city_slug in cfg.snapshot.baseline_cities)}
        for sc in SCOPES:
            s = stats[(stats.city_slug == c.city_slug) & (stats.category_slug == roots.get(sc))]
            row[f"site_total_{sc}"] = None if s.empty or pd.isna(s.iloc[0].site_total) else int(s.iloc[0].site_total)
            row[f"seen_{sc}"] = int(clr[(clr.city_slug == c.city_slug) & (clr.scope == sc)].product_id.nunique())
        have = sorted(cws.get(c.city_slug, ()))
        row["n_chains_with_stores"], row["chains_with_stores"] = len(have), "; ".join(have) or None
        ct_rows.append(row)
    cities = pd.DataFrame(ct_rows).reindex(columns=COLS["cities"])

    n_st = st.groupby("chain_id").size().to_dict()
    cws_by_chain = st.groupby("chain_id").city_slug.agg(lambda s: _join(s)).to_dict()
    chains = pd.DataFrame([{
        "chain_id": r.chain_id, "name": r.name, "hostname": r.hostname, "storefront_status": sf.get(r.chain_id),
        "n_products_storefront": len(chain_prods.get(r.chain_id, ())), "n_stores": n_st.get(r.chain_id, 0),
        "cities_with_stores": cws_by_chain.get(r.chain_id)} for r in chains_df.itertuples()]).reindex(columns=COLS["chains"])

    cat_rows = [{"source": "aggregator", "slug": r.slug, "name": r.name, "level": r.level, "parent_slug": r.parent_slug,
                 "scope": r.scope} for r in _q(conn, "SELECT * FROM categories").itertuples()]
    cat_rows += [{"source": f"storefront:{r.chain_id}", "slug": r.slug, "name": r.name,
                  "level": 1 if r.parent_slug is None else 2, "parent_slug": r.parent_slug, "scope": r.scope}
                 for r in _q(conn, "SELECT * FROM chain_categories").itertuples()]
    categories = pd.DataFrame(cat_rows).reindex(columns=COLS["categories"])

    # --- плоскі таблиці для Excel ---
    pcols = ["product_id", "title", "brand", "producer", "country", "species", "cat_l2_name", "cat_l3_name",
             "fish_keyword_match", "ean13", "product_url", "weight_text"]
    pj = products[pcols].set_index("product_id")
    flat_city_offers = city_offers.join(pj, on="product_id").reindex(columns=COLS["flat_city_offers"])
    flat_chain_offers = chain_offers.join(pj, on="product_id").reindex(columns=COLS["flat_chain_offers"])

    # --- snapshot_meta ---
    snap = conn.execute("SELECT * FROM snapshots WHERE snapshot_id = ?", (sid,)).fetchone()
    robots = [f"{r['host']}: {r['status']}" for r in conn.execute(
        "SELECT host, status FROM http_log WHERE snapshot_id = ? AND url LIKE '%/robots.txt' GROUP BY host", (sid,))]
    meta = {
        "snapshot_id": sid,
        "prices_window_start": min([x for x in (clr.scraped_at.min() if len(clr) else None,
                                                chl.scraped_at.min() if len(chl) else None) if x] or [None]),
        "prices_window_end": max([x for x in (clr.scraped_at.max() if len(clr) else None,
                                              chl.scraped_at.max() if len(chl) else None) if x] or [None]),
        "collection_started_at": snap["started_at"], "collection_finished_at": snap["finished_at"],
        "n_requests": conn.execute("SELECT COUNT(*) FROM http_log WHERE snapshot_id = ?", (sid,)).fetchone()[0],
        "n_cities": len(cities), "n_chains_storefront": int((chains.storefront_status == "ok").sum()),
        "n_stores": len(stores), "n_products": len(products), "code_version": snap["code_version"],
        "status": snap["status"], "robots_txt": "; ".join(robots), "config": snap["config_json"],
        "national_set_size": None if nat is None else len(nat),
        "baseline_jaccard": None if sim is None else round(sim, 3),
    }
    snapshot_meta = pd.DataFrame([{"key": k, "value": None if v is None else str(v)} for k, v in meta.items()])

    return {"products": products, "product_attributes": product_attributes, "city_offers": city_offers,
            "chain_offers": chain_offers, "city_chain_presence": presence, "stores": stores, "cities": cities,
            "chains": chains, "categories": categories, "flat_city_offers": flat_city_offers,
            "flat_chain_offers": flat_chain_offers, "snapshot_meta": snapshot_meta}


# --- зведення ---

def build_summary(t: dict[str, pd.DataFrame]) -> list[tuple[str, pd.DataFrame]]:
    co, ch, pr = t["city_offers"], t["chain_offers"], t["products"]
    out: list[tuple[str, pd.DataFrame]] = []
    if len(co):
        ex = co.assign(scope=co.scopes.str.split("; ")).explode("scope")
        a = ex.groupby(["city_name", "scope"]).agg(n_products=("product_id", "nunique"),
                                                   median_price_min_per_kg_uah=("price_min_per_kg_uah", "median")).reset_index()
        out.append(("1. Місто × розділ: товарів і медіана мін. ціни за кг", a))
        b = co.groupby(["city_name", "supply_hint"]).size().rename("n_products").reset_index()
        out.append(("2. Місто × supply_hint: товарів", b))
    if len(ch):
        ex = ch.assign(scope=ch.scopes.str.split("; ")).explode("scope")
        c = ex.groupby(["chain_name", "scope"]).agg(
            n_products=("product_id", "nunique"), promo_share=("discount_percent", lambda s: round(s.notna().mean(), 3)),
            median_discount_percent=("discount_percent", "median")).reset_index()
        out.append(("3. Мережа × розділ: товарів, частка акційних, медіанна знижка", c))
    brands = pr[pr.brand.notna()].groupby("brand").agg(n_cities=("n_cities", "max"), n_products=("product_id", "count")) \
        .reset_index().sort_values(["n_cities", "n_products"], ascending=False).head(30)
    out.append(("4. Топ-30 брендів за кількістю міст", brands))
    if len(co):
        j = co.merge(pr[["product_id", "species"]], on="product_id")
        j = j[j.species.notna()]
        top = j.groupby("species").product_id.nunique().sort_values(ascending=False).head(30).index
        piv = j[j.species.isin(top)].pivot_table(index="species", columns="city_name", values="product_id",
                                                 aggfunc="nunique", fill_value=0).reset_index()
        out.append(("5. Топ-30 значень species × місто (товарів)", piv))
    return out


# --- звіт якості ---

def build_qa(conn: sqlite3.Connection, cfg: Config, sid: str, t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict] = []

    def add(section, item, value, flag=""):
        rows.append({"section": section, "item": item, "value": value, "flag": flag})

    stats = _q(conn, "SELECT * FROM city_category_stats WHERE snapshot_id = ? ORDER BY city_slug, scope, category_slug", (sid,))
    stats["site_total"] = stats.site_total.astype("Int64")
    for r in stats.itertuples():
        full = r.site_total is not pd.NA and r.site_total and r.unique_seen >= 0.95 * r.site_total
        total = "—" if r.site_total is pd.NA else int(r.site_total)
        flag = "" if (r.status == "not_found" or total in ("—", 0) or full or r.filtered_out) else "<95%"
        add("Місто × розділ", f"{r.city_slug} / {r.category_slug} ({r.scope})",
            f"site_total={total}, зібрано={r.unique_seen}, сторінок={r.pages_fetched}, відфільтровано={r.filtered_out}, "
            f"немає в наявності={r.out_of_stock}, статус={r.status}", flag)
    pr = t["products"]
    for st, n in pr.details_status.fillna("немає").value_counts().items():
        add("Картки", f"details_status = {st}", int(n))
    for col in ("brand", "producer", "country", "species", "composition", "kcal_100g"):
        add("Картки", f"заповнено {col}", f"{pr[col].notna().mean():.1%}")
    for r in t["chains"].itertuples():
        add("Вітрини", f"{r.chain_id}: {r.storefront_status}", f"товарів={r.n_products_storefront}, магазинів={r.n_stores}",
            "" if r.storefront_status in ("ok", "excluded") else "перевірити")
    found = _q(conn, "SELECT chain_id, scope FROM chain_categories")
    for chain in t["chains"][t["chains"].storefront_status == "ok"].chain_id:
        have = set(found[found.chain_id == chain].scope)
        for sc in cfg.snapshot.scopes:
            if sc not in have:
                add("Вітрини", f"немає категорії {sc} у мережі {chain}", "не знайдено автоматично",
                    "підказка: [storefront_overrides]")
    only = pr[(pr.first_source == "storefront")]
    add("Вітрини", "товарів лише на вітрині", f"{len(only)} ({len(only) / max(len(pr), 1):.1%})")
    co = t["city_offers"]
    add("Контекст міста", "розмір національного набору", dict(t["snapshot_meta"].values).get("national_set_size"))
    sim = dict(t["snapshot_meta"].values).get("baseline_jaccard")
    add("Контекст міста", "схожість міст-еталонів (Jaccard)", sim, "N не визначено" if sim and float(sim) < 0.9 or sim is None else "")
    for (city, hint), n in co.groupby(["city_name", "supply_hint"]).size().items():
        add("Контекст міста", f"{city}: {hint}", int(n))
    for r in t["city_chain_presence"].query("presence == 'remote'").itertuples():
        add("Контекст міста", f"remote: {r.city_name} × {r.chain_name}", f"товарів у каталозі={r.n_listing_products_carried}")
    for r in t["stores"].groupby(["chain_name", "city_name"]).size().rename("n").reset_index().itertuples():
        add("Магазини", f"{r.chain_name} / {r.city_name}", r.n)
    for r in conn.execute("SELECT stage, task_key, last_error, status FROM tasks WHERE snapshot_id = ? AND "
                          "(status = 'failed' OR last_error = 'robots')", (sid,)):
        add("Задачі", f"{r['status']}: {r['stage']} {r['task_key']}", r["last_error"], "failed" if r["status"] == "failed" else "robots")
    if len(co):
        for r in co[co.price_min_uah > co.price_max_uah].itertuples():
            add("Аномалії", f"price_min > price_max: {r.city_slug} {r.product_id}", f"{r.price_min_uah} > {r.price_max_uah}", "!")
        add("Аномалії", "price_conflict", int(co.price_conflict.sum()))
        j = co.merge(pr[["product_id", "title"]], on="product_id")
        high = j[(j.price_max_per_kg_uah > 20000) & ~j.title.str.contains("ікр", case=False, na=False)]
        add("Аномалії", "ціна за кг > 20 000 грн (крім ікри)", len(high))
    add("Аномалії", "товарів без ваги", int(pr.weight_text.isna().sum()))
    for sc in SCOPES:
        sub = pr[pr.scopes.fillna("").str.contains(sc)]
        if len(sub):
            add("Аномалії", f"частка fish_keyword_match = 0, розділ {sc}", f"{(sub.fish_keyword_match == 0).mean():.1%}")
    h = _q(conn, "SELECT outcome, COUNT(*) n, AVG(delay_s) d FROM http_log WHERE snapshot_id = ? GROUP BY outcome", (sid,))
    add("HTTP", "запитів", int(h.n.sum()))
    for r in h.itertuples():
        add("HTTP", f"outcome = {r.outcome}", int(r.n), "")
    add("HTTP", "середня пауза, с", round(float(h.d.mean()), 2) if len(h) and h.d.notna().any() else None)
    return pd.DataFrame(rows)


def qa_markdown(qa: pd.DataFrame, sid: str) -> str:
    lines = [f"# Звіт якості знімка {sid}", ""]
    for section, g in qa.groupby("section", sort=False):
        lines += [f"## {section}", "", "| Показник | Значення | Позначка |", "|---|---|---|"]
        lines += [f"| {r.item} | {r.value} | {r.flag} |" for r in g.itertuples()]
        lines.append("")
    return "\n".join(lines)


# --- README ---

README = """# Знімок рибних товарів zakaz.ua, {sid}

## 1. Що це
Знімок усіх рибних товарів, доступних на zakaz.ua: {n_cities} міст, розділи {scopes}, {n_chains} вітрин мереж,
{n_stores} магазинів. Товарів: {n_products}. Призначення: аналіз ринку риби в Україні.

## 2. Коли зібрано
Ціни: {pw_start} .. {pw_end} (UTC). Увесь збір: {c_start} .. {c_end} (UTC). Київ = UTC+2 (взимку) / UTC+3 (влітку).

## 3. Файли і таблиці
- `zakaz_fish_{sid}.xlsx`: усе в одному файлі для Excel / Google Sheets, перший аркуш «Як користуватися».
- `csv/<таблиця>.csv`: UTF-8 з BOM, кома, крапка в десяткових. `parquet/<таблиця>.parquet`: типізовані колонки.
- `data_dictionary.csv`, `DATA_DICTIONARY.md`: опис кожної колонки. `qa_report.md`: звіт якості.
- З'єднання таблиць: `product_id` (товар), `city_slug` (місто), `chain_id` (мережа).
  `city_offers` + `products` по `product_id`; `chain_offers` + `chains` по `chain_id`.

## 4. Як відкрити
- Excel / Google Sheets: відкрийте XLSX (CSV в українській локалі Excel може зіпсувати десяткові).
- Python: `pandas.read_parquet("parquet/city_offers.parquet")`
- R: `arrow::read_parquet("parquet/city_offers.parquet")`

## 5. Що означають ціни
- `city_offers`: діапазон цін серед магазинів, які сайт показує для міста гостю без адреси. Туди можуть входити
  магазини ІНШИХ міст (див. `supply_hint`), тож це не «полиця в місті».
- `chain_offers`: ціна мережі на її вітрині в контексті магазину за замовчуванням (сайт не вказує, якого).
- Ціни по окремих магазинах НЕ зібрані.
- `is_promo` у місті означає, що хоча б в одному магазині діє акція.
- «за 1 кг» (`unit_kind = per_kg`) означає, що ціна вже за кілограм; для фасованих є `*_per_kg_uah`.

## 6. Контекст міста
`supply_hint` це ПІДКАЗКА: в однієї мережі можуть бути і місцеві, і віддалені магазини, а сайт не показує, з якого
магазину ціна. `local`: мережа/носії мають магазини в місті; `remote`: магазинів у місті немає; `national`: товар
з «національного набору» (є в містах-еталонах без місцевих партнерів: {baselines}); `local_or_national`;
`unknown`. Розмір національного набору: {nat_size}, схожість еталонів (Jaccard): {jacc}.
`city_chain_presence`: місто × мережа (`local` / `remote` / `absent`).
Ручну перевірку способів доставки (Самовивіз / Кур'єр / Нова пошта) див. у документації проєкту, якщо її зроблено.

## 7. Обмеження
- У розділах є нерибні товари (кава, кабаноси): див. `fish_keyword_match` (евристика за назвою, не класифікація).
- Розділ `frozen` перетинається з `core` (див. `scopes`); `semi` містить лише рядки з рибними назвами.
- Атрибути вводять продавці: «Країна-виробник» ≠ країна походження сировини; «Вид» порожній приблизно в третині товарів.
- `gs1_member_org` це країна реєстрації штрихкоду в GS1, а НЕ країна походження риби. Невалідні коди (≈6%) країни не мають.
- Товари «Немає в наявності» не зберігалися.
- Повнота по містах і розділах: `qa_report.md`.

## 8. Методика
Лише HTML-сторінки zakaz.ua і вітрин мереж; без cookies; один потік з паузами; robots.txt дотримано; API не
використовувались. Версія коду: {code_version}.

## 9. Умови використання
Дані взято з публічних сторінок zakaz.ua. Правила zakaz.ua обмежують відтворення й комерційне використання вмісту;
знімок призначений для дослідження. Перед публічним поширенням погодьте з zakaz.ua. Це не офіційні дані zakaz.ua.

## 10. Як цитувати
Джерело: zakaz.ua, знімок {sid}; збір і обробка: KSEA.
"""

HOWTO = [
    "Як користуватися цим файлом",
    "",
    "flat_city_offers: рядок = товар у каталозі міста (діапазон цін по магазинах). Фільтруйте за city_name, scopes, supply_hint.",
    "flat_chain_offers: рядок = товар на вітрині мережі (одна ціна, стара ціна, знижка).",
    "products: характеристики товарів. city_chain_presence / stores / cities / chains: довідники.",
    "summary: зведення; data_dictionary: опис колонок; qa: звіт якості.",
    "",
    "Застереження: ціни міста можуть містити магазини інших міст (supply_hint); ціни по магазинах не зібрані;",
    "fish_keyword_match = 0 може означати нерибний товар; gs1_member_org це не країна походження.",
    "Деталі: README.md у пакеті.",
]


def _write_xlsx(path: Path, t: dict[str, pd.DataFrame], summary, dictionary: pd.DataFrame, qa: pd.DataFrame) -> None:
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    order = ["flat_city_offers", "flat_chain_offers", "products", "city_chain_presence", "stores", "cities", "chains"]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        wb = xw.book
        ws = wb.create_sheet("Як користуватися")
        for i, line in enumerate(HOWTO, 1):
            ws.cell(row=i, column=1, value=line)
        ws.column_dimensions["A"].width = 120
        ws["A1"].font = Font(bold=True)
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        frames = [(n, t[n]) for n in order] + [("data_dictionary", dictionary), ("qa", qa)]
        for name, df in frames[:7]:
            df.to_excel(xw, sheet_name=name, index=False)
        row = 0
        sheet = "summary"
        for title, df in summary:
            pd.DataFrame({title: []}).to_excel(xw, sheet_name=sheet, startrow=row, index=False)
            df.to_excel(xw, sheet_name=sheet, startrow=row + 1, index=False)
            row += len(df) + 4
        for name, df in frames[7:]:
            df.to_excel(xw, sheet_name=name, index=False)
        ordered = ["Як користуватися"] + order + ["summary", "data_dictionary", "qa"]
        wb._sheets = [wb[n] for n in ordered if n in wb.sheetnames]
        for ws in wb.worksheets:
            if ws.title in ("Як користуватися", "summary"):
                continue
            ws.freeze_panes = "A2"
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions
            for ci, cell in enumerate(ws[1], 1):
                vals = [len(str(c.value)) for c in list(ws.iter_cols(min_col=ci, max_col=ci, max_row=200))[0] if c.value is not None]
                ws.column_dimensions[get_column_letter(ci)].width = min(max(vals + [8]) + 2, 60)
                if str(cell.value).endswith("_uah"):
                    for c in list(ws.iter_cols(min_col=ci, max_col=ci, min_row=2))[0]:
                        c.number_format = PRICE_FMT


def export_package(conn: sqlite3.Connection, cfg: Config, sid: str | None = None) -> Path:
    db.init_db(conn)
    if sid is None:
        sid = conn.execute("SELECT snapshot_id FROM snapshots ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    root = Path(cfg.out_dir) / "exports" / f"zakaz_fish_{sid}"
    if root.exists():
        shutil.rmtree(root)
    (root / "csv").mkdir(parents=True)
    (root / "parquet").mkdir()
    t = build_tables(conn, cfg, sid)
    qa = build_qa(conn, cfg, sid, t)
    dictionary = pd.DataFrame(FIELDS_V2, columns=["table", "column", "type", "source", "description", "example"])
    meta = dict(t["snapshot_meta"].values)
    for name, df in t.items():
        df.to_csv(root / "csv" / f"{name}.csv", index=False, encoding="utf-8-sig")
        df.to_parquet(root / "parquet" / f"{name}.parquet", index=False)
    dictionary.to_csv(root / "data_dictionary.csv", index=False, encoding="utf-8-sig")
    (root / "DATA_DICTIONARY.md").write_text(render_markdown_v2(), encoding="utf-8")
    (root / "qa_report.md").write_text(qa_markdown(qa, sid), encoding="utf-8")
    _write_xlsx(root / f"zakaz_fish_{sid}.xlsx", t, build_summary(t), dictionary, qa)
    (root / "README.md").write_text(README.format(
        sid=sid, n_cities=meta["n_cities"], scopes=", ".join(cfg.snapshot.scopes), n_chains=meta["n_chains_storefront"],
        n_stores=meta["n_stores"], n_products=meta["n_products"], pw_start=meta["prices_window_start"],
        pw_end=meta["prices_window_end"], c_start=meta["collection_started_at"], c_end=meta["collection_finished_at"],
        baselines=", ".join(cfg.snapshot.baseline_cities), nat_size=meta["national_set_size"],
        jacc=meta["baseline_jaccard"], code_version=meta["code_version"]), encoding="utf-8")
    write_discovered_cities(conn, root)
    shutil.make_archive(str(root), "zip", root.parent, root.name)
    return root
