"""S8: пакет даних, контекст міста, GS1, похідні поля."""
import zipfile
from datetime import date

import pandas as pd
import pytest
from openpyxl import load_workbook

from tests.fakesite import make_site
from tests.test_snapshot import SID, make_cfg
from zakaz_fish import context, db, snapshot
from zakaz_fish.dictionary_v2 import FIELDS_V2, TABLE_ORDER
from zakaz_fish.gs1 import gs1_lookup
from zakaz_fish.package import build_tables, export_package, per_kg
from zakaz_fish.prices import until_to_date


# --- GS1 ---

@pytest.mark.parametrize("ean,prefix,org", [
    ("4820185100281", "482", "Україна"), ("5901234123457", "590", "Польща"), ("7046493020005", "704", "Норвегія"),
    ("4337182023654", "433", "Німеччина"), ("4895197921582", "489", "Гонконг"), ("8698863690371", "869", "Туреччина"),
    ("8854661010623", "885", "Таїланд"), ("4751007732284", "475", "Латвія"), ("4770190042499", "477", "Литва"),
    ("8410090037855", "841", "Іспанія"), ("2001234567890", "200", "in-store"), ("2991234567890", "299", "in-store"),
])
def test_gs1_prefixes(ean, prefix, org):
    assert gs1_lookup(ean, True) == (prefix, org)


def test_gs1_invalid_ean_has_no_country():
    assert gs1_lookup("8001234567890", False) == (None, None)
    assert gs1_lookup(None, None) == (None, None)


# --- ціна за кг і дата акції ---

def test_price_per_kg():
    assert per_kg(19900, "per_kg", None) == 199.00
    assert per_kg(8650, "pack", 250) == 346.00
    assert per_kg(13900, "pack", None) is None          # шт / мл / л


@pytest.mark.parametrize("text,snap,expected", [
    ("до 28.10", date(2026, 10, 25), "2026-10-28"),
    ("до 05.01", date(2026, 12, 20), "2027-01-05"),
    ("до 28.10", date(2026, 12, 20), "2027-10-28"),
    ("до 30.02", date(2026, 2, 1), None),
    (None, date(2026, 1, 1), None),
])
def test_until_to_date(text, snap, expected):
    assert until_to_date(text, snap) == expected


# --- контекст міста ---

HINTS = [
    # id_type, chain, has_store, local, carriers, in_national, expected
    ("chain", "novus", 1, set(), set(), None, "local"),
    ("chain", "novus", 0, set(), set(), None, "remote"),
    ("chain", "novus", None, set(), set(), None, "unknown"),
    ("ean", None, None, {"novus"}, {"novus"}, 0, "local"),
    ("ean", None, None, {"novus"}, {"novus"}, 1, "local_or_national"),
    ("ean", None, None, set(), {"metro"}, 1, "national"),
    ("ean", None, None, set(), {"metro"}, 0, "remote"),
    ("ean", None, None, set(), set(), 0, "unknown"),
    ("ean", None, None, {"novus"}, {"novus"}, None, "unknown"),
]


@pytest.mark.parametrize("id_type,chain,store,local,carriers,nat,expected", HINTS)
def test_supply_hint_table(id_type, chain, store, local, carriers, nat, expected):
    assert context.supply_hint(id_type, chain, store, local, carriers, nat) == expected


def test_national_set_requires_similar_baselines():
    common = {str(i) for i in range(20)}
    a, b = common | {"a"}, common | {"b"}
    n, sim = context.national_set({"x": a, "y": b}, ["x", "y"])
    assert n == common and sim == pytest.approx(20 / 22)
    n, sim = context.national_set({"x": a, "y": {"1", "2"}}, ["x", "y"])
    assert n is None and sim < 0.9
    assert context.national_set({"x": a}, ["x", "y"]) == (None, None)


def test_chain_names_match_across_scripts():
    assert context.norm_name('Cупермаркет "Харків"') == context.norm_name('Супермаркет Харків'.replace("С", "C"))
    assert context.norm_name("ОнDe") == context.norm_name("ОнDe")


# --- пакет ---

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pkg")
    cfg = make_cfg(tmp)
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    snapshot.create_snapshot(conn, cfg, SID)
    assert snapshot.run_snapshot(conn, cfg, SID, transport=make_site(), sleep=lambda s: None, wait_for_delayed=False) == 0
    root = export_package(conn, cfg, SID)
    return cfg, conn, root


