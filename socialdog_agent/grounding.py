"""Check that every number in an answer can be traced to this turn's tool results.

The check is deliberately literal: a number counts as grounded when it equals a
tool value at the precision the answer shows (so 1234.567 may be written 1234.57
or 1235). Percentages may match either a percent value or a ratio, and 万 is
expanded. Dates, times, list markers and small integers are not checked.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable

IGNORED = [
    r"\d{4}-\d{1,2}-\d{1,2}", r"\d{4}-W\d{1,2}", r"\d{4}-\d{2}(?!\d)", r"\d{4}年(?:\d{1,2}月(?:\d{1,2}[日号])?)?",
    r"\d{1,2}月\d{1,2}[日号]?", r"\d{1,2}月(?:份)?", r"\d{1,2}:\d{2}(?::\d{2})?", r"(?:星期|周)[1-7一二三四五六日天]",
    r"第\d+", r"(?<![A-Za-z])[A-Z]\d{2}(?!\d)", r"(?m)^\s*\d+[.、)]\s", r"T\d{2}",
]
# ASCII-only look-behind: in Unicode regexes Chinese characters count as \w.
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)(\s*%|\s*万)?")
SMALL_INTEGER = 10


def _clean(text: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    for pattern in IGNORED:
        text = re.sub(pattern, " ", text)
    return text


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def evidence_numbers(values: Iterable[Any]) -> set[Decimal]:
    found: set[Decimal] = set()

    def walk(value, key=""):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            number = _decimal(value)
            if number is not None:
                found.add(number)
                if key.endswith("_cents"):
                    found.add(number / 100)
        elif isinstance(value, str):
            for match in NUMBER.finditer(re.sub(r"(?<=\d),(?=\d{3})", "", value)):
                number = _decimal(match.group(1))
                if number is not None:
                    found.add(number)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, str(k))
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, key)

    for item in values:
        walk(item)
    return found


def _matches(token: Decimal, decimals: int, scale: Decimal, pool: set[Decimal]) -> bool:
    tolerance = Decimal(5) * Decimal(10) ** -(decimals + 1) * scale + Decimal("1e-9")
    target = token * scale
    return any(abs(abs(target) - abs(e)) <= tolerance for e in pool)


def ungrounded_numbers(answer: str, evidence: Iterable[Any]) -> list[str]:
    """Return number tokens in the answer that no evidence value supports."""
    pool = evidence_numbers(evidence)
    missing = []
    for match in NUMBER.finditer(_clean(answer)):
        raw, suffix = match.group(1), (match.group(2) or "").strip()
        token = _decimal(raw)
        if token is None:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if not suffix and decimals == 0 and abs(token) <= SMALL_INTEGER:
            continue
        if suffix == "万":
            ok = _matches(token, decimals, Decimal(10000), pool)
        elif suffix == "%":
            ok = _matches(token, decimals, Decimal(1), pool) or _matches(token, decimals, Decimal("0.01"), pool)
        else:
            ok = _matches(token, decimals, Decimal(1), pool)
        if not ok:
            missing.append(raw + suffix)
    return list(dict.fromkeys(missing))


def mentions(answer: str, value: Any) -> bool:
    """Whether the answer states value (at the answer's own precision, 万 and % allowed)."""
    target = _decimal(value)
    if target is None:
        return False
    for match in NUMBER.finditer(_clean(answer)):
        raw, suffix = match.group(1), (match.group(2) or "").strip()
        token = _decimal(raw)
        if token is None:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = Decimal(10000) if suffix == "万" else Decimal(1)
        if _matches(token, decimals, scale, {target}):
            return True
    return False


def mentions_date(answer: str, iso_day: str) -> bool:
    year, month, day = iso_day[:10].split("-")
    forms = [iso_day[:10], f"{int(month)}月{int(day)}日", f"{int(month)}月{int(day)}号",
             f"{int(month)}/{int(day)}", f"{month}-{day}", f"{int(month)}.{int(day)}"]
    return any(form in answer for form in forms)
