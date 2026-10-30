"""Normalizer agent: turns whatever a page printed into (minor units, ISO currency).

Storefronts print money in a dozen ways. Everything downstream (history, alerts, the
dashboard) assumes `price_cents` is an int in the store's currency. This is the only place
that should know about currency symbols and locale formatting.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

SYMBOLS = {"US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "kr": "NOK", "¥": "JPY", "CHF": "CHF"}
CODES = {"USD", "EUR", "GBP", "INR", "SEK", "NOK", "DKK", "JPY", "CHF", "CAD", "AUD"}


def detect_currency(text: str, default: Optional[str] = None) -> Optional[str]:
    t = text.strip()
    for code in sorted(CODES):
        if re.search(rf"\b{code}\b", t):
            return code
    for sym, code in sorted(SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        if sym in t:
            return code
    return default


def parse_money(text: str, default_currency: Optional[str] = None) -> tuple[Optional[int], Optional[str]]:
    """Parse a printed price like "$1,299.00" into (129900, "USD").

    Returns (None, currency) when no number is present.
    """
    if text is None:
        return None, default_currency
    currency = detect_currency(text, default_currency)
    # Keep separators until their locale role is known (e.g. 1.299,00 vs 1,299.00).
    value = re.sub(r"[^\d,.+\-]", "", text).strip()
    if not value or not re.search(r"\d", value):
        return None, currency
    try:
        if "," in value and "." in value:
            decimal_sep = "," if value.rfind(",") > value.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            value = value.replace(thousands_sep, "").replace(decimal_sep, ".")
        elif "," in value:
            tail = value.rsplit(",", 1)[1]
            value = value.replace(",", "." if len(tail) in (1, 2) else "")
        elif value.count(".") > 1:
            head, tail = value.rsplit(".", 1)
            value = head.replace(".", "") + ("." + tail if len(tail) in (1, 2) else tail)
        amount = Decimal(value)
    except (ValueError, InvalidOperation):
        return None, currency
    return int(amount * 100), currency


def unit_price(price_cents: Optional[int], pack_size: int) -> Optional[int]:
    if price_cents is None or pack_size <= 1:
        return None
    return int(round(price_cents / pack_size))
