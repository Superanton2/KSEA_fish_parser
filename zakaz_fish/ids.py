import re

_EAN14 = re.compile(r"^\d{14}$")
_CHAIN = re.compile(r"^([a-z]+)(\d{14})$")


def parse_product_url(url: str) -> tuple[str, str]:
    """'/uk/products/<slug>--<product_id>/' -> (slug, product_id).

    product_id це хвіст після ОСТАННЬОГО '--'.
    """
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    last = path.rsplit("/", 1)[-1]
    if "--" not in last:
        raise ValueError(f"У URL немає product_id: {url!r}")
    slug, product_id = last.rsplit("--", 1)
    if not product_id:
        raise ValueError(f"Порожній product_id: {url!r}")
    return slug, product_id


def ean13_check(code13: str) -> bool:
    if not re.fullmatch(r"\d{13}", code13 or ""):
        return False
    digits = [int(c) for c in code13]
    total = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == digits[12]


def classify_id(product_id: str, known_chains) -> tuple[str, str | None, bool | None, str | None]:
    """-> (id_type, ean13, ean13_valid, chain_id)."""
    if _EAN14.match(product_id):
        ean13 = product_id[1:]
        return "ean", ean13, ean13_check(ean13), None
    m = _CHAIN.match(product_id)
    if m and m.group(1) in known_chains:
        return "chain", None, None, m.group(1)
    return "other", None, None, None
