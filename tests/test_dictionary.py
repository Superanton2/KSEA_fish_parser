from pathlib import Path

from zakaz_fish import db
from zakaz_fish.dictionary import FIELDS, render_markdown
from zakaz_fish.export import SHEETS

ROOT = Path(__file__).parent.parent


def test_dictionary_covers_every_exported_column_and_nothing_else():
    conn = db.connect(":memory:")
    db.init_db(conn)
    for sheet in SHEETS:
        table = "listing_prices" if sheet == "listing_prices_latest" else sheet
        actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        described = [c for t, c, _ in FIELDS if t == sheet]
        assert len(described) == len(set(described)), f"дублікати в словнику для {sheet}"
        assert set(described) == actual, f"{sheet}: розбіжність {set(described) ^ actual}"


def test_all_descriptions_are_filled():
    assert all(d.strip() for _, _, d in FIELDS)


def test_committed_markdown_is_in_sync():
    assert (ROOT / "DATA_DICTIONARY.md").read_text(encoding="utf-8") == render_markdown()
