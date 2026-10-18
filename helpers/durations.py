import re

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PART = re.compile(r"(\d+)([smhd])")
_WHOLE = re.compile(r"(?:\d+[smhd])+")


def parse_duration(text: str) -> int | None:
    compact = text.replace(" ", "").lower()
    if _WHOLE.fullmatch(compact) is None:
        return None
    total = sum(int(amount) * UNITS[unit] for amount, unit in _PART.findall(compact))
    return total or None
