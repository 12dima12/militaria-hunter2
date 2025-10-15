"""
Text formatting utilities for Telegram HTML messages
"""
from datetime import datetime
from html import escape as htmlesc
from typing import Optional

from utils.datetime_utils import display_berlin


def br_join(lines):
    """Join lines with newlines, filtering out empty/None lines"""
    return "\n".join([ln for ln in lines if ln is not None and str(ln).strip() != ""])


def b(txt):
    """Bold text"""
    return f"<b>{htmlesc(str(txt))}</b>"


def i(txt):
    """Italic text"""
    return f"<i>{htmlesc(str(txt))}</i>"


def a(label, url):
    """Link with label"""
    return f'<a href="{htmlesc(url)}">{htmlesc(label)}</a>'


def code(txt):
    """Code/monospace text"""
    return f"<code>{htmlesc(str(txt))}</code>"


def fmt_ts_de(dt_utc: Optional[datetime]) -> str:
    """Format UTC datetime as German Berlin time, or / if None."""

    return display_berlin(dt_utc)


def fmt_price_de(price_value: Optional[float], currency: Optional[str] = None) -> str:
    """Format price in German style"""
    if price_value is None:
        return "/"
    
    # Format with German thousand separators and decimal comma
    formatted = f"{price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    # Add currency
    currency_symbol = currency or "EUR"
    if currency_symbol == "EUR":
        return f"{formatted} €"
    else:
        return f"{formatted} {currency_symbol}"


def safe_truncate(text: str, max_len: int = 60) -> str:
    """Safely truncate text with ellipsis"""
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + "..."