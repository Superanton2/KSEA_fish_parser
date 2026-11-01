import logging
import re
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

_PACK = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(кг|г|мл|л|шт)\.?$")
_PER_KG = re.compile(r"^за\s+1\s*кг$")


def price_to_kop(text: str) -> int:
    """'199.00 ' -> 19900. Через Decimal, без float; пробіли (зокрема nbsp) і ₴ прибираємо."""
    cleaned = re.sub(r"[\s  ₴]", "", text or "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Не вдалося розібрати ціну: {text!r}") from None
    return int(value * 100)


def parse_weight(text: str | None) -> tuple[str, float | None, str | None]:
    """'за 1 кг' -> ('per_kg', 1, 'кг'); '250 г' -> ('pack', 250, 'г'); інше -> ('unknown', None, None)."""
    t = re.sub(r"[\s  ]+", " ", (text or "")).strip().lower()
    if not t:  # порожнє поле ваги (напр. устриці поштучно): не помилка, просто немає даних
        return "unknown", None, None
    if _PER_KG.match(t):
        return "per_kg", 1, "кг"
    m = _PACK.match(t)
    if m:
        qty = float(m.group(1).replace(",", "."))
        return "pack", int(qty) if qty.is_integer() else qty, m.group(2)
    log.warning("Нерозпізнана вага/одиниця: %r", text)
    return "unknown", None, None


_UNTIL = re.compile(r"до\s+(\d{1,2})\.(\d{1,2})")


def until_to_date(text: str | None, snapshot_date) -> str | None:
    """'до 28.10' + дата знімка -> ISO-дата з роком знімка; якщо вона раніша за знімок більш ніж на 30 днів,
    рік +1. Некоректна дата -> None (сирий текст зберігається окремо)."""
    from datetime import date, timedelta
    m = _UNTIL.search(text or "")
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    try:
        d = date(snapshot_date.year, month, day)
        if d < snapshot_date - timedelta(days=30):
            d = date(snapshot_date.year + 1, month, day)
    except ValueError:
        return None
    return d.isoformat()
