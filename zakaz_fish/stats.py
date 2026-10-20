import sqlite3


def collect_stats(conn: sqlite3.Connection) -> str:
    def rows(sql):
        return [tuple(r) for r in conn.execute(sql)]

    one = lambda sql: conn.execute(sql).fetchone()[0]
    lines = [
        f"Товарів: {one('SELECT COUNT(*) FROM products')} "
        f"(активних {one('SELECT COUNT(*) FROM products WHERE is_active = 1')}, "
        f"з деталями {one('SELECT COUNT(*) FROM products WHERE details_scraped_at IS NOT NULL')})",
        "",
        "По id_type:",
        *[f"  {t}: {n}" for t, n in rows("SELECT id_type, COUNT(*) FROM products GROUP BY id_type ORDER BY 2 DESC")],
        "",
        "По категоріях (за списками, активні товари):",
        *[f"  {slug}: {n}" for slug, n in rows(
            """SELECT pc.slug, COUNT(*) FROM product_categories pc
               JOIN products p USING (product_id)
               WHERE pc.source = 'listing' AND p.is_active = 1
               GROUP BY pc.slug ORDER BY 2 DESC""")],
        "",
        "Товари мереж (id_type='chain'):",
        *[f"  {c}: {n}" for c, n in rows(
            "SELECT chain_id, COUNT(*) FROM products WHERE id_type = 'chain' GROUP BY chain_id ORDER BY 2 DESC")],
        "",
        f"Мереж у довіднику: {one('SELECT COUNT(*) FROM chains')}, магазинів: {one('SELECT COUNT(*) FROM stores')}",
    ]
    last = rows("SELECT run_id, kind, status FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1")
    if last:
        lines.append("Останній запуск: {} ({}, {})".format(*last[0]))
    return "\n".join(lines)
