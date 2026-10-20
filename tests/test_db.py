from zakaz_fish import db

EXPECTED = {"runs", "chains", "stores", "categories", "products", "product_categories",
            "product_attributes", "listing_prices", "store_prices"}


def test_init_db_creates_all_tables_idempotently():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.init_db(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED <= names


def test_start_run_ids_are_unique():
    conn = db.connect(":memory:")
    db.init_db(conn)
    ids = {db.start_run(conn, "seed", "kyiv") for _ in range(3)}
    assert len(ids) == 3