def test_package_files_exist(built):
    cfg, conn, root = built
    names = {p.name for p in root.iterdir()}
    assert {"README.md", "data_dictionary.csv", "DATA_DICTIONARY.md", "qa_report.md", f"zakaz_fish_{SID}.xlsx",
            "csv", "parquet"} <= names
    assert (root.parent / f"zakaz_fish_{SID}.zip").exists()
    with zipfile.ZipFile(root.parent / f"zakaz_fish_{SID}.zip") as z:
        assert any(n.endswith("README.md") for n in z.namelist())
    for t in TABLE_ORDER:
        assert (root / "csv" / f"{t}.csv").exists() and (root / "parquet" / f"{t}.parquet").exists()


def test_tables_not_empty_and_dictionary_covers_all_columns(built):
    cfg, conn, root = built
    tables = build_tables(conn, cfg, SID)
    assert set(tables) == set(TABLE_ORDER)
    for name in ("products", "city_offers", "chain_offers", "stores", "cities", "chains", "categories",
                 "flat_city_offers", "flat_chain_offers", "city_chain_presence", "snapshot_meta"):
        assert len(tables[name]) > 0, name
    for name, df in tables.items():
        described = [c for t, c, *_ in FIELDS_V2 if t == name]
        assert list(df.columns) == described, name
    assert len({(t, c) for t, c, *_ in FIELDS_V2}) == len(FIELDS_V2)
    assert all(f[4] for f in FIELDS_V2)


def test_values_in_tables(built):
    cfg, conn, root = built
    t = build_tables(conn, cfg, SID)
    p = t["products"].set_index("product_id")
    trout = p.loc["04820185100281"]
    assert (trout.gs1_prefix, trout.gs1_member_org, trout.pack_g) == ("482", "Україна", 180.0)
    assert trout.meta_price_max_uah == 450.36 and "NOVUS" in trout.meta_chains
    assert p.loc[p.id_type == "chain"].gs1_member_org.isna().all()
    co = t["city_offers"]
    assert set(co.supply_hint) <= {"local", "remote", "national", "local_or_national", "unknown"}
    k = co[(co.city_slug == "kyiv")].iloc[0]
    assert k.price_min_uah <= k.price_max_uah
    # NOVUS має магазини в Києві, але не в Івано-Франківську
    pres = t["city_chain_presence"].set_index(["city_slug", "chain_id"]).presence
    assert pres[("kyiv", "novus")] == "local"
    assert pres[("ivano-frankivsk", "novus")] in ("remote", "absent")
    st = t["stores"]
    assert st[st.address == "вул. Соборна, 2"].has_live_fish.iloc[0] == 1
    flat = t["flat_chain_offers"]
    assert flat.price_uah.notna().all() and (flat.chain_name == "NOVUS").all()


def test_xlsx_opens_with_expected_sheets_and_formatting(built):
    cfg, conn, root = built
    wb = load_workbook(root / f"zakaz_fish_{SID}.xlsx")
    assert wb.sheetnames == ["Як користуватися", "flat_city_offers", "flat_chain_offers", "products",
                             "city_chain_presence", "stores", "cities", "chains", "summary", "data_dictionary", "qa"]
    ws = wb["flat_city_offers"]
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref
    header = [c.value for c in ws[1]]
    col = header.index("price_min_uah") + 1
    assert ws.cell(row=2, column=col).number_format == "0.00"
    assert wb["summary"].max_row > 5


def test_csv_utf8_bom_and_parquet_types(built):
    cfg, conn, root = built
    assert (root / "csv" / "products.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    df = pd.read_parquet(root / "parquet" / "city_offers.parquet")
    assert df.price_min_uah.dtype.kind == "f" and len(df) > 0


def test_readme_and_qa_report_are_filled(built):
    cfg, conn, root = built
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "{" not in readme and SID in readme and "supply_hint" in readme
    qa = (root / "qa_report.md").read_text(encoding="utf-8")
    assert "Місто × розділ" in qa and "HTTP" in qa
    assert "secret@example.org" not in readme + qa
    assert "secret@example.org" not in (root / "csv" / "snapshot_meta.csv").read_text(encoding="utf-8")
