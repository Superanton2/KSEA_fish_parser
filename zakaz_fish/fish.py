"""Евристика «рибна назва» (SPEC v2, 9.3): основи слів із конфігу. Не класифікація."""


def fish_match(title: str | None, keywords) -> bool:
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)
